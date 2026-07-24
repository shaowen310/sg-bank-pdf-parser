"""Post-processing passes applied to a :class:`ParsedStatement`.

These steps fill in values that the source PDF does not print but which the IR
schema expects (and which downstream renderers read generically). By
materializing the value into the IR, the JSON becomes self-contained and
auditable, and the renderers can stay dumb (read ``t.balance_after`` directly).
"""

from __future__ import annotations

import re

from .account_type import AccountType
from .common import verify_fd_interest
from .ir_schema import ParsedStatement, Transaction

# Opening a new FD deposit (increases outstanding principal).
_FD_OPEN_RE = re.compile(r"new|rollover|placement|top[- ]?up|open", re.I)
# Closing / reducing an FD deposit (decreases outstanding principal).
# Matched as substrings and checked *before* OPEN so that "renew" is not
# captured by the "new" open-keyword.
_FD_CLOSE_RE = re.compile(
    r"renew|withdraw|premature|matur|mature|close|closure|redemption|redeem", re.I
)


def fill_fd_running_balances(statement: ParsedStatement) -> ParsedStatement:
    """Reconstruct ``balance_after`` for fixed-deposit accounts lacking it.

    FD movement tables don't carry a per-row balance, so we rebuild the running
    *outstanding-principal* balance from the deposit ledger (``fd_records``) plus
    the principal movements (``transactions``):

    * Opening outstanding principal = ``account.opening_balance`` when present,
      else the sum of principals of deposits placed *before* the first movement
      (i.e. still live at the start of the period).
    * Each movement then adds (placement / new / rollover) or removes
      (renewal / withdrawal / premature / maturity) principal. Interest-only
      rows leave the balance unchanged.

    Accounts that already carry a per-row ``balance_after`` (e.g. ICBC, which
    prints running balances in its FD table) are left untouched — this pass is
    idempotent and bank-agnostic.

    Mutates and returns *statement*.
    """
    for acct in statement.accounts:
        if acct.account_type != AccountType.FIXED_DEPOSIT:
            continue
        txns = acct.transactions or []
        if not txns:
            continue
        # Skip accounts whose balances were already extracted from the PDF.
        if any(t.balance_after is not None for t in txns):
            continue

        earliest = min((t.posted_date for t in txns if t.posted_date), default=None)
        if acct.opening_balance is not None:
            opening = float(acct.opening_balance)
        else:
            opening = 0.0
            for r in (acct.fd_records or []):
                vd = r.value_date
                if vd and earliest and vd < earliest:
                    opening += float(r.principal or 0.0)

        bal = opening
        for t in txns:
            amt = float(t.amount or 0.0)
            desc = t.description or ""
            if _FD_CLOSE_RE.search(desc):
                delta = -abs(amt)
            elif _FD_OPEN_RE.search(desc):
                delta = abs(amt)
            else:
                delta = 0.0
            bal += delta
            t.balance_after = bal

        statement.warnings.append(
            f"Inferred running balance for fixed-deposit account "+
            f"'{acct.name}' ({acct.account_no}): opening {opening:,.2f} "+
            f"across {len(txns)} transactions."
        )
    return statement


def link_fd_to_ca(statement: ParsedStatement) -> ParsedStatement:
    """Link fixed-deposit principal movements to their funding-account twin.

    In a consolidated statement a placement / rollover / withdrawal of a fixed
    deposit is usually printed twice — once in the FIXED_DEPOSIT account and once
    as a transaction in the funding current/savings account on the same day with
    the opposite sign and equal magnitude. This pass tags the funding-account twin
    for traceability and sets ``related_txn_ids`` (a list) bidirectionally so
    downstream consumers can collapse the double-count.

    Bank-agnostic: works for any extractor that emits FIXED_DEPOSIT accounts with
    transactions — e.g. ICBC and DBS. It links *every* FD-account transaction
    regardless of tag, because FD accounts only ever carry principal movements
    (interest is modelled separately, so it never appears as an FD transaction to
    match). No-op when there are no FD accounts, and idempotent on reload (skips
    txns that already carry a ``related_txn_ids`` entry for this group).

    A closure can carry interest. In the combined-row model the FD transaction
    carries an ``interest_amount`` and is tagged ``fd_interest``; the
    funding-account credit equals ``principal + interest``, so the match compares
    ``CA amount`` against the sum of ``|FD principal| + interest``. In the
    separate-leg model the principal and interest are emitted as two FD
    transactions (``fd_principal`` and ``fd_interest``); the CA credit equals
    their combined ``|amount|`` and the CA twin links to *both* legs. Either way,
    both atomic tags are copied onto the twin.

    Mutates and returns *statement*.
    """
    fd_accounts = [a for a in statement.accounts
                   if a.account_type == AccountType.FIXED_DEPOSIT.value]
    if not fd_accounts:
        return statement

    # Candidate funding transactions (exclude FD accounts themselves).
    funding_txns: list[Transaction] = []
    for acct in statement.accounts:
        if acct.account_type == AccountType.FIXED_DEPOSIT.value:
            continue
        funding_txns.extend(acct.transactions)

    for acct in fd_accounts:
        # Group this FD account's transactions by (deposit_no, posted_date).
        groups: dict[tuple[str, str], list[Transaction]] = {}
        for fd_txn in acct.transactions:
            if not fd_txn.posted_date:
                continue
            deposit_no = (fd_txn.extras or {}).get("fd_link", {}).get(
                "deposit_no", ""
            )
            groups.setdefault((deposit_no, fd_txn.posted_date), []).append(fd_txn)

        for (deposit_no, posted_date), fd_legs in groups.items():
            # Magnitude the funding-account twin must equal: sum of each leg's
            # principal amount plus any interest. On a combined row, interest is
            # separate from ``amount``; on a standalone interest leg it is folded
            # into ``interest_amount`` (with amount 0) — adding ``interest_amount``
            # keeps both cases correct.
            total_mag = sum(
                abs(t.amount) + (t.interest_amount or 0.0) for t in fd_legs
            )
            if total_mag <= 0:
                continue

            # PASS 1: split CA credits — each FD leg matches a *distinct* CA
            # credit on the same day whose |amount| equals that leg's magnitude
            # (principal, plus interest when carried on the leg). Used by DBS
            # maturities, which emit the principal and interest as two separate
            # CA credits.
            consumed: set[int] = set()
            leg_ca: dict[str, Transaction] = {}
            for fl in fd_legs:
                leg_mag = abs(fl.amount) + (fl.interest_amount or 0.0)
                if leg_mag <= 0:
                    continue
                for idx, ca_txn in enumerate(funding_txns):
                    if idx in consumed or ca_txn.posted_date != posted_date:
                        continue
                    if abs(ca_txn.amount - leg_mag) > 1e-6:
                        continue
                    consumed.add(idx)
                    leg_ca[fl.txn_id] = ca_txn
                    break

            if len(leg_ca) == len(fd_legs):
                for fl in fd_legs:
                    if fl.txn_id in leg_ca:
                        _link_fd_ca(
                            leg_ca[fl.txn_id], fl,
                            matched_on="CA amount == FD leg (principal + interest)",
                        )
                continue

            # PASS 2: combined CA credit — a single CA credit on the same day
            # equals the summed magnitude of all FD legs (ICBC, premature
            # withdrawals, closures with interest on one row).
            combined: Transaction | None = None
            for ca_txn in funding_txns:
                if ca_txn.posted_date != posted_date:
                    continue
                if abs(ca_txn.amount - total_mag) > 1e-6:
                    continue
                combined = ca_txn
                break
            if combined is not None:
                for fl in fd_legs:
                    _link_fd_ca(
                        combined, fl,
                        matched_on="CA amount == sum(FD legs: principal + interest)",
                    )
                continue

            # PASS 3: no matching CA twin at all. Link whatever split-matched
            # above, then treat the remaining legs as internal FD movements
            # (renewal / rollover) that never leave the account: they are NOT
            # transfers, so clear the (extractor-set) flag to suppress the false
            # "transfer without linked twin" warning.
            for fl in fd_legs:
                if fl.txn_id in leg_ca:
                    _link_fd_ca(
                        leg_ca[fl.txn_id], fl,
                        matched_on="CA amount == FD leg (principal + interest)",
                    )
                else:
                    fl.is_internal_transfer = False

    return statement


def _link_fd_ca(ca_txn: Transaction, fd_leg: Transaction,
                matched_on: str = "") -> None:
    """Record a bidirectional FD <-> CA transfer link on both transactions.

    Marks both the funding-account twin and the FD leg as ``is_internal_transfer`` and
    cross-links their ``related_txn_ids`` (deduped). FD atomic tags
    (``fd_principal`` / ``fd_interest``) are copied onto the twin, and the
    ``fd_link`` extras are mirrored for traceability.
    """
    ca_txn.category_hint = "fixed_deposit"
    ca_txn.is_internal_transfer = True
    fd_leg.is_internal_transfer = True
    if fd_leg.txn_id not in ca_txn.related_txn_ids:
        ca_txn.related_txn_ids.append(fd_leg.txn_id)
    if ca_txn.txn_id not in fd_leg.related_txn_ids:
        fd_leg.related_txn_ids.append(ca_txn.txn_id)
    # Copy FD tags (fd_principal / fd_interest) from the leg onto the twin, deduped.
    for tg in (fd_leg.tags or []):
        if tg not in ca_txn.tags:
            ca_txn.tags = list(ca_txn.tags) + [tg]
    # Mirror the fd_link extras onto the twin for traceability.
    fd_link = (fd_leg.extras or {}).get("fd_link", {})
    ca_txn.extras = {
        **(ca_txn.extras or {}),
        "fd_link": {
            "fd_account_no": fd_link.get("fd_account_no", ""),
            "deposit_no": fd_link.get("deposit_no", ""),
            "matched_on": matched_on,
        },
    }


def verify_fd_interest_consistency(statement: ParsedStatement) -> ParsedStatement:
    """Verify every fixed-deposit interest amount against principal × rate × tenor.

    Runs on both the fresh-extraction and IR-reload paths, so ``--ir-only``
    re-validates FD interest even when the builder was never re-run. Idempotent:
    a warning already present in ``statement.warnings`` (e.g. loaded from a saved
    ``.ir.json``) is not re-appended.

    Mutates and returns *statement*.
    """
    for acct in statement.accounts:
        if acct.account_type != AccountType.FIXED_DEPOSIT:
            continue
        for rec in (acct.fd_records or []):
            warn = verify_fd_interest(
                principal=rec.principal,
                interest_rate=rec.interest_rate,
                value_date=rec.value_date,
                maturity_date=rec.maturity_date,
                interest_amount=rec.interest_amount,
            )
            if warn and warn not in statement.warnings:
                statement.warnings.append(warn)
    return statement


def verify_fx_base_amount(statement: ParsedStatement) -> ParsedStatement:
    """Verify that an explicit ``base_amount`` matches ``amount × fx_rate``.

    When a transaction carries both ``base_amount`` and ``fx_rate``, the explicit
    base_amount wins but its consistency against the FX rate is verified. This
    runs on every pipeline path (the builder no longer emits this warning), so it
    is checked on fresh extraction and on IR reload. Idempotent: an already-present
    warning is not re-appended.

    Mutates and returns *statement*.
    """
    for acct in statement.accounts:
        for txn in (acct.transactions or []):
            base_amount = txn.base_amount
            fx_rate = txn.fx_rate
            if base_amount is None or fx_rate is None:
                continue
            expected = round(float(txn.amount or 0.0) * fx_rate, 2)
            if abs(base_amount - expected) > 0.01:
                warn = (
                    f"base_amount mismatch: {base_amount} != {txn.amount} × "
                    f"{fx_rate} = {expected} (diff={abs(base_amount - expected):.4f}), "
                    f"using explicit base_amount"
                )
                if warn not in statement.warnings:
                    statement.warnings.append(warn)
    return statement


def verify_transfer_links(statement: ParsedStatement) -> ParsedStatement:
    """Verify that every transfer transaction references its linked twins.

    A transaction flagged ``is_internal_transfer`` is one side of a matched pair/group
    (e.g. a fixed-deposit placement and its funding-account twin). Two checks
    apply:

    * A transfer must carry at least one id in ``related_txn_ids``; an empty
      list means the linker failed to find the other side, which would let the
      move be double-counted downstream.
    * The links must be symmetric: if A marks B as related, B must also mark A
      as related. A one-sided link indicates the linker only updated one side
      (e.g. a missed twin), again risking double-counting.

    Runs on every pipeline path (fresh extraction and IR reload) so a saved
    ``.ir.json`` whose links were lost is still flagged. Idempotent: an
    already-present warning is not re-appended.

    Mutates and returns *statement*.
    """
    # Index every transaction by id so we can resolve related_txn_ids.
    txn_by_id: dict[str, Transaction] = {}
    for acct in statement.accounts:
        for txn in (acct.transactions or []):
            if txn.txn_id:
                txn_by_id[txn.txn_id] = txn

    for acct in statement.accounts:
        for txn in (acct.transactions or []):
            if not txn.is_internal_transfer:
                continue
            if not txn.related_txn_ids:
                warn = (
                    f"transfer without linked twin: txn {txn.txn_id!r} "
                    f"(account {acct.account_no}, {txn.posted_date}, amount "
                    f"{txn.amount}) is_internal_transfer=true but related_txn_ids is empty"
                )
                if warn not in statement.warnings:
                    statement.warnings.append(warn)
                continue
            # Symmetric-link check: every twin that A names must name A back.
            for twin_id in txn.related_txn_ids:
                twin = txn_by_id.get(twin_id)
                if twin is None:
                    continue
                if txn.txn_id in twin.related_txn_ids:
                    continue
                warn = (
                    f"transfer link not reciprocal: txn {txn.txn_id!r} "
                    f"(account {acct.account_no}) lists {twin_id!r} as related "
                    f"but {twin_id!r} does not list it back"
                )
                if warn not in statement.warnings:
                    statement.warnings.append(warn)
    return statement

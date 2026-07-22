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
    for traceability and sets ``related_txn_id`` bidirectionally so downstream
    consumers can collapse the double-count.

    Bank-agnostic: works for any extractor that emits FIXED_DEPOSIT accounts with
    transactions — e.g. ICBC and DBS. It links *every* FD-account transaction
    regardless of tag, because FD accounts only ever carry principal movements
    (interest is modelled separately, so it never appears as an FD transaction to
    match). No-op when there are no FD accounts, and idempotent on reload (skips
    txns that already carry a ``related_txn_id``).

    A closure can carry interest: the FD transaction then also carries an
    ``interest_amount`` and is tagged ``fd_interest``. The funding-account credit
    equals ``principal + interest``, so the match compares
    ``CA amount + FD principal - interest`` to zero. Both atomic tags
    (``fd_principal`` and, when present, ``fd_interest``) are copied onto the twin.

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

    # --- Principal (+interest) leg: bidirectional related_txn_id ---
    for acct in fd_accounts:
        for fd_txn in acct.transactions:
            interest = fd_txn.interest_amount or 0.0
            for ca_txn in funding_txns:
                if ca_txn.related_txn_id:
                    continue
                if ca_txn.posted_date != fd_txn.posted_date:
                    continue
                # CA credit equals the FD principal (net of any interest leg).
                if abs(ca_txn.amount + fd_txn.amount - interest) > 1e-6:
                    continue
                fd_txn.related_txn_id = ca_txn.txn_id
                ca_txn.related_txn_id = fd_txn.txn_id
                ca_txn.category_hint = "fixed_deposit"
                ca_txn.is_transfer = True
                # Copy FD tags onto the twin, deduped (both fd_principal and,
                # when present, fd_interest).
                for tg in (fd_txn.tags or []):
                    if tg not in ca_txn.tags:
                        ca_txn.tags = list(ca_txn.tags) + [tg]
                ca_txn.extras = {
                    **(ca_txn.extras or {}),
                    "fd_link": {
                        "fd_account_no": acct.account_no,
                        "deposit_no": (fd_txn.extras or {})
                        .get("fd_link", {}).get("deposit_no", ""),
                        "matched_on": "principal (+interest) == -CA amount",
                    },
                }
                break

    return statement


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

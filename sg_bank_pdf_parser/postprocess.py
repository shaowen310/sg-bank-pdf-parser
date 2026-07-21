"""Post-processing passes applied to a :class:`ParsedStatement`.

These steps fill in values that the source PDF does not print but which the IR
schema expects (and which downstream renderers read generically). By
materializing the value into the IR, the JSON becomes self-contained and
auditable, and the renderers can stay dumb (read ``t.balance_after`` directly).
"""

from __future__ import annotations

import re

from .account_type import AccountType
from .ir_schema import ParsedStatement

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
            f"across {len(txns)} movements."
        )
    return statement

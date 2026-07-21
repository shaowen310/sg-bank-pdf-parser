"""ICBC bilingual statement → IR extractor.

Wraps ``icbc_parser.parse_icbc()``.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, ClassVar, override

from ..account_type import AccountType
from .base import BaseExtractor
from ..ir_schema import ParsedStatement, Transaction
from ..parsers.icbc_parser import CATxnRow, FDTxnRow


class ICBCExtractor(BaseExtractor):
    parser_name: ClassVar[str] = "icbc_sg"
    parser_version: ClassVar[str] = "1.0"

    @classmethod
    @override
    def supports(cls, pdf_path: Path) -> bool:
        return True

    @classmethod
    @override
    def bank_name(cls) -> str:
        return "ICBC"

    @override
    def to_ir(self, pdf_path: Path) -> ParsedStatement:
        from ..parsers.icbc_parser import parse_icbc

        pdf = self._open_pdf(pdf_path)
        try:
            meta, ca_summary, fd_summary, ca_txns, fd_txns, reminders, notes = \
                parse_icbc(pdf)
        finally:
            pdf.close()

        builder = self._create_builder()
        _ = builder.set_source(str(pdf_path))

        # Use the first CA summary row for the base currency.
        first_ca: dict[str, Any] = dict(ca_summary[0]) if ca_summary else {}
        _ = builder.set_meta(
            institution="ICBC",
            currency=str(first_ca.get("ccy", "SGD")),
        )

        sd = str(meta.get("statement_date", ""))
        if sd:
            _ = builder.set_period(sd, sd)

        # ---- Populate reminders and notes (in extras for ICBC) ----
        extras: dict[str, object] = {}
        if reminders:
            extras["reminders"] = reminders
        if notes:
            extras["notes"] = notes
        if extras:
            _ = builder.set_extras(extras)

        # ---- Current Account(s): group CA txns by (account_no, ccy) ----
        # ICBC repeats the same account number across currencies, so key on the
        # currency too to avoid replicating every currency's transactions.
        ca_by_key: "OrderedDict[tuple[str, str], list[CATxnRow]]" = OrderedDict()
        for t in ca_txns:
            if t.get("is_bf") or t.get("is_total"):
                continue  # skip B/F and Total rows
            key = (str(t.get("acct_no", "")), str(t.get("ccy", "SGD")))
            ca_by_key.setdefault(key, []).append(t)

        for row in ca_summary:
            acct_no = str(row.get("acct_no", ""))
            ccy = str(row.get("ccy", "SGD"))
            txns = ca_by_key.get((acct_no, ccy), [])
            _ = builder.add_account(
                name=str(row.get("acct_type", "Current Account")),
                account_no=acct_no,
                account_type=AccountType.CURRENT.value,
                currency=ccy,
                balance=_icbc_parse_balance(row.get("balance")),
            )
            for t in txns:
                withdrawal = float(str(t.get("withdrawal", "0") or "0").replace(",", ""))
                deposit = float(str(t.get("deposit", "0") or "0").replace(",", ""))
                amount = deposit - withdrawal
                ccy = str(t.get("ccy", "SGD"))

                balance_str = str(t.get("balance", "") or "").replace(",", "")
                balance = float(balance_str) if balance_str else None

                _ = builder.add_transaction(
                    posted_date=_icbc_date_to_iso(str(t.get("date", ""))),
                    amount=amount,
                    currency=ccy,
                    description=str(t.get("remark", "")),
                    raw_description=str(t.get("remark", "")),
                    is_accrual=False,
                    balance_after=balance,
                )

        # ---- Fixed Deposit(s): group FD txns by account number ----
        fd_by_no: "OrderedDict[str, list[FDTxnRow]]" = OrderedDict()
        for t in fd_txns:
            fd_by_no.setdefault(str(t.get("acct_no", "")), []).append(t)

        for row in fd_summary:
            acct_no = str(row.get("acct_no", ""))
            txns = fd_by_no.get(acct_no, [])
            _ = builder.add_account(
                name="Fixed Deposit",
                account_no=acct_no,
                account_type=AccountType.FIXED_DEPOSIT.value,
                currency=str(row.get("ccy", "SGD")),
                balance=_icbc_parse_balance(row.get("balance")),
            )
            for t in txns:
                # The FD table's "+Deposit / -Withdrawal" column carries the
                # sign already (full-width minus － for withdrawals); preserve it.
                amt_str = (str(t.get("amount", "") or "")
                           .replace(",", "")
                           .replace("\uFF0D", "-")
                           .replace("\u2212", "-"))
                signed_amount = float(amt_str) if amt_str else 0.0
                principal = abs(signed_amount)
                ccy = str(t.get("ccy", "SGD"))

                _ = builder.add_fd_record(
                    deposit_no=str(t.get("seq_no", "")),
                    value_date=_icbc_date_to_iso(str(t.get("value_date", ""))) or None,
                    maturity_date=_icbc_date_to_iso(str(t.get("maturity_date", ""))) or None,
                    interest_rate=str(t.get("rate", "")),
                    interest_amount=_icbc_parse_balance(t.get("interest_amount")),
                    principal=principal,
                    currency=ccy,
                    description=str(t.get("remark", "") or f"FD {t.get('seq_no', '')}"),
                )

                # Build the FD *transaction* purely from the FD table (principal
                # leg only). Interest is an investment gain, not a balance change
                # of the FD account, so it is intentionally NOT mirrored here.
                # balance_after is read from the FD table's Balance column.
                _ = builder.add_transaction(
                    posted_date=_icbc_date_to_iso(
                        str(t.get("deal_date", "") or str(t.get("value_date", "")) or "")
                    ),
                    amount=signed_amount,
                    currency=ccy,
                    description=str(t.get("remark", "") or "CLO"),
                    is_transfer=True,
                    category_hint="fixed_deposit",
                    tags=["fd_principal"],
                    balance_after=_icbc_parse_balance(t.get("balance")),
                    extras={"fd_link": {
                        "fd_account_no": acct_no,
                        "deposit_no": str(t.get("seq_no", "")),
                    }},
                )

        statement = builder.build()
        _link_icbc_fd_to_ca(statement)
        return statement


def _link_icbc_fd_to_ca(statement: ParsedStatement) -> None:
    """Link the FD-table-built principal transaction to its Current Account twin.

    - **Principal leg (bidirectional):** for each FD-account ``fd_principal``
      transaction, find the CA transaction on the same date whose amount sums to
      zero with it (opposite sign, equal magnitude) and set ``related_txn_id`` on
      both. The CA twin is tagged ``fd_principal`` for traceability.
    - **Interest leg:** intentionally NOT matched against the FD table's interest
      amount, because a premature withdrawal changes the actual interest credited
      (penalty / reduced rate), making the scalar match unreliable. Interest
      reconciliation is left for manual review.
    """
    fd_accounts = [a for a in statement.accounts
                   if a.account_type == AccountType.FIXED_DEPOSIT.value]
    if not fd_accounts:
        return

    # Candidate CA transactions (exclude FD accounts themselves).
    ca_txns: list[Transaction] = []
    for acct in statement.accounts:
        if acct.account_type == AccountType.FIXED_DEPOSIT.value:
            continue
        ca_txns.extend(acct.transactions)

    # --- Principal leg: bidirectional related_txn_id ---
    for acct in fd_accounts:
        for fd_txn in acct.transactions:
            if "fd_principal" not in (fd_txn.tags or []):
                continue
            for ca_txn in ca_txns:
                if ca_txn.related_txn_id:
                    continue
                if ca_txn.posted_date != fd_txn.posted_date:
                    continue
                if abs(ca_txn.amount + fd_txn.amount) > 1e-6:
                    continue
                fd_txn.related_txn_id = ca_txn.txn_id
                ca_txn.related_txn_id = fd_txn.txn_id
                ca_txn.category_hint = "fixed_deposit"
                ca_txn.is_transfer = True
                ca_txn.tags = list(ca_txn.tags) + ["fd_principal"]
                ca_txn.extras = {
                    **(ca_txn.extras or {}),
                    "fd_link": {
                        "fd_account_no": (fd_txn.extras or {}).get("fd_link", {}).get("fd_account_no", ""),
                        "deposit_no": (fd_txn.extras or {}).get("fd_link", {}).get("deposit_no", ""),
                    },
                }
                break


def _icbc_date_to_iso(date_str: str) -> str:
    """Convert ICBC YYYY/MM/DD → ISO YYYY-MM-DD."""
    parts = date_str.split("/")
    if len(parts) == 3:
        return f"{parts[0]}-{parts[1]}-{parts[2]}"
    return date_str


def _icbc_parse_balance(val: object) -> float | None:
    """Parse ICBC balance/amount value to float.

    Handles comma grouping and ICBC's full-width minus (U+FF0D) / minus sign
    (U+2212) which appear on withdrawal rows.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).replace(",", "").strip()
        s = s.replace("\uFF0D", "-").replace("\u2212", "-")
        return float(s) if s else None
    except (ValueError, TypeError):
        return None

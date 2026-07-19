"""OCBC consolidated statement & credit card → IR extractors.

Wraps ``ocbc_parser.parse_consolidated()`` and ``ocbc_parser.parse_card()``.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast, override

from ..account_type import AccountType
from .base import BaseExtractor
from ..ir_schema import ParsedStatement

if TYPE_CHECKING:
    from ..parsers.ocbc_parser import BankTxn, TimeDeposit


def _ocbc_account_type(name: str) -> str:
    """Map an OCBC deposit product name to its canonical ``account_type``.

    Only recognized OCBC product names are classified; anything unrecognized
    falls back to ``unknown`` so we never blindly assign a type.

    - "STATEMENT SAVINGS" / "360 ACCOUNT" (and other savings/current deposit
      products) -> ``current``
    - "TIME DEPOSITS" / fixed deposits -> ``fixed_deposit``
    - anything else -> ``unknown``
    """
    lowered = (name or "").lower()
    if "time deposit" in lowered or "fixed" in lowered:
        return AccountType.FIXED_DEPOSIT.value
    if "savings" in lowered or "360" in lowered or "current" in lowered:
        return AccountType.CURRENT.value
    return AccountType.UNKNOWN.value


class OCBCConsolidatedExtractor(BaseExtractor):
    parser_name: ClassVar[str] = "ocbc_consolidated"
    parser_version: ClassVar[str] = "1.0"

    @classmethod
    @override
    def supports(cls, pdf_path: Path) -> bool:
        return True

    @classmethod
    @override
    def bank_name(cls) -> str:
        return "OCBC"

    @override
    def to_ir(self, pdf_path: Path) -> ParsedStatement:
        from ..common import PDF
        from ..parsers.ocbc_parser import parse_consolidated

        pdf = self._open_pdf(pdf_path)
        try:
            meta, transactions, time_deposit_rows = parse_consolidated(cast(PDF, cast(object, pdf)))
        finally:
            pdf.close()

        builder = self._create_builder()
        _ = builder.set_source(str(pdf_path))

        base_ccy = str(meta.get("currency", "SGD"))
        # OCBC bank statement may be consolidated (multi-section); account type
        # is classified per account via _ocbc_account_type().
        _ = builder.set_meta(
            institution="OCBC",
            currency=base_ccy,
        )

        sd = str(meta.get("statement_date", ""))
        if sd:
            # OCBC date: "04 JUN 2026"
            _ = builder.set_period(sd, sd)  # single date; no range exposed

        # Group transactions into per-account buckets (OCBC bank statements can
        # list multiple accounts in one statement).
        accounts_map: OrderedDict[tuple[str, str], list[BankTxn]] = OrderedDict()
        for t in transactions:
            key = (str(t.get("account_no", "")), str(t.get("account", "")))
            accounts_map.setdefault(key, []).append(t)

        for (acct_no, acct_name), txns in accounts_map.items():
            _ = builder.add_account(
                name=acct_name,
                account_no=acct_no,
                account_type=_ocbc_account_type(acct_name),
                currency=base_ccy,
            )
            for t in txns:
                withdrawal = float(str(t.get("withdrawal", "0") or "0").replace(",", ""))
                deposit = float(str(t.get("deposit", "0") or "0").replace(",", ""))
                amount = deposit - withdrawal

                balance_str = str(t.get("balance", "") or "").replace(",", "")
                balance = float(balance_str) if balance_str else None

                _ = builder.add_transaction(
                    posted_date=str(t.get("txn_date", "")),
                    amount=amount,
                    currency=base_ccy,
                    description=str(t.get("description", "")),
                    raw_description=str(t.get("description", "")),
                    value_date=str(t.get("value_date", "")) or None,
                    is_accrual=False,
                    balance_after=balance,
                )

        # ---- Fixed Deposits (previously stored in extras['time_deposits']) ----
        # Model them as dedicated fixed_deposit accounts with FD records (not
        # transactions, and not in the statement-level extras).
        if time_deposit_rows:
            fd_by_no: OrderedDict[str, list[TimeDeposit]] = OrderedDict()
            for td in time_deposit_rows:
                fd_by_no.setdefault(str(td.get("account_no", "")), []).append(td)

            for acct_no, tds in fd_by_no.items():
                _ = builder.add_account(
                    name="TIME DEPOSITS",
                    account_no=acct_no,
                    account_type=AccountType.FIXED_DEPOSIT.value,
                    currency=base_ccy,
                )
                for td in tds:
                    bal = float(str(td.get("balance", "0")).replace(",", ""))
                    _ = builder.add_fd_record(
                        deposit_no=str(td.get("deposit_no", "")),
                        value_date=None,  # OCBC statements never print a value date
                        maturity_date=_ocbc_maturity_to_iso(str(td.get("maturity", ""))),
                        interest_rate=str(td.get("rate", "")),
                        interest_amount=None,
                        principal=bal,
                        currency=base_ccy,
                        description=f"FD {td.get('deposit_no', '')}",
                    )

        return builder.build()


class OCRCCardExtractor(BaseExtractor):
    parser_name: ClassVar[str] = "ocbc_card"
    parser_version: ClassVar[str] = "1.0"

    @classmethod
    @override
    def supports(cls, pdf_path: Path) -> bool:
        return True

    @classmethod
    @override
    def bank_name(cls) -> str:
        return "OCBC"

    @override
    def to_ir(self, pdf_path: Path) -> ParsedStatement:
        from ..common import PDF
        from ..parsers.ocbc_parser import parse_card

        pdf = self._open_pdf(pdf_path)
        try:
            meta, transactions = parse_card(cast(PDF, cast(object, pdf)))
        finally:
            pdf.close()

        builder = self._create_builder()
        _ = builder.set_source(str(pdf_path))

        _ = builder.set_meta(
            institution="OCBC",
            account_holder=str(meta.get("card_name", "")),
            currency="SGD",
        )

        _ = builder.add_account(
            name=str(meta.get("card_name", "")) or "Credit Card",
            account_no=str(meta.get("card_no", "")),
            account_type=AccountType.CREDIT_CARD.value,
            currency="SGD",
        )

        sd = str(meta.get("statement_date", ""))
        if sd:
            # OCBC card date: "19-05-2026"
            parts = sd.split("-")
            if len(parts) == 3:
                sd_iso = f"{parts[2]}-{parts[1]}-{parts[0]}"
                _ = builder.set_period(sd_iso, sd_iso)

        for t in transactions:
            amount_signed = t.get("amount_signed")
            if amount_signed is None:
                continue
            sgd_amount = float(amount_signed)

            # Credit card: charges are accrual (obligation exists but actual
            # settlement is later); payments are actual.
            is_accrual = sgd_amount > 0

            # Separate foreign-currency amount from SGD base amount.
            fc_str = str(t.get("foreign_currency", "")).strip()
            currency = "SGD"
            amount = sgd_amount
            if fc_str:
                parts = fc_str.split(" ", 1)
                if len(parts) == 2 and len(parts[0]) == 3 and parts[0].isalpha() and parts[0].isupper():
                    fc_amount = float(parts[1].replace(",", ""))
                    if fc_amount != 0:
                        currency = parts[0]
                        amount = fc_amount if sgd_amount > 0 else -fc_amount

            _ = builder.add_transaction(
                posted_date=str(t.get("date_iso", t.get("date", ""))),
                amount=amount,
                currency=currency,
                base_amount=sgd_amount if currency != "SGD" else None,
                description=str(t.get("description", "")),
                raw_description=str(t.get("description", "")),
                is_accrual=is_accrual,
            )

        # ---- Populate credit card summary (bank-agnostic fields) ----
        payment_due = meta.get("payment_due_date", "")
        payment_due_iso = ""
        if payment_due:
            parts = payment_due.split("-")
            if len(parts) == 3:
                payment_due_iso = f"{parts[2]}-{parts[1]}-{parts[0]}"

        _ = builder.set_credit_card_summary(
            payment_due_date=payment_due_iso or None,
            credit_limit=meta.get("total_credit_limit", "") or None,
            available_credit=meta.get("total_available_credit_limit", "") or None,
            minimum_due=meta.get("total_minimum_due", "") or None,
            previous_balance=meta.get("last_month_balance", "") or None,
            total_amount_due=meta.get("total_amount_due", "") or None,
        )

        # ---- Bank-specific extras (OCBC intermediate calculation rows) ----
        extras = {}
        for key in ("subtotal", "total"):
            val = meta.get(key, "")
            if val:
                extras[key] = val
        if extras:
            _ = builder.set_extras(extras)

        return builder.build()


def _ocbc_maturity_to_iso(maturity: str) -> str | None:
    """OCBC time-deposit maturity is 'DD MMM YYYY' → ISO YYYY-MM-DD."""
    s = maturity.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d %b %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

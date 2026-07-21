"""IRBuilder — chainable API for constructing a ParsedStatement.

Usage inside an Extractor::

    builder = IRBuilder("dbs_sg", "1.0")
    builder.set_source(str(pdf_path))
    builder.set_meta(
        institution="DBS",
        account_holder="JOHN DOE",
        currency="SGD",
    )
    builder.set_period("2026-06-01", "2026-06-30")

    builder.add_account(
        name="DBS Savings Plus",
        account_no="XXX-XXX-XXX-X",
        account_type="current",
        currency="SGD",
        opening_balance=12345.67,
        closing_balance=12600.00,
    )
    for row in raw_transactions:
        builder.add_transaction(
            posted_date=row["date"],
            amount=-50.00,
            currency="SGD",
            description=row["desc"],
            raw_description=row["raw"],
            is_accrual=False,
            balance_after=12600.00,
        )

    statement = builder.build()
    json_str = builder.to_json()
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .account_type import AccountType
from .common import parse_fd_rate, verify_fd_interest
from .ir_schema import (
    Account,
    CreditCardSummary,
    DebugInfo,
    FixedDepositRecord,
    InvestmentHolding,
    ParsedStatement,
    ParserInfo,
    SourceAccount,
    StatementMeta,
    Transaction,
    generate_txn_id,
    to_json as ir_to_json,
)


def _fd_records_complementary(existing: "FixedDepositRecord", new_principal: float,
                              new_interest_amount: float | None) -> bool:
    """Return True if ``new`` carries the field ``existing`` is missing.

    This prevents two genuinely distinct deposits that merely share the same
    deposit_no/value_date/maturity_date from being collapsed into one, while
    still merging a principal line with its matching interest line.
    """
    existing_principal = bool(existing.principal)
    existing_interest = bool(existing.interest_amount)
    new_principal_b = bool(new_principal)
    new_interest_b = bool(new_interest_amount)
    if existing_principal and new_interest_b and not existing_interest:
        return True
    if existing_interest and new_principal_b and not existing_principal:
        return True
    return False


class IRBuilder:
    """Builds a ``ParsedStatement`` incrementally through chainable methods."""

    def __init__(self, parser_name: str, parser_version: str) -> None:
        self._parser: ParserInfo = ParserInfo(name=parser_name, version=parser_version)
        self._meta: StatementMeta = StatementMeta()
        self._source_file: str = ""
        self._accounts: list[Account] = []
        self._active_account: Account | None = None
        self._warnings: list[str] = []
        # FD interest-verification warnings, keyed by (deposit_no, value_date,
        # maturity_date) so a record that is merged across multiple add_fd_record
        # calls is never double-reported.
        self._fd_warnings: dict[tuple[str, str | None, str | None], str | None] = {}
        self._period_from: str | None = None
        self._period_to: str | None = None
        self._base_currency: str = ""

        # Phase 3 extension accumulators
        self._investment_holdings: list[InvestmentHolding] = []
        self._reconciliation: dict[str, Any] | None = None
        self._extras: dict[str, Any] | None = None
        self._credit_card_summary: CreditCardSummary | None = None

    # -- Chainable setters ---------------------------------------------------

    def set_source(self, path: str) -> "IRBuilder":
        """Record the source PDF filename (directory stripped)."""
        self._source_file = os.path.basename(path)
        return self

    def set_meta(
        self,
        *,
        institution: str = "",
        institution_code: str | None = None,
        account_holder: str | None = None,
        currency: str = "",
    ) -> "IRBuilder":
        """Set statement-level metadata (institution, base currency, holder)."""
        self._meta.institution = institution
        self._meta.institution_code = institution_code
        self._meta.account_holder = account_holder
        self._meta.currency = currency
        self._base_currency = currency
        return self

    def add_account(
        self,
        *,
        name: str = "",
        account_no: str = "",
        account_type: str = "unknown",
        currency: str = "",
        account_holder: str | None = None,
        opening_balance: float | None = None,
        closing_balance: float | None = None,
        balance: float | None = None,
        balance_sgd: float | None = None,
        extras: dict[str, Any] | None = None,
        investment_holdings: list[InvestmentHolding] | None = None,
    ) -> "IRBuilder":
        """Append an ``Account`` and make it the active account for transactions.

        Subsequent ``add_transaction`` / ``add_transaction_dict`` calls route
        into this account until the next ``add_account`` call.

        ``investment_holdings`` attaches a per-account list of
        ``InvestmentHolding`` (e.g. SRS unit trusts, or the UOB portfolio's
        UNIT_TRUST fund holdings). An account owns its holdings rather than the
        statement — the top-level ``statement.investment_holdings`` list is no
        longer populated by the UOB portfolio extractor.
        """
        acct = Account(
            name=name,
            account_no=account_no,
            account_type=AccountType.normalize(account_type).value,
            currency=currency,
            account_holder=account_holder,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            balance=balance,
            balance_sgd=balance_sgd,
            extras=extras or None,
            investment_holdings=investment_holdings or None,
        )
        self._accounts.append(acct)
        self._active_account = acct
        if currency:
            self._base_currency = currency
        return self

    def set_period(self, period_from: str, period_to: str) -> "IRBuilder":
        """Set statement date range (ISO 8601 YYYY-MM-DD)."""
        self._period_from = period_from
        self._period_to = period_to
        self._meta.period_from = period_from
        self._meta.period_to = period_to
        return self

    def add_warning(self, message: str) -> "IRBuilder":
        """Append a parse warning."""
        self._warnings.append(message)
        return self

    # -- Phase 3 extension methods --------------------------------------------

    def add_investment_holding(
        self,
        *,
        name: str = "",
        units: str = "",
        currency: str = "",
        unit_price: str = "",
        valuation: str = "",
    ) -> "IRBuilder":
        """Append an investment/holding row."""
        self._investment_holdings.append(InvestmentHolding(
            name=name,
            units=units,
            currency=currency,
            unit_price=unit_price,
            valuation=valuation,
        ))
        return self

    def set_reconciliation(self, data: dict[str, Any]) -> "IRBuilder":
        """Set the reconciliation data dict."""
        self._reconciliation = dict(data)
        return self

    def set_extras(self, data: dict[str, Any]) -> "IRBuilder":
        """Set bank-specific supplementary data dict."""
        self._extras = dict(data)
        return self

    def set_credit_card_summary(
        self,
        *,
        payment_due_date: str | None = None,
        credit_limit: str | None = None,
        available_credit: str | None = None,
        minimum_due: str | None = None,
        previous_balance: str | None = None,
        total_amount_due: str | None = None,
    ) -> "IRBuilder":
        """Set credit card statement summary fields (bank-agnostic)."""
        self._credit_card_summary = CreditCardSummary(
            payment_due_date=payment_due_date,
            credit_limit=credit_limit,
            available_credit=available_credit,
            minimum_due=minimum_due,
            previous_balance=previous_balance,
            total_amount_due=total_amount_due,
        )
        return self

    # -- Transaction builder -------------------------------------------------

    def add_transaction(
        self,
        *,
        posted_date: str,
        amount: float,
        currency: str = "",
        base_amount: float | None = None,
        description: str = "",
        raw_description: str = "",
        value_date: str | None = None,
        fx_rate: float | None = None,
        counterparty: str | None = None,
        counterparty_account: str | None = None,
        category_hint: str | None = None,
        tags: list[str] | None = None,
        is_accrual: bool = False,
        is_reversal: bool = False,
        is_transfer: bool = False,
        related_txn_id: str | None = None,
        source_account: SourceAccount | None = None,
        balance_after: float | None = None,
        extras: dict[str, Any] | None = None,
        _debug: DebugInfo | None = None,
    ) -> "IRBuilder":
        """Append one transaction to the active account.

        ``base_amount`` and ``base_currency`` are auto-populated:
          - If *both* ``base_amount`` and ``fx_rate`` given → validate with warning.
          - If *only* ``base_amount`` given → use it directly (no fx_rate needed).
          - If *only* ``fx_rate`` given → ``base_amount = round(amount * fx_rate, 2)``.
          - If *neither* given → ``base_amount = amount`` (single-currency transaction).

        ``source_account`` is optional and no longer auto-derived from statement
        metadata (transactions are now nested inside an ``Account``).
        """
        if self._active_account is None:
            # Guard: ensure there is always an account to hold the transaction.
            _ = self.add_account(name="Account")

        base_currency = self._base_currency or currency

        if base_amount is not None and fx_rate is not None:
            # Both provided: validate consistency, trust base_amount
            expected = round(amount * fx_rate, 2)
            if abs(base_amount - expected) > 0.01:
                self._warnings.append(
                    f"base_amount mismatch: {base_amount} != {amount} × {fx_rate} = {expected} "
                    + f"(diff={abs(base_amount - expected):.4f}), using explicit base_amount"
                )
        elif base_amount is not None:
            # Only base_amount provided: use as-is (no fx_rate needed)
            pass
        elif fx_rate is not None:
            base_amount = round(amount * fx_rate, 2)
        else:
            base_amount = amount

        txn = Transaction(
            txn_id=generate_txn_id(posted_date, amount, currency, description, counterparty),
            posted_date=posted_date,
            value_date=value_date,
            amount=amount,
            currency=currency or self._base_currency,
            fx_rate=fx_rate,
            base_amount=base_amount,
            base_currency=base_currency,
            description=description,
            raw_description=raw_description,
            counterparty=counterparty,
            counterparty_account=counterparty_account,
            category_hint=category_hint,
            tags=tags or [],
            is_accrual=is_accrual,
            is_reversal=is_reversal,
            is_transfer=is_transfer,
            related_txn_id=related_txn_id,
            source_account=source_account,
            balance_after=balance_after,
            extras=extras,
            _debug=_debug,
        )
        assert self._active_account is not None
        self._active_account.transactions.append(txn)
        return self

    def add_fd_record(
        self,
        *,
        deposit_no: str = "",
        value_date: str | None = None,
        maturity_date: str | None = None,
        interest_rate: str | None = None,
        interest_amount: float | None = None,
        principal: float = 0.0,
        currency: str = "",
        assume_pct_rate: bool = False,
    ) -> "IRBuilder":
        """Append one fixed-deposit record to the active account.

        Fixed deposits are NOT transactions. They are stored on
        ``Account.fd_records`` rather than ``Account.transactions``.
        """
        if self._active_account is None:
            # Guard: ensure there is always an account to hold the record.
            _ = self.add_account(name="Fixed Deposit")
        acct = self._active_account
        assert acct is not None
        if acct.fd_records is None:
            acct.fd_records = []
        rate_dec = parse_fd_rate(interest_rate, assume_pct=assume_pct_rate)
        key = (deposit_no, value_date, maturity_date)
        existing = None
        for rec in acct.fd_records:
            if rec.deposit_no == deposit_no and rec.value_date == value_date \
                    and rec.maturity_date == maturity_date:
                existing = rec
                break

        if existing is not None and _fd_records_complementary(existing, principal, interest_amount):
            # Merge the complementary FD row into the existing record instead of
            # appending a duplicate (one row carries principal, the other interest).
            if not existing.principal:
                existing.principal = principal or 0.0
            if existing.interest_amount is None or existing.interest_amount == 0:
                existing.interest_amount = interest_amount
            if not existing.raw_interest_rate:
                existing.raw_interest_rate = interest_rate or ""
            if existing.interest_rate is None and rate_dec is not None:
                existing.interest_rate = rate_dec
            if not existing.currency:
                existing.currency = currency or self._base_currency
            merged = existing
        else:
            merged = FixedDepositRecord(
                deposit_no=deposit_no,
                value_date=value_date,
                maturity_date=maturity_date,
                interest_rate=rate_dec,
                raw_interest_rate=interest_rate,
                interest_amount=interest_amount,
                principal=principal,
                currency=currency or self._base_currency,
            )
            acct.fd_records.append(merged)

        warn = verify_fd_interest(
            principal=merged.principal,
            interest_rate=merged.interest_rate,
            value_date=merged.value_date,
            maturity_date=merged.maturity_date,
            interest_amount=merged.interest_amount,
        )
        self._fd_warnings[key] = warn
        return self

    def add_transaction_dict(self, row: dict[str, Any]) -> "IRBuilder":
        """Convenience: add a transaction from a raw parser dict.

        Keys recognised (all optional, missing keys get defaults):
        ``posted_date``, ``amount``, ``currency``, ``base_amount``,
        ``description``, ``raw_description``, ``value_date``, ``counterparty``,
        ``counterparty_account``, ``balance_after``, ``fx_rate``, ``is_accrual``,
        ``extras``.
        """
        return self.add_transaction(
            posted_date=row.get("posted_date", ""),
            amount=row.get("amount", 0),
            currency=row.get("currency", ""),
            base_amount=row.get("base_amount"),
            description=row.get("description", ""),
            raw_description=row.get("raw_description", ""),
            value_date=row.get("value_date"),
            fx_rate=row.get("fx_rate"),
            counterparty=row.get("counterparty"),
            counterparty_account=row.get("counterparty_account"),
            category_hint=row.get("category_hint"),
            tags=row.get("tags"),
            is_accrual=row.get("is_accrual", False),
            is_reversal=row.get("is_reversal", False),
            is_transfer=row.get("is_transfer", False),
            related_txn_id=row.get("related_txn_id"),
            source_account=row.get("source_account"),
            balance_after=row.get("balance_after"),
            extras=row.get("extras"),
        )

    # -- Build & serialise ---------------------------------------------------

    def build(self) -> ParsedStatement:
        """Assemble and return the final ``ParsedStatement``."""
        return ParsedStatement(
            ir_version="2026.3",
            parsed_at=datetime.now(timezone.utc).isoformat(),
            parser=self._parser,
            source_file=self._source_file,
            statement_meta=self._meta,
            accounts=list(self._accounts),
            warnings=list(self._warnings)
            + [w for w in self._fd_warnings.values() if w],
            investment_holdings=list(self._investment_holdings) if self._investment_holdings else None,
            reconciliation=self._reconciliation,
            extras=self._extras,
            credit_card_summary=self._credit_card_summary,
        )

    def to_json(self, *, indent: int = 2) -> str:
        """Build and serialise to a JSON string in one step."""
        return ir_to_json(self.build(), indent=indent)

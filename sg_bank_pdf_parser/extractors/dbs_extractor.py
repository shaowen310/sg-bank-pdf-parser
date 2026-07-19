"""DBS/POSB Consolidated Statement → IR extractor.

Wraps ``dbs_parser.parse_dbs()`` and maps its output to a ``ParsedStatement``.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, override

from ..account_type import AccountType
from .base import BaseExtractor
from ..ir_schema import InvestmentHolding, ParsedStatement


class DBSExtractor(BaseExtractor):
    parser_name: ClassVar[str] = "dbs_sg"
    parser_version: ClassVar[str] = "1.0"

    @classmethod
    @override
    def supports(cls, pdf_path: Path) -> bool:
        return True  # caller already matched via detect_type()

    @classmethod
    @override
    def bank_name(cls) -> str:
        return "DBS/POSB"

    @override
    def to_ir(self, pdf_path: Path) -> ParsedStatement:
        from ..parsers.dbs_parser import parse_dbs

        pdf = self._open_pdf(pdf_path)
        try:
            meta, summary, accounts, srs_data = parse_dbs(pdf)
        finally:
            pdf.close()

        builder = self._create_builder()
        _ = builder.set_source(str(pdf_path))

        base_ccy = str(meta.get("currency", "SGD"))

        _ = builder.set_meta(
            institution="DBS/POSB",
            account_holder=str(meta.get("account_holder", "")),
            currency=base_ccy,
        )

        # Period is derived from statement_date if available (DBS does not
        # expose an explicit period range, only a single statement_date).
        sd = str(meta.get("statement_date", ""))
        # DBS dates come as "DD Mon YYYY" (e.g. "30 Jun 2026"); normalise to ISO.
        if sd:
            from datetime import datetime
            try:
                dt = datetime.strptime(sd, "%d %b %Y")
                sd_iso = dt.strftime("%Y-%m-%d")
                _ = builder.set_period(sd_iso, sd_iso)
            except ValueError:
                pass  # silent fallback — period remains empty

        # Build per-account balance lookups keyed by account number so each
        # Account can absorb its summary-derived balance (replacing the old
        # flat account_summary table).
        deposit_by_no = {str(d.get("account_no", "")): d for d in summary.get("deposits", [])}
        fd_by_no = {str(d.get("account_no", "")): d for d in summary.get("fixed_deposits", [])}
        srs_by_no = {str(srs_data.get("account_no", ""))} if srs_data.get("account_no") else set()

        def _srs_holdings() -> list[InvestmentHolding]:
            """Map SRS unit trusts to per-account InvestmentHoldings."""
            holdings: list[InvestmentHolding] = []
            for ut in srs_data.get("unit_trusts", []):
                holdings.append(InvestmentHolding(
                    name=ut.get("name", ""),
                    units=ut.get("free_qty", ""),
                    currency="SGD",
                    valuation=ut.get("market_value", ""),
                    cost=ut.get("total_cost", ""),
                    unrealised_pl=ut.get("unrealised_pl", ""),
                ))
            return holdings

        srs_account_added = False
        for acct in accounts:
            acct_no = str(acct.get("account_no", ""))
            name = str(acct.get("name", ""))
            is_fd = (name == "Fixed Deposit") or (acct_no in fd_by_no)
            is_srs = (name == "SRS Account") or (acct_no in srs_by_no)

            # TODO: DBS fixed-deposit handling is incorrect — fd_records and
            # transactions are being mixed. The parser exposes the FD account's
            # own movement lines (renewals, premature withdrawals, interest
            # postings) under "fd_transactions", but here every line is mapped
            # to add_fd_record (a deposit-placement record) regardless of
            # whether it is a true new placement or a transactional movement.
            # They need to be split: genuine placements → add_fd_record, while
            # withdrawals/interest/maturity movements → add_transaction.
            # After fixing, the data currently in "fd_transactions" should be
            # fully reflected in fd_records (placements) and transactions
            # (movements), so there is no need to retain "fd_transactions" as a
            # separate field in the IR JSON output.
            if is_srs:
                # SRS is a first-class account: it owns a balance and its
                # transactions like any other account, but additionally owns a
                # list of investment holdings (unit trusts). The summary-derived
                # cash balance comes from srs_data (the SRS account number is
                # not in the page-0 CASA table).
                srs_account_added = True
                cash_balance_raw = srs_data.get("cash_balance", "")
                srs_balance = _parse_float(cash_balance_raw) if cash_balance_raw else None
                account_type = AccountType.SRS
                txn_list = acct.get("transactions", [])
                _ = builder.add_account(
                    name=name or "SRS Account",
                    account_no=acct_no or str(srs_data.get("account_no", "")),
                    account_type=account_type.value,
                    currency=str(acct.get("currency", base_ccy)),
                    opening_balance=_parse_float(acct.get("opening_balance")),
                    balance=srs_balance,
                    balance_sgd=srs_balance,
                    investment_holdings=_srs_holdings(),
                    extras={
                        "total": srs_data.get("total", ""),
                        "contributions": srs_data.get("contributions", {}),
                    },
                )
            elif is_fd:
                bal = fd_by_no.get(acct_no, {})
                account_type = AccountType.FIXED_DEPOSIT
                txn_list = acct.get("fd_transactions", [])
                _ = builder.add_account(
                    name=name,
                    account_no=acct_no,
                    account_type=account_type.value,
                    currency=str(acct.get("currency", base_ccy)),
                    opening_balance=_parse_float(acct.get("opening_balance")),
                    balance=_parse_float(bal.get("balance")),
                    balance_sgd=_parse_float(bal.get("balance_sgd")),
                )
            else:
                bal = deposit_by_no.get(acct_no, {})
                account_type = AccountType.CURRENT
                txn_list = acct.get("transactions", [])
                _ = builder.add_account(
                    name=name,
                    account_no=acct_no,
                    account_type=account_type.value,
                    currency=str(acct.get("currency", base_ccy)),
                    opening_balance=_parse_float(acct.get("opening_balance")),
                    balance=_parse_float(bal.get("balance")),
                    balance_sgd=_parse_float(bal.get("balance_sgd")),
                )

            for t in txn_list:
                withdrawal = float(str(t.get("withdrawal", "0") or "0").replace(",", ""))
                deposit = float(str(t.get("deposit", "0") or "0").replace(",", ""))
                amount = deposit - withdrawal
                balance_str = str(t.get("balance", "") or "").replace(",", "")
                balance = float(balance_str) if balance_str else None
                posted_date = _dbs_date_to_iso(str(t.get("txn_date", "")))
                description = str(t.get("description", ""))

                if is_fd:
                    principal = _parse_float(t.get("principal")) or 0.0
                    value_date_iso, maturity_date_iso = _dbs_period_to_dates_iso(
                        str(t.get("period", ""))
                    )
                    _ = builder.add_fd_record(
                        deposit_no=str(t.get("deposit_no", "")),
                        value_date=value_date_iso,
                        maturity_date=maturity_date_iso,
                        interest_rate=str(t.get("interest_rate", "")),
                        interest_amount=_parse_float(t.get("interest_amt")),
                        principal=principal,
                        currency=base_ccy,
                        description=description,
                    )
                else:
                    _ = builder.add_transaction(
                        posted_date=posted_date,
                        amount=amount,
                        currency=str(t.get("currency", base_ccy)),
                        description=description,
                        raw_description=description,
                        is_accrual=False,
                        balance_after=balance,
                    )

        # ---- SRS fallback ----
        # The SRS account normally comes from the page-4 transaction section
        # (handled in the loop above). If that section is absent but srs_data
        # was parsed from page 1, still emit a first-class SRS account carrying
        # its balance, holdings, and contributions (with no transactions).
        if srs_data and not srs_account_added:
            cash_balance_raw = srs_data.get("cash_balance", "")
            srs_balance = _parse_float(cash_balance_raw) if cash_balance_raw else None
            _ = builder.add_account(
                name="SRS Account",
                account_no=str(srs_data.get("account_no", "")),
                account_type=AccountType.SRS.value,
                currency=base_ccy,
                balance=srs_balance,
                balance_sgd=srs_balance,
                investment_holdings=_srs_holdings(),
                extras={
                    "total": srs_data.get("total", ""),
                    "contributions": srs_data.get("contributions", {}),
                },
            )

        return builder.build()


def _dbs_date_to_iso(date_str: str) -> str:
    """Convert DBS DD/MM/YYYY → ISO YYYY-MM-DD."""
    parts = date_str.split("/")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return date_str


def _dbs_period_to_dates_iso(period: str) -> tuple[str | None, str | None]:
    """DBS FD period is 'DD/MM/YYYY - DD/MM/YYYY'.

    The former date is the start/deal date (value_date) and the latter date
    is the maturity date. Both are returned ISO-normalized.
    """
    parts = period.split("-")
    if len(parts) == 2:
        return _dbs_date_to_iso(parts[0].strip()), _dbs_date_to_iso(parts[1].strip())
    return None, None


def _parse_float(val: object) -> float | None:
    """Parse a potentially comma-formatted value to float, returning None on failure."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).replace(",", "").strip()
        return float(s) if s else None
    except (ValueError, TypeError):
        return None

"""Minimal smoke tests — no PDF fixtures required.

These assert the package imports, the IR builder produces well-formed records,
the (bank, family) extractor registry is populated, and masking runs. Fixture-
driven parsing tests live under tests/cache + tests/outputs (which are gitignored).

Running the suite requires the project to be installed (``pip install -e .``)
so that ``pdfplumber`` (an import-time dependency) is available.
"""

from typing import Any

import sg_bank_pdf_parser
from sg_bank_pdf_parser.common import WordDict, mask_id, sanitize_description
from sg_bank_pdf_parser.ir_builder import IRBuilder


def test_package_imports():
    # __init__ re-exports main (the sgbankpdf CLI entry point)
    assert hasattr(sg_bank_pdf_parser, "main")
    assert callable(sg_bank_pdf_parser.main)


def test_ir_builder_basic_record():
    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    _ = (
        builder
        .add_account(name="Smoke Account", account_no="****1234")
        .add_transaction(posted_date="2026-01-02", description="Test txn", amount=12.34)
        .add_transaction(posted_date="2026-01-03", description="Test txn 2", amount=-5.00)
    )
    stmt = builder.build()

    assert stmt.statement_meta.institution == "TEST"
    assert len(stmt.accounts) == 1
    assert len(stmt.accounts[0].transactions) == 2
    # deterministic content-hash txn_id + auto-computed base_amount
    assert stmt.accounts[0].transactions[0].txn_id
    assert stmt.accounts[0].transactions[0].base_amount == 12.34


def test_registry_populated():
    from sg_bank_pdf_parser.extractors.registry import get_extractor

    assert get_extractor("dbs", "consolidated") is not None
    assert get_extractor("ocbc", "card") is not None
    assert get_extractor("uob", "one") is not None
    assert get_extractor("icbc", "consolidated") is not None


def test_masking_runs():
    masked = sanitize_description("NRIC S1234567A and account 12345678")
    assert "S1234567A" not in masked
    assert "[NRIC]" in masked
    # account/card numbers keep only the last 4 digits
    assert mask_id("12345678") == "XXXX5678"


def test_dbs_fd_section_splits_placements_and_movements():
    """Parser must tag dated rows as placements and Deposit-No.-led sub-rows
    (no date) as movements, instead of merging everything into one record.
    A dated row describing a withdrawal/premature credit is a *movement*, not a
    placement."""
    from sg_bank_pdf_parser.parsers.dbs_parser import _parse_fd_section  # pyright: ignore[reportPrivateUsage]

    def w(text: str, x0: int, x1: int) -> WordDict:
        return {"text": text, "x0": float(x0), "x1": float(x1), "top": 0.0}

    lines = [
        # Account header
        [w("Fixed", 38, 60), w("Deposit", 70, 100), w("Account", 110, 160),
         w("No.", 170, 190), w("1234-56789012-3", 200, 260)],
        # FD table header
        [w("Date", 40, 60), w("Deposit", 95, 120), w("No.", 125, 145),
         w("Period", 185, 220), w("Description", 295, 340)],
        # Placement row (dated)
        [w("02/06/2025", 40, 90), w("123456789012", 95, 165),
         w("02/06/2025", 185, 230), w("-", 232, 235), w("02/06/2026", 237, 285),
         w("Renew", 295, 330), w("Principal", 332, 380), w("&", 382, 388),
         w("500.00", 410, 450), w("10,000.00", 500, 555)],
        # Placement continuation (interest rate line)
        [w("365/365", 295, 330), w("Interest", 332, 380), w("0.050000", 500, 555)],
        # Premature withdrawal row (dated, a movement -> transaction only)
        [w("02/06/2026", 40, 90), w("123456789012", 95, 165),
         w("02/06/2026", 185, 230), w("-", 232, 235), w("02/06/2027", 237, 285),
         w("Premature", 295, 330), w("Withdrawal", 332, 380),
         w("Credited", 382, 420), w("to", 422, 440), w("003-0-XX0350", 442, 520),
         w("10,245.00", 500, 555)],
        # Movement sub-row (no date; starts with the 12-digit Deposit No.)
        [w("123456789099", 95, 165),
         w("02/06/2026", 185, 230), w("-", 232, 235), w("02/06/2027", 237, 285),
         w("New", 295, 330), w("Rollover", 332, 380), w("Deposit", 382, 420),
         w("600.00", 410, 450), w("11,000.00", 500, 555)],
        # Termination
        [w("Total", 40, 60), w("Principal", 95, 140), w("Amount", 145, 190),
         w("21,000.00", 500, 555)],
    ]

    accounts: list[dict[str, Any]] = []
    _parse_fd_section(lines, accounts)

    assert len(accounts) == 1
    fd = accounts[0]["fd_transactions"]
    assert len(fd) == 3, fd

    assert fd[0]["txn_type"] == "placement"
    assert fd[0]["deposit_no"] == "123456789012"
    assert fd[0]["principal"] == "10,000.00"

    # Dated premature-withdrawal row is a movement, not a placement
    assert fd[1]["txn_type"] == "movement"
    assert fd[1]["deposit_no"] == "123456789012"
    assert fd[1]["principal"] == "10,245.00"

    # "New Rollover Deposit" sub-row is a *placement* (a fixed deposit record),
    # even though it has no leading date and starts with the Deposit No.
    assert fd[2]["txn_type"] == "placement"
    assert fd[2]["deposit_no"] == "123456789099"
    assert fd[2]["principal"] == "11,000.00"
    assert fd[2]["description"]


def test_dbs_fd_premature_penalty_continuation_flips_to_movement():
    """A dated "Withdrawal" main line is a *placement* (FD record) on its own,
    but when DBS appends a continuation line "Interest Due To Premature
    Withdrawal ...", the whole row must be reclassified as a *movement*
    (transaction, not an FD record). See references/dbs-layouts.md,
    "FD premature-withdrawal penalty continuation"."""
    from sg_bank_pdf_parser.parsers.dbs_parser import _parse_fd_section  # pyright: ignore[reportPrivateUsage]

    def w(text: str, x0: int, x1: int) -> WordDict:
        return {"text": text, "x0": float(x0), "x1": float(x1), "top": 0.0}

    lines = [
        [w("Fixed", 38, 60), w("Deposit", 70, 100), w("Account", 110, 160),
         w("No.", 170, 190), w("1234-56789012-3", 200, 260)],
        [w("Date", 40, 60), w("Deposit", 95, 120), w("No.", 125, 145),
         w("Period", 185, 220), w("Description", 295, 340)],
        # Dated withdrawal main line (plain "Withdrawal" -> placement by itself)
        [w("02/06/2026", 40, 90), w("123456789012", 95, 165),
         w("02/06/2026", 185, 230), w("-", 232, 235), w("02/06/2027", 237, 285),
         w("Withdrawal", 295, 340), w("Credited", 342, 380), w("to", 382, 400),
         w("003-0-XX0350", 402, 480), w("10,245.00", 505, 555)],
        # Continuation: premature-withdrawal penalty line
        [w("Interest", 295, 335), w("Due", 337, 360), w("To", 362, 380),
         w("Premature", 382, 430), w("Withdrawal", 432, 480)],
        [w("Total", 40, 60), w("Principal", 95, 140), w("Amount", 145, 190),
         w("10,245.00", 505, 555)],
    ]

    accounts: list[dict[str, Any]] = []
    _parse_fd_section(lines, accounts)

    assert len(accounts) == 1
    fd = accounts[0]["fd_transactions"]
    assert len(fd) == 1, fd

    # The premature penalty continuation flips the row to a movement.
    assert fd[0]["txn_type"] == "movement"
    assert fd[0]["deposit_no"] == "123456789012"
    assert "Interest Due To Premature Withdrawal" in fd[0]["description"]


def test_transaction_interest_amount_roundtrip():
    """``interest_amount`` is a first-class field and must survive to_dict/from_dict."""
    from sg_bank_pdf_parser.ir_schema import from_json, to_json

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    _ = (
        builder.add_account(name="FD", account_no="FD1", account_type="fixed_deposit")
        .add_transaction(
            posted_date="2026-06-02",
            amount=-10245.0,
            tags=["fd_principal", "fd_interest"],
            interest_amount=0.03,
        )
    )
    stmt = builder.build()

    restored = from_json(to_json(stmt))
    txn = restored.accounts[0].transactions[0]
    assert txn.interest_amount == 0.03
    assert txn.tags == ["fd_principal", "fd_interest"]


def test_link_fd_to_ca_closure_with_interest():
    """Closure w/ interest: the CA twin carries BOTH atomic tags (fd_principal +
    fd_interest) and links bidirectionally. CA credit == principal + interest."""
    from sg_bank_pdf_parser.account_type import AccountType
    from sg_bank_pdf_parser.postprocess import link_fd_to_ca

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    _ = (
        builder.add_account(name="FD", account_no="FD1",
                            account_type=AccountType.FIXED_DEPOSIT.value)
        .add_transaction(
            posted_date="2026-06-02",
            amount=-10245.0,
            tags=["fd_principal", "fd_interest"],
            interest_amount=0.03,
            extras={"fd_link": {"deposit_no": "123"}},
        )
    )
    _ = (
        builder.add_account(name="CA", account_no="CA1")
        .add_transaction(
            posted_date="2026-06-02",
            amount=10245.03,
            tags=["ca_twin"],
        )
    )
    stmt = builder.build()
    _ = link_fd_to_ca(stmt)

    fd_txn = stmt.accounts[0].transactions[0]
    ca_txn = stmt.accounts[1].transactions[0]
    assert fd_txn.related_txn_ids == [ca_txn.txn_id]
    assert ca_txn.related_txn_ids == [fd_txn.txn_id]
    assert ca_txn.is_internal_transfer is True
    assert ca_txn.category_hint == "fixed_deposit"
    assert "fd_principal" in ca_txn.tags
    assert "fd_interest" in ca_txn.tags
    assert sorted(set(ca_txn.tags)) == ["ca_twin", "fd_interest", "fd_principal"]


def test_link_fd_to_ca_principal_only():
    """Principal-only placement (e.g. ICBC / new placement): the CA twin gets
    fd_principal only, no interest leg, and still links."""
    from sg_bank_pdf_parser.account_type import AccountType
    from sg_bank_pdf_parser.postprocess import link_fd_to_ca

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    _ = (
        builder.add_account(name="FD", account_no="FD1",
                            account_type=AccountType.FIXED_DEPOSIT.value)
        .add_transaction(
            posted_date="2026-06-02",
            amount=-10000.0,
            tags=["fd_principal"],
        )
    )
    _ = (
        builder.add_account(name="CA", account_no="CA1")
        .add_transaction(
            posted_date="2026-06-02",
            amount=10000.0,
            tags=["ca_twin"],
        )
    )
    stmt = builder.build()
    _ = link_fd_to_ca(stmt)

    ca_txn = stmt.accounts[1].transactions[0]
    assert ca_txn.related_txn_ids
    assert "fd_principal" in ca_txn.tags
    assert "fd_interest" not in ca_txn.tags


def test_link_fd_to_ca_separate_principal_and_interest_legs():
    """Separate FD transactions: principal and interest emitted as two FD rows
    (same deposit_no + date). The single CA credit (= principal + interest) must
    link to *both* legs, and each leg must link back to the CA twin."""
    from sg_bank_pdf_parser.account_type import AccountType
    from sg_bank_pdf_parser.postprocess import link_fd_to_ca

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    fd_acct = builder.add_account(
        name="FD", account_no="FD1", account_type=AccountType.FIXED_DEPOSIT.value
    )
    _ = fd_acct.add_transaction(
        posted_date="2026-06-02",
        amount=-10000.0,
        tags=["fd_principal"],
        extras={"fd_link": {"deposit_no": "DEP-1"}},
    )
    _ = fd_acct.add_transaction(
        posted_date="2026-06-02",
        amount=-0.03,
        tags=["fd_interest"],
        extras={"fd_link": {"deposit_no": "DEP-1"}},
    )
    _ = (
        builder.add_account(name="CA", account_no="CA1")
        .add_transaction(
            posted_date="2026-06-02",
            amount=10000.03,
            tags=["ca_twin"],
        )
    )
    stmt = builder.build()
    _ = link_fd_to_ca(stmt)

    fd_txns = stmt.accounts[0].transactions
    principal_leg = next(t for t in fd_txns if "fd_principal" in t.tags)
    interest_leg = next(t for t in fd_txns if "fd_interest" in t.tags)
    ca_txn = stmt.accounts[1].transactions[0]

    # The CA twin references both FD legs.
    assert sorted(ca_txn.related_txn_ids) == sorted(
        [principal_leg.txn_id, interest_leg.txn_id]
    )
    # Each FD leg references the CA twin.
    assert principal_leg.related_txn_ids == [ca_txn.txn_id]
    assert interest_leg.related_txn_ids == [ca_txn.txn_id]
    # Both atomic tags are copied onto the twin.
    assert sorted(set(ca_txn.tags)) == [
        "ca_twin",
        "fd_interest",
        "fd_principal",
    ]
    assert ca_txn.is_internal_transfer is True
    assert ca_txn.category_hint == "fixed_deposit"


def test_link_fd_to_ca_split_ca_credits():
    """DBS maturity: principal + interest legs link to TWO separate CA credits."""
    from sg_bank_pdf_parser.account_type import AccountType
    from sg_bank_pdf_parser.postprocess import link_fd_to_ca

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    fd_acct = builder.add_account(
        name="FD", account_no="FD1", account_type=AccountType.FIXED_DEPOSIT.value
    )
    _ = fd_acct.add_transaction(
        posted_date="2026-06-13",
        amount=-9999.0,
        tags=["fd_principal"],
        extras={"fd_link": {"deposit_no": "DEP-13", "fd_account_no": "FD1"}},
    )
    _ = fd_acct.add_transaction(
        posted_date="2026-06-13",
        amount=-0.0,
        interest_amount=244.98,
        tags=["fd_interest"],
        extras={"fd_link": {"deposit_no": "DEP-13", "fd_account_no": "FD1"}},
    )
    _ = builder.add_account(name="CA", account_no="CA1").add_transaction(
        posted_date="2026-06-13",
        amount=9999.0,
        tags=["ca_twin"],
    )
    _ = builder.add_account(name="CA2", account_no="CA2").add_transaction(
        posted_date="2026-06-13",
        amount=244.98,
        tags=["ca_twin"],
    )
    stmt = builder.build()
    _ = link_fd_to_ca(stmt)

    fd_txns = stmt.accounts[0].transactions
    principal_leg = next(t for t in fd_txns if "fd_principal" in t.tags)
    interest_leg = next(t for t in fd_txns if "fd_interest" in t.tags)
    ca_p = stmt.accounts[1].transactions[0]
    ca_i = stmt.accounts[2].transactions[0]

    # Principal leg <-> principal CA credit.
    assert ca_p.txn_id in principal_leg.related_txn_ids
    assert principal_leg.txn_id in ca_p.related_txn_ids
    assert principal_leg.is_internal_transfer and ca_p.is_internal_transfer
    assert "fd_principal" in ca_p.tags

    # Interest leg <-> interest CA credit.
    assert ca_i.txn_id in interest_leg.related_txn_ids
    assert interest_leg.txn_id in ca_i.related_txn_ids
    assert interest_leg.is_internal_transfer and ca_i.is_internal_transfer
    assert "fd_interest" in ca_i.tags

    # Each CA credit links to exactly ONE FD leg (no cross-contamination).
    assert ca_p.related_txn_ids == [principal_leg.txn_id]
    assert ca_i.related_txn_ids == [interest_leg.txn_id]


def test_link_fd_to_ca_internal_fd_not_transfer():
    """A FD leg with no CA twin (renewal/rollover) is NOT a transfer."""
    from sg_bank_pdf_parser.account_type import AccountType
    from sg_bank_pdf_parser.postprocess import link_fd_to_ca, verify_transfer_links

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    fd_acct = builder.add_account(
        name="FD", account_no="FD1", account_type=AccountType.FIXED_DEPOSIT.value
    )
    _ = fd_acct.add_transaction(
        posted_date="2026-06-02",
        amount=-10000.0,
        is_internal_transfer=True,  # extractor pre-marks every FD leg
        tags=["fd_principal"],
        extras={"fd_link": {"deposit_no": "DEP-R", "fd_account_no": "FD1"}},
    )
    # No funding account at all -> nothing to link to.
    stmt = builder.build()
    _ = link_fd_to_ca(stmt)

    leg = stmt.accounts[0].transactions[0]
    assert leg.is_internal_transfer is False
    assert leg.related_txn_ids == []

    _ = verify_transfer_links(stmt)
    assert not any("transfer without linked twin" in w for w in stmt.warnings)


def test_verify_transfer_links_flags_orphan_transfer():
    """A transfer with an empty related_txn_ids list must be flagged."""
    from sg_bank_pdf_parser.postprocess import verify_transfer_links

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    _ = (
        builder.add_account(name="CA", account_no="CA1")
        .add_transaction(
            posted_date="2026-06-02",
            amount=100.0,
            is_internal_transfer=True,  # linked twin missing
        )
    )
    stmt = builder.build()
    _ = verify_transfer_links(stmt)

    assert any("transfer without linked twin" in w for w in stmt.warnings)


def test_verify_transfer_links_passes_linked_or_non_transfer():
    """No warning when the transfer has links, or when it is not a transfer."""
    from sg_bank_pdf_parser.postprocess import verify_transfer_links

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    linked = builder.add_account(name="CA", account_no="CA1")
    _ = linked.add_transaction(
        posted_date="2026-06-02",
        amount=100.0,
        is_internal_transfer=True,
        related_txn_ids=["FD-X"],
    )
    _ = builder.add_account(name="CA2", account_no="CA2").add_transaction(
        posted_date="2026-06-02",
        amount=50.0,
        is_internal_transfer=False,  # ordinary txn, links allowed to be empty
    )
    stmt = builder.build()
    _ = verify_transfer_links(stmt)

    assert not any("transfer without linked twin" in w for w in stmt.warnings)


def test_verify_transfer_links_flags_non_reciprocal():
    """If A lists B as related but B does not list A back, it must be flagged.

    B carries a (dangling) link to C so it is not itself an orphan, isolating
    the reciprocity rule from the empty-links rule.
    """
    from sg_bank_pdf_parser.postprocess import verify_transfer_links

    builder = IRBuilder("test_smoke", "1.0")
    _ = builder.set_meta(institution="TEST", currency="SGD")
    a = builder.add_account(name="CA", account_no="CA1")
    _ = a.add_transaction(
        posted_date="2026-06-02",
        amount=100.0,
        is_internal_transfer=True,
    )
    b = builder.add_account(name="FD", account_no="FD1")
    _ = b.add_transaction(
        posted_date="2026-06-02",
        amount=-100.0,
        is_internal_transfer=True,
        related_txn_ids=["C1"],  # B -> C (C absent); A not listed back
    )
    stmt = builder.build()
    a_txn = stmt.accounts[0].transactions[0]
    b_txn = stmt.accounts[1].transactions[0]
    # A -> B, but B does not list A back.
    a_txn.related_txn_ids = [b_txn.txn_id]
    _ = verify_transfer_links(stmt)

    assert any("transfer link not reciprocal" in w for w in stmt.warnings)
    assert not any("transfer without linked twin" in w for w in stmt.warnings)

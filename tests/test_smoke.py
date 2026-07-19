"""Minimal smoke tests — no PDF fixtures required.

These assert the package imports, the IR builder produces well-formed records,
the (bank, family) extractor registry is populated, and masking runs. Fixture-
driven parsing tests live under tests/cache + tests/outputs (which are gitignored).

Running the suite requires the project to be installed (``pip install -e .``)
so that ``pdfplumber`` (an import-time dependency) is available.
"""

import sg_bank_pdf_parser
from sg_bank_pdf_parser.common import mask_id, sanitize_description
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

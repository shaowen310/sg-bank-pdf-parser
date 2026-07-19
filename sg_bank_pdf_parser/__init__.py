"""sg_bank_pdf_parser — Parse Singapore bank statement PDFs into structured IR and Markdown.

Supported banks: DBS/POSB, OCBC, UOB, ICBC.

Usage:
    from sg_bank_pdf_parser import detect_type, ParsedStatement
    from sg_bank_pdf_parser.convert_statement import main

    # CLI: sgbankpdf input.pdf output.md --no-mask
    # Python: python -m sg_bank_pdf_parser input.pdf
"""

from .ir_schema import ParsedStatement, Transaction, StatementMeta, SourceAccount
from .convert_statement import detect_type, main

__version__ = "0.1.0"
__all__ = [
    "ParsedStatement",
    "Transaction",
    "StatementMeta",
    "SourceAccount",
    "detect_type",
    "main",
]

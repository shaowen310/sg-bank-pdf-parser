# sg_bank_pdf_parser

Parse Singapore bank statement PDFs (DBS, OCBC, UOB, ICBC) into structured
Markdown tables **and** a machine-readable IR JSON. No OCR, no manual cleanup.

This repository is both:

- a **pip-installable Python library** (`sg_bank_pdf_parser`) exposing the
  `sgbankpdf` CLI, and
- a **CodeBuddy skill** (`SKILL.md` + `references/`) — installable via
  `pack_and_install_skill.py` when checked out as the `sg-bank-to-md` submodule
  of the [`skills`](https://github.com/shaowen310/skills) monorepo.

## Supported banks / statement families

| Bank | Families |
| --- | --- |
| DBS / POSB | Consolidated (Account Summary + multi-account Transaction Details, Fixed Deposits, etc.) |
| OCBC | Consolidated (Account Summary + multi-account Transaction Details, Fixed Deposits, etc.) · Credit card |
| ICBC | Consolidated (Account Summary + multi-account Transaction Details, Fixed Deposits, etc.) |
| UOB | Single-account · One multi-account · Multi-account portfolio |

Detection is **explicit** (regex rules per family) with **no fallback** — an
unrecognized PDF is reported as unsupported rather than silently mis-parsed.

## Install

```bash
# From PyPI (after publishing)
pip install sg_bank_pdf_parser

# Or from a checkout / git submodule
pip install -e .
```

Requires **Python >= 3.12** and `pdfplumber` (installed automatically).

## Usage

```bash
sgbankpdf <input.pdf> [output.md] [--no-mask]
# or
python -m sg_bank_pdf_parser <input.pdf> [output.md] [--no-mask]
```

- `input.pdf` — the bank statement PDF.
- `output.md` — optional; defaults to `<input>.md`.
- `--no-mask` — disable masking (account numbers, NRIC/FIN, person names).
  Masking is **on by default**.

Two files are produced alongside the input:

- `<input>.md` — human-readable Markdown tables (sensitive data masked).
- `<input>.ir.json` — schema-versioned `ParsedStatement` IR
  (`ir_version`, `statement_meta`, `accounts[]`, `warnings[]`) for downstream
  cashflow analysis and multi-bank consolidation.

### Programmatic API

```python
from sg_bank_pdf_parser.convert_statement import run

statement = run("statement.pdf")   # -> ParsedStatement (IR dataclass)
print(statement.statement_meta.bank, statement.statement_meta.family)
```

## Masking

On by default: account/card/deposit numbers show only the last 4 digits;
long numeric IDs (4+ digits) in descriptions become `[ID-XXXX]`; NRIC/FIN is
fully replaced with `[NRIC]`; person names are masked context-aware (UEN, bank
codes, and reference numbers are preserved).

## Repo layout

```
sg-bank-pdf-parser/
├── pyproject.toml          # pip project (sgbankpdf CLI)
├── SKILL.md                # CodeBuddy skill wrapper
├── references/             # per-bank layout coordinates & quirks
├── tests/                  # smoke tests (fixtures are gitignored)
└── sg_bank_pdf_parser/     # the package
    ├── convert_statement.py  # CLI entry + auto-detect/dispatch
    ├── ir_schema.py          # ParsedStatement / Account / Transaction
    ├── ir_builder.py         # chainable IRBuilder
    ├── common.py             # masking, sanitization, validation
    ├── extractors/           # IR extraction + (bank, family) registry
    ├── parsers/              # bank-specific pdfplumber logic
    └── renderers/            # per-family Markdown renderers + masking
```

## Development

```bash
pip install -e ".[dev]"   # if a dev extra is added
pytest                     # runs tests/test_smoke.py
```

## License

[MIT](LICENSE) © 2026 Zhou Shaowen

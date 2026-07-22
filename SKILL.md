---
name: sg-bank-to-md
description: >-
  Convert Singapore bank (DBS, OCBC, UOB, ICBC) PDF statements into structured
  Markdown tables and machine-readable IR JSON. Three-stage pipeline (parse → IR
  → render) with explicit auto-detection of 7 statement families; automatic
  masking of account numbers, card numbers, NRIC/FIN, and person names. No OCR,
  no manual clean-up. Use for: "convert DBS/OCBC/UOB/ICBC statement PDF to
  Markdown", "extract transactions from my bank statement", "turn my credit
  card PDF into a table", "summarize my UOB portfolio statement", "convert my
  ICBC/UOB One statement to Markdown".
agent_created: true
---

# SG Bank to Markdown

## Overview

Transform Singapore bank PDF statements into structured Markdown and a
machine-readable IR JSON. The pipeline has three stages — **raw PDF parse**
(bank-specific `pdfplumber` logic) → **IR extraction** (structured
`ParsedStatement` dataclass via the chainable `IRBuilder`) → **Markdown render**
(per-bank/family renderer, with masking applied at output time).

The script auto-detects the source bank (DBS, OCBC, UOB, ICBC) and the statement
family via explicit regex rules — **no fallback defaults**:

- **DBS/POSB consolidated** — Account Summary (CASA + FD), SRS with Unit Trusts, multi-account Transaction Details.
- **OCBC consolidated statement** — multiple accounts, 7-col transaction tables, opening/closing balances, per-section totals.
- **OCBC credit card** — single date column, parenthesized credits, FX `Currency / Amount` column, reconciliation block.
- **ICBC bilingual** — multi-currency Current + Fixed Deposit accounts, per-currency Dr./Cr. summaries, reminders/notes.
- **UOB single-account** — 5-col table, `BALANCE B/F` opening, multi-line descriptions.
- **UOB One multi-account** — multiple `Account Transaction Details` sections per PDF, continuation pages merged.
- **UOB multi-account portfolio** — Portfolio Overview, Deposits, Investments tables, FX reference rates.

Unrecognized formats are reported as unsupported (no silent wrong-bank processing).

## When to use

### DBS / POSB
- "Convert this DBS consolidated statement PDF to Markdown"
- "Extract the transactions from my DBS/POSB bank statement"

### OCBC
- "Convert this OCBC statement PDF to Markdown"
- "Turn my OCBC credit card PDF into a table"

### ICBC
- "Convert this ICBC statement PDF to Markdown"
- "Extract the transactions from my ICBC bank statement"

### UOB
- "Parse the transactions from this UOB bank statement PDF"
- "Summarize my UOB portfolio statement" / "Convert my UOB One statement to Markdown"

## Workflow

### Step 1 — Install

```bash
# From PyPI (published)
pip install sg_bank_pdf_parser

# Or from a checkout (e.g. this repo, or as a git submodule)
pip install -e .
```

This installs the `sgbankpdf` CLI and the `sg_bank_pdf_parser` Python package.
Requires Python >= 3.12 and `pdfplumber` (auto-installed).

### Step 2 — Run the converter

```bash
sgbankpdf <input.pdf> [output.md] [--no-mask]
# or
python -m sg_bank_pdf_parser <input.pdf> [output.md] [--no-mask]
```

- `input.pdf` — path to the bank statement PDF.
- `output.md` — optional; defaults to the input filename with `.md`.
- `--no-mask` — disables all masking (account numbers, NRIC, person names). Enabled by default.

Two outputs are produced:
- `.md` — human-readable Markdown tables, sensitive data masked.
- `.ir.json` — schema-versioned Intermediate Representation for downstream cashflow analysis / multi-bank consolidation.

### Step 3 — Verify

The CLI prints the detected bank + family and the record count. If output is
empty or columns are misaligned, the statement may be a new product family —
re-measure column x-edges (see `references/`) rather than patching blindly.

## Output format (summary)

**Masking (on by default):** account/card/deposit numbers show only last 4
digits; long numeric IDs (4+ digits) in descriptions become `[ID-XXXX]`; NRIC/FIN
fully replaced with `[NRIC]`; person names masked context-aware (preserves UEN,
bank codes, reference numbers). PDF artifacts like fused brackets are cleaned.

**Structured IR (`.ir.json`):** `ir_version` (currently `"2026.3"`),
`statement_meta`, `accounts[]` (each with identity, balances, nested
`transactions[]`), and `warnings[]`. Always generated alongside the Markdown.

Per-bank Markdown layout details are intentionally not duplicated here — see the
source renderers and `references/` (below).

## Public IR Contract

The `*.ir.json` output is the **supported downstream interface** for this skill.
Two authoritative definitions of the contract exist:

1. **Python (source of truth)** — `sg_bank_pdf_parser/ir_schema.py`
   (`ParsedStatement` and friends, with `to_json()` / `from_json()`).
2. **Language-agnostic** — `references/ir.schema.json` (JSON Schema 2020-12),
   for validating IR produced/consumed by non-Python tools.

### Compatibility rule
- Every IR carries an `ir_version` string. Downstream consumers **must** require
  `ir_version >= "2026.3"`.
- `from_json()` / `from_dict()` **reject** any IR that lacks the `accounts`
  field (i.e. IR produced by parsers older than 2026.3). Callers must re-run
  extraction from the source PDF.
- When merging multiple IRs, carry forward the **minimum** `ir_version` you
  observe so the result stays consumable by the same minimum-version consumers.

### Field contract (summary)
- Top level requires: `ir_version`, `accounts`.
- `Account.account_type` is constrained to the `AccountType` vocabulary
  (`current`, `credit_card`, `ewallet`, `fixed_deposit`, `srs`, `unit_trust`,
  `unknown`). `AccountType.normalize` coerces unknown values to `unknown`.
- `Transaction.txn_id` is a deterministic content hash — use it for cross-source
  de-duplication.
- `balance` is per-`currency`; SGD-equivalent values are computed via FX rates
  in downstream renderers.

## Architecture

1. **Raw PDF parsing** (`parsers/*_parser.py`) — bank-specific `pdfplumber`
   extraction → plain dicts/lists.
2. **IR extraction** (`extractors/*_extractor.py` + `extractors/registry.py`) —
   wraps parser output into `ParsedStatement` via `IRBuilder`; registry maps
   `(bank, family)` → extractor.
3. **Markdown rendering** (`renderers/markdown.py`) — one `*_ir_to_markdown()`
   per family; masking applied at render time.

## Resources

### Pipeline core
- `sg_bank_pdf_parser/convert_statement.py` — CLI entry; auto-detect + dispatch.
- `sg_bank_pdf_parser/extractors/base.py` — `BaseExtractor` ABC.
- `sg_bank_pdf_parser/extractors/registry.py` — `(bank, family)` → extractor.

### IR layer
- `sg_bank_pdf_parser/ir_schema.py` — `ParsedStatement`, `Account`, `Transaction`, …
- `sg_bank_pdf_parser/ir_builder.py` — chainable `IRBuilder` (auto `txn_id`, `base_amount`).
- `sg_bank_pdf_parser/renderers/markdown.py` — per-family renderers + `MD_RENDERER_REGISTRY`.
- `sg_bank_pdf_parser/renderers/helpers.py` — rendering helpers wrapping masking.
- `sg_bank_pdf_parser/common.py` — masking, sanitization, line grouping, validation.

### Bank extractors / parsers
- DBS: `extractors/dbs_extractor.py`, `parsers/dbs_parser.py`
- OCBC: `extractors/ocbc_extractor.py`, `parsers/ocbc_parser.py`
- ICBC: `extractors/icbc_extractor.py`, `parsers/icbc_parser.py`
- UOB: `extractors/uob_extractor.py`, `parsers/uob_parser.py`

### Layout references (`references/`)
- `dbs-layouts.md`, `icbc-layouts.md`, `ocbc-layouts.md`, `uob-layouts.md`, `layouts.md`
  — column x-edge coordinates, section markers, parsing quirks, and how to
  re-measure when a new statement family appears.

## Notes / gotchas

- **Detection is explicit and order-independent.** Each bank is identified by a unique,
  mutually-exclusive signal (no fallback, no priority): UOB via `uobgroup.com` email in
  the "Contact Us" card; ICBC via `Statement Date 结单日期`; DBS via the rotated
  `"DBS … POSB"` left-margin banner on page 0 (`x0 < 25`: `SBD` + `BSOP`, i.e. `DBS` +
  `POSB` character-reversed by 90° rotation); OCBC via the `"OCBC Bank"` wordmark in the
  upper-right of page 1 (region `x ≥ 0.5·w`, `y ≤ 0.15·h`), family `card` when page-1
  carries `PAYMENT DUE … CREDIT LIMIT`, otherwise `consolidated`. A PDF can match at most one
  detector, so the order of the `if` chain in `detect_type()` is irrelevant. No match → unsupported.
- **OCBC rotated margin banner** bleeds into data lines; must be filtered.
- **DBS sidebar noise** (rotated left-margin text, x0 ≈ 11) filtered per page.
- **Right-aligned numeric columns:** classify by x1 edge, not text order.
- **Dates:** DBS `DD/MM/YYYY` (year from string); OCBC/UOB take year from the
  statement/`Period:` line; UOB uses mixed-case `DD Mon`.
- **OCBC card reconciliation:** `TOTAL AMOUNT DUE` already includes prior balance.
- **DBS multi-page / UOB One multi-section merging:** matched by `(name, account_no)`.
- **ICBC bilingual:** match on English keywords only; Chinese text preserved as-is.

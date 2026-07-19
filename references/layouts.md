# Singapore Bank Statement PDF Layouts

This directory contains bank-specific layout references for the PDF
statement parser. Each bank has its own document structure, coordinate
systems, and parsing quirks.

## Bank Index

| Bank | Statement Families | Reference File |
|------|--------------------|----------------|
| **DBS / POSB** | Consolidated Statement (CASA + FD + SRS + Unit Trust + My Account) | [`dbs-layouts.md`](./dbs-layouts.md) |
| **OCBC** | Bank Account Statement, Credit Card Statement | [`ocbc-layouts.md`](./ocbc-layouts.md) |
| **ICBC** | Bilingual Bank Statement (Current + Fixed Deposit) | [`icbc-layouts.md`](./icbc-layouts.md) |
| **UOB** | Single-Account Transaction, One Multi-Account, Portfolio Summary | [`uob-layouts.md`](./uob-layouts.md) |

## Detection Flow

The detection order (corresponding to `convert_statement.py:detect_type()`) is:

```
UOB → ICBC → DBS → OCBC
```

```mermaid
flowchart LR
    A[PDF Input] --> B{UOB?<br/>Period: line}
    B -- Yes --> C[UOB parser]
    B -- No --> D{ICBC?<br/>Statement Date 结单日期}
    D -- Yes --> E[ICBC parser]
    D -- No --> F{DBS?<br/>rotated "DBS … POSB"<br/>left-margin banner<br/>(page 0, x0<25: SBD + BSOP)}
    F -- Yes --> G[DBS/POSB parser]
    F -- No --> H{OCBC?<br/>"OCBC Bank" wordmark<br/>upper-right of page 1}
    H -- Yes --> I[OCBC parser<br/>card if page-1 has<br/>PAYMENT DUE + CREDIT LIMIT,<br/>else bank]
    H -- No --> J[Unsupported]
```

| Bank | Detection Signature | Priority |
|------|-------------------|----------|
| **UOB** | `Period: <DD Mon YYYY> to <DD Mon YYYY>` | 1st — most unique |
| **ICBC** | `Statement Date 结单日期：YYYY/MM/DD` | 2nd — bilingual header, unique |
| **DBS** | rotated `"DBS … POSB"` left-margin banner on page 0 (`x0 < 25`: `SBD` + `BSOP`, character-reversed by 90° rotation) | 3rd — precise bank-level signal |
| **OCBC** | `"OCBC Bank"` wordmark in page-1 upper-right (region `x ≥ 0.5·w`, `y ≤ 0.15·h`); family `card` if page-1 `PAYMENT DUE … CREDIT LIMIT`, else `bank` | 4th — fallback |

This ordering avoids false positives: UOB's `Period:` line is the most
unambiguous, while OCBC is matched by its `OCBC Bank` wordmark in the top-right
corner of page 1 — a precise signal that no other supported bank emits there,
so it remains a safe fallback.

## Placeholder Reference

The parsers and examples use these placeholders in place of any real
account/card data. Each placeholder has a single canonical matching rule;
anywhere a placeholder appears in this document it should be read as
"the real value that satisfies the rule".

| Placeholder | Bank(s) | Real-world meaning | Matching rule (regex) | Example match |
|-------------|---------|--------------------|-----------------------|---------------|
| `<ACCOUNT_NO>` | OCBC | Bank account number | `Account\s+No\.?\s*(\d+)` | `NNNNNNNNNNNN` |
| `<CARD_TYPE>` | OCBC | Credit card product name (all caps) | `[A-Z][A-Z\s]+` | `OCBC VISA PLATINUM` |
| `<CARD_NO>` | OCBC | Credit card number (printed) | `\d{4}-\d{4}-\d{4}-\d{4}` | `NNNN-NNNN-NNNN-NNNN` |
| `<UOB_ACC_NO>` | UOB | Account number (dashed) | `\b\d{3}-\d{3}-\d{3}-\d{1,3}\b` | `XXX-XXX-XXX-X` |
| `<UOB_PERIOD>` | UOB | Statement period | `Period:\s*(\d{1,2}\s+\w{3}\s+\d{4}\s+to\s+\d{1,2}\s+\w{3}\s+\d{4})` | `Period: 01 Jun 2026 to 30 Jun 2026` |
| `<DBS_ACCT_NO>` | DBS | Account number (various dash formats) | `\b\d{1,4}[-.]\d{1,8}[-.]\d{1,8}([-.]\d{1,8})?\b` | `NNN-N-NNNNNN` |

### Sensitive Number Masking

Sensitive numbers (bank account numbers, time-deposit account numbers,
time-deposit deposit numbers, and credit card numbers) are **masked** in the
rendered Markdown: only the last 4 digits are kept, every other digit is
replaced with `X`. The example matches above show the full un-masked value as
it appears in the source PDF; the script's output shows only the masked form
(e.g. `XXXXXXXXXX3456`).

## Parsing Strategies by Bank

| Bank | Method | Reason |
|------|--------|--------|
| **OCBC** | `extract_words()` + x1 coordinate classification | Right-aligned numeric columns + rotated sidebar noise banner |
| **UOB** | `extract_words()` + x1 coordinate classification | Right-aligned numeric columns |
| **DBS** | `extract_words()` + x1 classification + regex | Mixed approach (sidebar noise, 7-column FD table, multi-currency My Account) |
| **ICBC** | `extract_text()` + regex | Good text layer + bilingual labels, no rotated banners, no tight numeric columns requiring coordinates |

## General parsing approach

1. `pdfplumber.open(path)` → iterate `page.extract_words(use_text_flow=False)`.
2. Cluster words into visual lines by `top` coordinate (tolerance 3 pt).
3. Sort each line's words by `x0` to recover reading order.
4. Detect bank and family first (see **Detection Flow** above).
5. Walk lines using x-edges (not just left-to-right text) to assign each
   numeric token to its column. OCBC, UOB, and DBS can interleave description
   and dates depending on baseline.
6. Normalize dates to ISO `YYYY-MM-DD` — each bank has its own date format:
   - OCBC: all-caps `DD MMM`, year from statement period line
   - UOB: mixed-case `DD Mon`, year from `Period:` end date
   - DBS: `DD/MM/YYYY`, year self-contained
   - ICBC: `YYYY/MM/DD`, year self-contained

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

Detection is **order-independent**: each bank is identified by a unique,
mutually-exclusive signal (see `convert_statement.py:detect_type()`), so exactly
one detector can match a given PDF. The four checks are independent and can be
evaluated in any order — the `UOB → ICBC → DBS → OCBC` sequence in the code is just
a short-circuit convention, not a functional dependency.

```mermaid
flowchart LR
    A[PDF Input]
    A --> B{UOB?<br/>uobgroup.com email<br/>(Contact Us card)}
    A --> D{ICBC?<br/>Statement Date 结单日期}
    A --> F{DBS?<br/>rotated "DBS … POSB"<br/>left-margin banner<br/>(page 0, x0<25: SBD + BSOP)}
    A --> H{OCBC?<br/>"OCBC Bank" wordmark<br/>upper-right of page 1}
    B -- Yes --> C[UOB parser]
    D -- Yes --> E[ICBC parser]
    F -- Yes --> G[DBS/POSB parser]
    H -- Yes --> I[OCBC parser<br/>card if page-1 has<br/>PAYMENT DUE + CREDIT LIMIT,<br/>else bank]
    B -- No --> J[Unsupported]
    D -- No --> J
    F -- No --> J
    H -- No --> J
```

| Bank | Detection Signature |
|------|-------------------|
| **UOB** | `uobgroup.com` email in the "Contact Us" card (any page); `Period:` is consumed by the parser for date extraction, not detection |
| **ICBC** | `Statement Date 结单日期：YYYY/MM/DD` |
| **DBS** | rotated `"DBS … POSB"` left-margin banner on page 0 (`x0 < 25`: `SBD` + `BSOP`, character-reversed by 90° rotation) |
| **OCBC** | `"OCBC Bank"` wordmark in page-1 upper-right (region `x ≥ 0.5·w`, `y ≤ 0.15·h`); family `card` if page-1 `PAYMENT DUE … CREDIT LIMIT`, else `bank` |

Because each signal is bank-exclusive, a PDF can match at most one detector — no
priority or fallback is needed. If none of the four signals is present, the PDF
is reported as unsupported.

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
| `[acct_no] / [fd_acct_no]` | ICBC | ICBC account number (Current / Fixed Deposit) | literal placeholder in source PDF text layer; masked to last 4 digits | `[acct_no]` |

### Sensitive Number Masking

Sensitive numbers (bank account numbers, time-deposit account numbers,
time-deposit deposit numbers, and credit card numbers) are **masked** in the
rendered Markdown: only the last 4 digits are kept, every other digit is
replaced with `X`. The example matches above show the full un-masked value as
it appears in the source PDF; the script's output shows only the masked form
(e.g. `XXXXXXXXXX3456`). Person names that appear in transaction descriptions
are fully replaced with `[NAME]`.

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

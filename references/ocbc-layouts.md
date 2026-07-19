# OCBC Statement PDF Layouts

This reference documents the empirically-determined structure of OCBC PDF
statements needed to parse them reliably. Coordinates are in PDF points
(1 pt = 1/72 inch). They are stable across statements of the same product
family, but may shift slightly between product families — re-measure with
`pdfplumber` `extract_words` if a new product family produces empty/garbled
output.

## Detection Signature

OCBC statements are detected by the `OCBC Bank` wordmark in the upper-right
corner of page 1:

```
OCBC Bank
```

`detect_ocbc()` crops the top-right quadrant of page 1 (right half, top 15%:
`page.crop((0.5*w, 0, w, 0.15*h))`) and matches the substring `OCBC Bank`.
Both OCBC bank and credit-card statements carry this wordmark (beside the
Chulia Street address), and no other supported bank prints `OCBC Bank` in
that region — so it is a precise, bank-level signal that pre-empts the weaker
table-header heuristics.

Family is decided from page-1 content:

* `card` — credit-card statements expose `PAYMENT DUE` / `CREDIT LIMIT` on page 1.
* `consolidated` — everything else (consolidated / savings account statements).

## Placeholder Reference

The parser and examples below use these placeholders in place of any
real account/card data. Each placeholder has a single canonical matching
rule; anywhere a placeholder appears in this document it should be read
as "the real value that satisfies the rule".

| Placeholder    | Real-world meaning                | Matching rule (regex)              | Example match          |
| -------------- | --------------------------------- | ---------------------------------- | ---------------------- |
| `<ACCOUNT_NO>` | OCBC bank account number          | `Account\s+No\.?\s*(\d+)`          | `NNNNNNNNNNNN`         |
| `<CARD_TYPE>`  | Credit card product name (all caps) | `[A-Z][A-Z\s]+`                  | `OCBC VISA PLATINUM` |
| `<CARD_NO>`    | OCBC credit card number (printed) | `\d{4}-\d{4}-\d{4}-\d{4}`         | `NNNN-NNNN-NNNN-NNNN`  |

> Sensitive numbers are masked to the last 4 digits in the rendered Markdown.
> See the central [Sensitive Number Masking](./layouts.md#sensitive-number-masking)
> section for the full masking rules.

## 1. Bank Account Statement ("Statement of Account" / "Consolidated Statement")

Multiple accounts per statement (e.g. STATEMENT SAVINGS, 360 ACCOUNT, TIME DEPOSITS).

### Table header
```
Date Date Description Cheque Withdrawal Deposit Balance
```
- Two date columns at the left: **Transaction Date** then **Value Date**.
- Numeric columns are **right-aligned**; classify by the x1 (right edge) of the word:
  - Cheque:    x1 ∈ [255, 275]
  - Withdrawal: x1 ∈ [370, 390]
  - Deposit:   x1 ∈ [455, 470]
  - Balance:   x1 ∈ [550, 565]
- Date columns: Txn Date x0 ∈ [40, 80]; Value Date x0 ∈ [85, 120].
- Description starts at x0 ≥ 130.

### Section markers
- Account name line: `STATEMENT SAVINGS`, `360 ACCOUNT`, `TIME DEPOSITS`.
- Account number: `Account No. <ACCOUNT_NO>`.
- `BALANCE B/F <amount>` — opening balance; appears **between** the header and
  the first transaction. Capture it, then skip (do NOT treat as a transaction
  and do NOT end the section).
- `BALANCE C/F <amount>` — closing balance; **ends** the transaction section.
- `Total Withdrawals/Deposits`, `Total Interest Paid This Year`, `Average Balance`
  also end a section.

### Multi-line descriptions
A transaction's description may wrap onto continuation lines. Continuation text
has x0 ≥ 130 and the same visual `top` band as the row. Append it to the
current transaction's description.
- **Critical:** OCBC prints a **rotated** bank-name banner in the right margin
  (text reads `detimiL`, `noitaroproC`, `gniknaB`, `esenihC-aesrevO`, `:.oN`,
  `.geR`, `.oC`). These words bleed into the right edge of data lines. Filter
  them out (exact-match set) and ignore any continuation word with x0 ≥ 240
  that is not part of a real description.

### Time deposits
Row format: `<account_no> <deposit_no> <rate%> <DD MMM YYYY maturity> <balance>`
on a single line under the `TIME DEPOSITS` header. Treat as a separate table.

### Dates
Month abbreviations (JUN, MAY). Year is on the statement date line
(e.g. `30 JUN 2026`); assume all rows share that year.

---

## 2. Credit Card Statement

Single card per statement. Header:
```
TRANSACTION DATE DESCRIPTION AMOUNT (SGD)
```

### Transaction line
- Date token `DD/MM` at x0 ∈ [55, 80] (single token, e.g. `25/12`).
- Description words at x0 ≥ 199.
- Amount is the **rightmost** numeric token, right edge x1 ∈ [510, 550].
- **Credits** (payments, cash rebates) are shown parenthesized: `(XXX.XX)`.
  Plain numbers are charges. Keep parentheses in display; treat as negative for
  math.

### Foreign currency
A foreign-currency transaction is followed by a continuation line. The
currency code and original amount (e.g. `JPY XX,XXX.XX`) are extracted into a
dedicated `Currency / Amount` column in the output table. The raw continuation
text is parsed but not kept verbatim.

Example continuation line:
```
FOREIGN CURRENCY JPY XX,XXX.XX
```

### Trailing lines (not transactions)
- `LAST MONTH'S BALANCE <amt>`
- `SUBTOTAL <amt>`, `TOTAL <amt>`, `TOTAL AMOUNT DUE <amt>`
- `CASH REBATE` is a real transaction (parenthesized) — keep it.

### Sidebar noise
Same rotated banner words at the right margin (`detimiL`, etc.). Exclude any
description word with x0 > 480.

### Summary / metadata (page 1)
Regex over the concatenated page text:
```
STATEMENT DATE  PAYMENT DUE DATE  TOTAL CREDIT LIMIT  TOTAL AVAILABLE CREDIT LIMIT  TOTAL MINIMUM DUE
01-01-2026       01-02-2026         S$XX,XXX            S$XX,XXX.XX                    S$XX.XX
```
Capture with:
```
(\d{2}-\d{2}-\d{4})\s+(\d{2}-\d{2}-\d{4})\s+S\$([\d,]+)\s+S\$([\d,]+\.\d{2})\s+S\$([\d,]+\.\d{2})
```
Card name + number:
```
<CARD_TYPE>
<all-caps line>
<CARD_NO>
```
The all-caps line between the card name and the card number is not captured. The card-name line is whatever all-caps line sits directly above the cardholder's printed name (so the parser works for any product, OCBC-branded or not, e.g. `VISA PLATINUM`, `MASTERCARD WORLD`).

### Reconciliation
`Total Amount Due = Last Month's Balance + Σcharges − Σcredits`.
The TOTAL/TOTAL AMOUNT DUE line already includes the carried-over balance, so a
naive Σ(charges) − Σ(credits) will understate it by exactly the opening balance.

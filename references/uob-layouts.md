# UOB Statement PDF Layouts

This reference documents the empirically-determined structure of UOB PDF
statements needed to parse them reliably. Coordinates are in PDF points
(1 pt = 1/72 inch). They are stable across statements of the same product
family, but may shift slightly between product families — re-measure with
`pdfplumber` `extract_words` if a new product family produces empty/garbled
output.

## Detection Signature

UOB statements are detected by a `uobgroup.com` email address in the
"Contact Us" card (any page):

```
<anything>@uobgroup.com
```

`detect_uob()` scans the full document text for an email of the form
`[A-Za-z0-9._%+-]+@uobgroup.com` (case-insensitive). No other supported bank
emits that domain, so it is a precise, bank-level signal. (The `Period:` line
is consumed for date extraction, not for detection.)

Family is decided from the document content:

* `portfolio` — statements with no `Account Transaction Details` block (a portfolio summary, not a transaction listing).
* `one` — multi-account statements that head each transaction section with the `One Account` label.
* `txn` — everything else (single-account transaction-style statements).

## Placeholder Reference

The parser and examples below use these placeholders in place of any
real account/card data. Each placeholder has a single canonical matching
rule; anywhere a placeholder appears in this document it should be read
as "the real value that satisfies the rule".

| Placeholder    | Real-world meaning                  | Matching rule (regex)                | Example match                                |
| -------------- | ----------------------------------- | ------------------------------------ | -------------------------------------------- |
| `<UOB_ACC_NO>` | UOB account number (dashed)         | `\b\d{3}-\d{3}-\d{3}-\d{1,3}\b`     | `XXX-XXX-XXX-X`                              |
| `<UOB_PERIOD>` | UOB statement period                | `Period:\s*(\d{1,2}\s+\w{3}\s+\d{4}\s+to\s+\d{1,2}\s+\w{3}\s+\d{4})` | `Period: 01 Jun 2026 to 30 Jun 2026` |

> Sensitive numbers are masked to the last 4 digits in the rendered Markdown.
> See the central [Sensitive Number Masking](./layouts.md#sensitive-number-masking)
> section for the full masking rules.

## 1. UOB Single-Account Transaction Statement (eStatement\_\<acct\>\_\<YYYYMM\>.pdf)

A `Period: <start> to <end>` line is present; it is consumed by the parser for
period/date extraction (not used for bank detection). One account
per file; the transactions are on the second page under the
`Account Transaction Details` heading.

### Page 0 — header
```
CUSTOMER NAME
123 SAMPLE ROAD ...
SINGAPORE <postal>
Statement of Account
Period: 01 Jun 2026 to 30 Jun 2026
Account Overview as at 30 Jun 2026
...
```
The `Period:` line is detected by:
```
Period:\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+to\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})
```
UOB does **not** print a rotated margin banner — no `SIDEBAR_NOISE` filter is
required for UOB.

### Page 1 — transactions section
```
Account Transaction Details
UOB Stash Account XXX-XXX-XXX-X
Date Description Withdrawals Deposits Balance
SGD SGD SGD
01 Jun BALANCE B/F XX,XXX.XX
02 Jun Bonus Interest XXX.XX XX,XXX.XX
02 Jun Funds Transfer XXX.XX XX,XXX.XX
         mBK-XXXXXXXXXX
...
30 Jun Interest Credit XX.XX XX,XXX.XX
Total X,XXX.XX X,XXX.XX XX,XXX.XX
```

### Section markers
- The line directly below the section header (NOT above) carries
  `<Account Name> <UOB_ACC_NO>`. The account name is the leftmost contiguous
  run of words on that line; the dashed account number is matched by
  `<UOB_ACC_NO>`.
- The line directly above the account-name line is the column-header line
  (`Date Description Withdrawals Deposits Balance`); below that is the
  `SGD SGD SGD` currency sub-header. These are skipped, not transactions.
- A `BALANCE B/F <amount>` row appears as the first row of the section. It
  does **not** have a date column — it is captured as the opening balance.
- A `Total <w> <d> <balance>` row ends the section; do not emit it as a
  transaction.
- `End of Transaction Details` also ends the section.

### Transaction line
- Date tokens: `DD` at x0 ∈ [52, 62] and `Mon` at x0 ∈ [63, 80] (3-letter,
  mixed case, e.g. `Jun`).
- Description: x0 ∈ [120, 200] (anything to the right of x0 > 200 is the
  rotated/garbage band, which UOB does not emit but the parser still
  defends against).
- Numeric columns, classified by x1 (right edge):
  - Withdrawals: x1 ∈ [355, 390]
  - Deposits:    x1 ∈ [430, 470]
  - Balance:     x1 ∈ [500, 550]
- The numeric row in the test PDF also includes the `SGD` sub-header
  immediately below the column header; the parser skips it by detecting
  that all three tokens are the same `SGD` word.

### Multi-line descriptions
Continuation lines have x0 ∈ [120, 200] and no date tokens. They are
appended (space-joined) to the current pending transaction's description.
Common continuations:
- `mBK-<digits>` — UOB mobile-banking reference
- `OTHR Other` — counter-party marker
- `<Cardholder name> Other` — payee/owner tag

### Date normalization
- Date string is `<DD> <Mon>` (mixed case).
- Year is taken from the `Period:` end date.
- Parsed with `%d %b %Y` (or `MONTH_MAP` for locale independence).
- Output: ISO `YYYY-MM-DD`.

### Masking
UOB account numbers are dashed (`<UOB_ACC_NO>` → e.g. `XXX-XXX-XXX-X`).
`mask_id` strips non-digits and keeps the last 4 visible:
`XXX-XXX-XXX-X` → `XXXXXXX0000`.

---

## 2. UOB Multi-Account Portfolio Summary (eStatement\_\<YYYYMM\>.pdf)

A `Period: <start> to <end>` line is present; the parser uses it for
period/date extraction (not bank detection). The
`Portfolio Overview` and `Account Overview` variants share the same structure;
they differ only in the section heading. The deposits and investments tables
have no per-transaction rows — they are summary/holdings only.

### Page 0 — header + Portfolio Overview + Deposits
```
Statement of Account
Period: 01 Jun 2026 to 30 Jun 2026
Portfolio Overview as at 30 Jun 2026
Amount (SGD)
Deposits XX,XXX.XX
Investments XX,XXX.XX
Structured Investments 0.00
Total Deposits and Investments1 XX,XXX.XX
Loans (Total Outstanding Loan Amount) 0.00
...
Deposits
Currency Credit Line Interest Earned2 Interest Charged2 Balance
Savings
UOB Stash Account S G D 0.00 XX.XX 0.00 XX,XXX.XX
XXX-XXX-XXX-X
Locked Amount4 as of 30 Jun 2026 is X,XXX.XX
Total (SGD) XX,XXX.XX
Grand Total (SGD Equivalent² ) XX,XXX.XX
```

### Portfolio Overview
Parsed line by line. Each row is `<Label>  <Amount>` where the amount is a
`\d{1,3}(,\d{3})*\.\d{2}` token at the right edge. The label list:
- `Deposits`
- `Investments`
- `Structured Investments`
- `Total Deposits and Investments1` (the trailing `1` is a footnote
  reference: _¹ Excludes Deposits and Investments in CPFIS and SRS
  Accounts._)
- `Loans (Total Outstanding Loan Amount)`

### Deposits table (per-account summary)
- Section starts on a line whose stripped text equals `Deposits`.
- The line directly above each dashed account-number line carries the
  account name (in the leftmost x-band) **and** the numeric data (Currency
  S G D | Credit Line | Interest Earned | Interest Charged | Balance) in
  the right-side bands.
- Account name: take the leftmost contiguous run of words on the previous
  line where the gap between consecutive words is ≤ 20pt. This excludes the
  numeric data, which starts at x0 ≈ 197.
- Account number: the next line, matched by `<UOB_ACC_NO>`.
- Numeric columns, classified by x1 (right edge):
  - Credit Line:       x1 ∈ [280, 305]
  - Interest Earned:   x1 ∈ [355, 390]
  - Interest Charged:  x1 ∈ [440, 470]
  - Balance:           x1 ∈ [500, 550]
- The `S G D` currency code is printed as 3 single-letter tokens at
  x0 ∈ [195, 215] (sometimes joined to `SGD` as a single word on later
  lines); collapse them to `SGD` when all three are present.
- `Locked Amount<digit>? as of <date> is <amount>` — attaches to the most
  recent deposit row.
- `Total (SGD) <amount>` and `Grand Total (SGD Equivalent) <amount>` rows
  end the section; they are rendered as bold rows. The trailing `²` in
  `Grand Total (SGD Equivalent²)` is a footnote reference:
  _² Rates against Singapore Dollar as at <date>. Rates in the table are
  for reference only._

### Investments table (per-fund holdings)
- Section starts on a line whose stripped text equals `Investments` (or begins
  with `Investments `).
- The unit-trust account that owns these holdings is named in the header that
  immediately follows the `Investments` marker:
  - `Unit Trust Account <UOB_ACC_NO>` on the first page
  - `Unit Trust Account <UOB_ACC_NO> (continued)` on continuation pages
  - or, combined onto the `Investments` line itself:
    `Investments Unit Trust Account <UOB_ACC_NO>`
  The parser captures `{name, account_no}` from this header and builds a
  first-class `UNIT_TRUST` account owning the fund rows. Fund data rows never
  carry a dashed account number, so the match is header-only and safe across
  page continuation.
- The header band is `Units Currency Indicative Market` /
  `Price Valuation` (two-line header) — these are skipped.
- A data row looks like:
  ```
  <Fund Name words> <units> <ccy> <price> <valuation>
  ```
  where:
  - Fund name: leftmost contiguous run, including any class suffixes like
    `CLASS A SGD DIS`. (The class name may include a currency code that
    matches the column currency; that is preserved in the name.)
  - Units:    `\d+(,\d{3})*\.\d{4}` at x1 ∈ [300, 360]
  - Currency: `[A-Z]{3}` at x0 ∈ [350, 390]
  - Price:    `\d+\.\d{4}` at x1 ∈ [430, 470]
  - Valuation: `\d+(,\d{3})*\.\d{2,4}` at x1 ∈ [500, 550]
- Continuations on subsequent pages: pages 2+ repeat the `Investments`
  heading and continue with new data rows. The parser's `in_investments`
  flag persists across pages so continuation rows are captured.
- `Total (SGD) <amount>` and `Grand Total (SGD Equivalent) <amount>` rows
  end the section; they are rendered as bold rows. The trailing `²` in
  `Grand Total (SGD Equivalent²)` is a footnote reference:
  _² Rates against Singapore Dollar as at <date>. Rates in the table are
  for reference only._

### Foreign Exchange, Gold, Silver Reference Rates table

On the final page of the portfolio PDF (`Page 3 of 3`), a two-column
reference-rates table is present:

```text
Foreign Exchange, Gold, Silver
3Rates against Singapore Dollar as at 30 Jun 2026. Rates in the table are for reference only.
Code FX, Gold, Silver Unit FX/Price   Code FX, Gold, Silver Unit FX/Price
USD      US DOLLAR          1   1.2827    CHF      SWISS FRANC     100 158.3500
GBP      BRITISH POUND      1   1.6912    JPY      JAPANESE YEN    100   0.7809
EUR      EURO               1   1.4562    HKD HONG KONG DOLLAR    100  16.3310
AUD AUSTRALIAN DOLLAR       1   0.8762    CNH CHINESE RENMINBI    100  18.8400
                                                             (OFF-SHORE)
CAD CANADIAN DOLLAR         1   0.8996    Gold   Savings Account   1 GM 167.3000
NZD NEW ZEALAND DOLLAR      1   0.7227    Silver Savings Account  1 OZ  75.6500
```

The parser extracts all currencies/Gold/Silver rows and discards the two-column
layout. Continuation lines (e.g. `(OFF-SHORE)`) are appended to the previous
entry's name. The rendered Markdown is a single 4-column table under the
`## Foreign Exchange, Gold, Silver Reference Rates` heading.

### Multi-page handling
Section flags (`in_deposits`, `in_investments`) are page-local by default
but are promoted to function-scope in the script so multi-page tables are
parsed in full.

---

## 3. UOB One Multi-Account Transaction Statement (eStatement\_\<YYYYMM\>\_\<acct\>.pdf)

A `Period: <start> to <end>` line is present; the parser uses it for
period/date extraction (not bank detection). The statement
contains a page-zero Account Overview followed by many `Account Transaction
Details` sections (one per currency sub-account). Each section has its own
`BALANCE B/F`, transaction rows, and `Total` line. The same account may span
multiple pages (e.g. the One Account continues from page 2 to page 3, or a
FX+ account continues from page 4 to page 5).

### Page 0 — Account Overview

```
Statement of Account
Period: 01 Jun 2026 to 30 Jun 2026
Account Overview as at 30 Jun 2026
Amount (SGD)
Deposits X,XXX.XX
...
```

The `Deposits <amount>` line is parsed as the **Account Overview** total
(e.g. `X,XXX.XX`).  This value is rendered in the `## Account Overview` table.

Immediately below the Account Overview is a **Deposits** summary table with one
row per sub-account:

```
Deposits
Currency Credit Line Interest Earned2 Interest Charged2 Balance
Current
One Account SGD 0.00 X.XX - X,XXX.XX
NNN-NNN-NNN-N
Locked Amount4 as of 30 Jun 2026 is X,XXX.XX
FX+
NNN-NNN-NNN-N AUD 0.00 0.00 - 0.00
Locked Amount4 as of 30 Jun 2026 is 0.00
...
Total (SGD) X,XXX.XX
Grand Total (SGD Equivalent *) X,XXX.XX
```

Deposit-row layout:
- **One Account style**: the data line (account name + currency + amounts) sits
  above the account-number-only line (e.g. `NNN-NNN-NNN-N`).
- **FX+ style**: the account name (`FX+`) is on its own line; the data line
  starts with the dashed account number, followed by currency and amounts.
- **Footnotes**: column headers carry `2` (Interest Earned/Charged for 2026);
  `Locked Amount4` carries footnote `4`; the Grand Total carries `*` (Rates
  against SGD — shared with the FX rates table).

The Deposits summary spans page 0 and page 1 (continued); per-currency `Total
(<ccy>)` and `Grand Total (SGD Equivalent *)` rows are captured and rendered.

### Page 6 (last page) — Foreign Exchange, Gold, Silver Reference Rates

The last page carries a `Foreign Exchange, Gold, Silver` table in the same
two-entries-per-line format used by the portfolio summary:

```
Foreign Exchange, Gold, Silver
*Rates against Singapore Dollar as at 30 Jun 2026. Rates in the table are for reference only.
Code FX, Gold, Silver Unit FX/Price Code FX, Gold, Silver Unit FX/Price
USD US DOLLAR 1 1.2827 CHF SWISS FRANC 100 158.3500
...
Important Information
```

This is parsed by the shared `_parse_uob_fx_rates` helper (same as the
portfolio parser).  The `*` footnote text is "Rates against Singapore Dollar as
at <date>. Rates in the table are for reference only."

### Transaction pages

Each transaction page begins with `Account Transaction Details` and contains
one or more account sections. The first section on each page is preceded by
that heading; subsequent sections are delimited by account-name / dashed-
account-number lines.

```
Account Transaction Details
One Account XXX-XXX-XXX-X
Date Description Withdrawals Deposits Balance
SGD SGD SGD
01 Jun BALANCE B/F X,XXX.XX
02 Jun PAYNOW-FAST XX.XX X,XXX.XX
     PAYNOW OTHR
     PERSON A
     From PERSON A
...
30 Jun Interest Credit 0.10 X,XXX.XX
Total X,XXX.XX X,XXX.XX X,XXX.XX

FX+ XXX-XXX-XXX-X
Withdrawals Deposits Balance
Date Description
AUD AUD AUD
01 Jun BALANCE B/F 0.00
Total 0.00
```

### Section markers

- **Section start**: a line containing an account name (`One Account`, `FX+`, etc.)
  followed by a dashed account number (`<UOB_ACC_NO>`). On continuation pages
  the same line may end with `(continued)`; this suffix is stripped when
  rendering.
- **Header band**: column headers are split across two visual lines
  (`Withdrawals Deposits Balance` and `Date Description`), followed by a
  three-token currency line (`SGD SGD SGD`, `AUD AUD AUD`, etc.). The parser
  skips everything until the first `BALANCE B/F` or date row.
- **Currency sub-header**: the three repeated currency tokens (e.g. `SGD SGD SGD`)
  are used to infer the section currency. The currency is appended to the
  account name in the rendered Markdown (e.g. `One Account (SGD)`).
- **BALANCE B/F**: the opening-balance row, optionally prefixed with a date
  token (`01 Jun BALANCE B/F X,XXX.XX`).
- **Total**: the section ends with a `Total <w> <d> <balance>` row. The parser
  records these values; the renderer uses them for the `Total
  Withdrawals/Deposits` row and for the closing balance of sections with no
  transaction rows.
- **End of Transaction Details**: also ends the section.

### Transaction line

Date tokens and numeric column x-bands are the same as the single-account
transaction statement:

- Date tokens: `DD` at x0 ∈ [52, 62] and `Mon` at x0 ∈ [63, 80].
- Description: x0 ∈ [120, 240].
- Numeric columns, classified by x1:
  - Withdrawals: x1 ∈ [355, 390]
  - Deposits:    x1 ∈ [430, 470]
  - Balance:     x1 ∈ [500, 550]

### Multi-page continuation

The parser tracks the last account number. If a later section on the same or a
subsequent page has the same account number, its transactions are appended to
that account's list. The opening balance is taken from the first occurrence; the
`Total` line is updated from the last occurrence.

### Masking

UOB account numbers are masked by `mask_id` as in the single-account parser. The
output shows only the last 4 digits of each account number.


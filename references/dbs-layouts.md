# DBS/POSB Consolidated Statement Layout Reference

> Detection order: **3rd** (UOB → ICBC → **DBS** → OCBC)

Column x-positions (PDF points) measured from a sample DBS/POSB consolidated statement PDF.

## Statement pages (7 total)

| Page | Content |
|------|---------|
| 0 | Account Summary — Deposits overview (CASA + Fixed Deposits) |
| 1 | Supplementary Retirement Scheme — Account, Contributions, Unit Trusts |
| 2 | Transaction Details — DBS Savings Plus Account |
| 3 | Transaction Details — DBS Savings Plus (cont'd), My Account (SGD + USD), Fixed Deposit |
| 4 | Transaction Details — Fixed Deposit (cont'd), SRS Account |
| 5 | Messages For You (informational, not parsed) |
| 6 | Terms / QR code (informational, not parsed) |

## Detection signature

```
"Consolidated Statement" + "Account Summary"   (without UOB "Period:" or OCBC "TRANSACTION DATE")
```

Checked in `detect_type()` after UOB but before OCBC.

## Account Summary (page 0)

### Account | Account No. | Balance (Base Currency) | Balance (SGD Equivalent)

| Column | x0 range | x1 range | Notes |
|--------|----------|----------|-------|
| Account name | 38–180 | — | Left-hand side |
| Account No. | 265–350 | — | DBS format: `NNN-N-NNNNNN`, `NNN-NNNNNN-N`, `NNNN-NNNNNNNN-N`, `NNNN-NNNNNN-N-NNN` |
| Balance (Base) | — | 440–485 | 2dp bank number |
| Balance (SGD Eq.) | — | 510–560 | 2dp bank number |
| Currency | ~390–425 | — | 3-letter code (SGD, USD) |

### Fixed Deposit vs CASA split

DBS prints a second sub-header before the FD table:
```
Fixed Deposit   Total: SGD Equivalent X.XX
```
Detection: if a line contains both `"Fixed Deposit"` and `"Total:"`, switch context to FD.

## Transaction Details (pages 2–4)

### Transaction table: Date | Description | Withdrawal (-) | Deposit (+) | Balance

| Column | x0 range | x1 range | Notes |
|--------|----------|----------|-------|
| Date | 40–92 | — | `DD/MM/YYYY` format |
| Description | ≥100 | <350 | Multi-line via continuation |
| Withdrawal (-) | — | 350–400 | 2dp bank number |
| Deposit (+) | — | 425–480 | 2dp bank number |
| Balance | — | 505–560 | 2dp bank number |

### Section markers

Account sections start with `"<AccountName> Account No. <dashed-number>"` on one line.
Transaction table header: `"Date Description Withdrawal (-) Deposit (+) Balance (SGD)"`.

| Section | Detection |
|---------|-----------|
| DBS Savings Plus Account | `"DBS Savings Plus" in text and "Account No." in text` |
| My Account | `"My Account" in text and "Account No." in text` |
| Fixed Deposit | `"Fixed Deposit" in text and "Account No." in text` |
| SRS Account | `"Supplementary Retirement Scheme" in text and "Account No." in text` |

### My Account multi-currency

My Account may contain SGD and USD sub-sections delimited by `"CURRENCY: SINGAPORE DOLLAR"` /
`"CURRENCY: UNITED STATES DOLLAR"`. Each sub-section has its own `Balance Brought Forward`,
`Total Balance Carried Forward`, and transaction rows.

For USD sub-sections, the closing line is `"Indicative in SGD @ <rate> <amount>"`.

### Key rows

| Row | Detection |
|-----|-----------|
| Balance Brought Forward | `re.search(r"Balance\s+Brought\s+Forward\s+(?:([A-Z]{3})\s+)?([\d,]+\.\d{2})", text)` |
| Balance Carried Forward | `"Balance Carried Forward" in text` |
| Total Balance Carried Forward | `"Total Balance Carried Forward" in text` (3-column variant with withdrawal/deposit/balance totals) |
| Decorative `"4 4 4 4 4"` | Skip — single-character numeral rows at x positions matching column centers |

### Continuation lines

Description continuation lines appear at x0 ≥ 100 without date tokens.
Common patterns include:
- `DBS:I-BANK` — Internet banking channel
- `OTHR TRANSFER XXXX...` — Transfer reference
- `PAYNOW TRANSFER XXXXXXX` — PayNow reference
- `TO: EZ WASH LAUNDRY` — Payee name
- `QZXXXXXXXXXXXXXXXXXX` — Transaction ID
- `NETS QR PAYMENT XXXX...` — NETS payment reference

## Fixed Deposit transactions (pages 3–4)

### FD table: Date | Deposit No. | Period | Description | Interest Amt | Principal | Interest Rate (% p.a.)

| Column | x0 range | x1 range | Notes |
|--------|----------|----------|-------|
| Date | 40–92 | — | `DD/MM/YYYY` |
| Deposit No. | 95–170 | — | 12-digit number (e.g. `NNNNNNNNNNNN`) |
| Period | 185–290 | — | `DD/MM/YYYY - DD/MM/YYYY` |
| Description | ≥295 | <500 | Multi-line continuation |
| Interest Amt | — | 420–460 | 2dp bank number |
| Principal | — | 505–560 | 2dp bank number |
| Interest Rate | — | 510–560 | 4–6dp number on continuation line |

**Note:** Interest Rate and Principal share overlapping x-bands on the right edge.
On main (dated) rows the rightmost value is always Principal; Interest Rate only
appears on continuation (sub-row) lines and uses 4–6 decimal places.

### FD sub-rows

DBS FD entries may have sub-rows that begin with the Deposit No. (not a date)
and represent a separate action on the same deposit (e.g. Rollover, Premature
Withdrawal). Sub-row continuation lines start with `"365/365"` (day-count basis).

| Row type | Example (amounts masked) |
|----------|---------|
| Main row (with date) | `DD/MM/YYYY NNNNNNNNNNNN DD/MM/YYYY - DD/MM/YYYY Renew Principal & XXX.XX XX,XXX.XX` |
| Continuation | `365/365 Interest XX.XXXXXX` |
| Sub-row (no date) | `NNNNNNNNNNNN DD/MM/YYYY - DD/MM/YYYY New Rollover Deposit XX.XX XX,XXX.XX` |
| Sub-row continuation | `365/365 XX.XXXXXX` |

## Supplementary Retirement Scheme (page 1)

### SRS Summary

```yaml
Supplementary Retirement Scheme Account Total: SGD XX,XXX.XX
Account  Account No.  Cash Balance (SGD)
SRS Account  NNNN-NNNNNN-N-NNN  XX.XX
```

### Contribution Details

```yaml
Contribution Details  Limit  Amount (SGD)
Max Contribution Amount  XX,XXX.XX
Total Contribution Made to Date  X,XXX.XX
Balance Contribution Limit  XX,XXX.XX
```

### Unit Trusts table: Name | Free Qty | Total Cost (SGD) | Market Value (SGD) | Unrealised P/L (SGD)

| Column | x1 range | Notes |
|--------|----------|-------|
| Free Qty | 200–255 | 4dp number, NOT matched by `is_bank_num()` |
| Total Cost | 330–375 | 2dp bank number |
| Market Value | 420–465 | 2dp bank number |
| Unrealised P/L | 520–560 | 2dp bank number |

## Sidebar noise

DBS prints rotated text in the left margin (x0 ≈ 11) on every page footer.
The following words must be filtered:

```
.oN, geR, ziB, BSOP, X-XXXXXXXX-RM, :oN, TSG, .geR, .oC, SBD,
)XXXX/XX(, XXXXXXXXX, XXXXXXXXX, XXXXXXXXXXXX,
XXXX_XXXX_XXXX_XXXX_XXXXXXXXXXXXXXX_XXXXX
```

Filtering is done by `_filter_line_words()`: remove words with `x0 < 20` and
words whose text is in `DBS_SIDEBAR_NOISE`. Also filter CID font artefacts
matching `(cid:\d+)`.

## Date format

DBS uses `DD/MM/YYYY` throughout (e.g. `02/06/2026`), converted to ISO
`YYYY-MM-DD` during normalization. Year is implicit from the date string
itself — no external year inference needed (unlike OCBC/UOB).

## Account number formats

| Account type | Format | Regex group |
|-------------|--------|-------------|
| Savings Plus | NNN-N-NNNNNN | `\d{1,4}-\d{1,8}-\d{1,8}` |
| My Account | NNN-NNNNNN-N | `\d{1,4}-\d{1,8}-\d{1,8}` |
| Fixed Deposit | NNNN-NNNNNNNN-N | `\d{1,4}-\d{1,8}-\d{1,8}` |
| SRS Account | NNNN-NNNNNN-N-NNN | `\d{1,4}-\d{1,8}-\d{1,8}-\d{1,8}` |

All matched by `DBS_ACCT_NO_RE = r"\b\d{1,4}[-.]\d{1,8}[-.]\d{1,8}([-.]\d{1,8})?\b"`.

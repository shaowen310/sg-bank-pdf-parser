# ICBC (Industrial and Commercial Bank of China) — Statement Layout Reference

## Detection Signature

ICBC statements are detected by the unique bilingual header line:

```
Statement Date 结单日期：YYYY/MM/DD
```

This signature appears on page 1. It is a unique, bank-level signal — no other supported bank prints the bilingual `Statement Date 结单日期` header.

## Page Structure

### Page 1

```
┌──────────────────────────────────────────────────────────────┐
│  Statement Date 结单日期：2026/06/30                          │
│  [Customer Name]                                              │
│  [Address lines...]                                           │
│  [Singapore XXXXXX]                                           │
│                                                               │
│  ── Account Summary 帐户摘要 ──                               │
│                                                               │
│  Current Account 往来帐户                                     │
│  ┌──────────┬──────────────────────┬─────┬──────────┬───────┐ │
│  │ A/c Type │ Account No./Card No. │ CCY │ Balance  │ A/C   │ │
│  │          │                      │     │          │ Code  │ │
│  ├──────────┼──────────────────────┼─────┼──────────┼───────┤ │
│  │ C/A      │ [acct_no]            │ CNY │ XXX,XX   │       │ │
│  │ C/A      │ [acct_no]            │ USD │ X,XX     │       │ │
│  │ C/A      │ [acct_no]            │ SGD │ XX,XXX   │       │ │
│  └──────────┴──────────────────────┴─────┴──────────┴───────┘ │
│                                                               │
│  Fixed Deposit Account 定期存款帐户                           │
│  ┌──────────────────────┬─────┬──────────┬──────────┬───────┐ │
│  │ Account No./Card No. │ CCY │ Balance  │ Deal     │ A/C   │ │
│  │                      │     │          │ Status   │ Code  │ │
│  ├──────────────────────┼─────┼──────────┼──────────┼───────┤ │
│  │ [acct_no]            │ SGD │ X,XX     │ Matured  │       │ │
│  │ [acct_no]            │ CNY │ X,XXX    │ Normal   │       │ │
│  │ ...                  │ ... │ ...      │ ...      │       │ │
│  └──────────────────────┴─────┴──────────┴──────────┴───────┘ │
│                                                               │
│  ── Transaction Record 交易记录 ──                             │
│                                                               │
│  Current Account 往来帐户                                     │
│  Account No.帐户号码 [acct_no]                                │
│  ┌──────────┬──────────┬─────┬──────────┬──────────┬────────┐ │
│  │ Date     │ Remark   │ CCY │ Deposit  │Withdrawal│Balance │ │
│  │          │          │     │ Amount   │ Amount   │        │ │
│  ├──────────┼──────────┼─────┼──────────┼──────────┼────────┤ │
│  │ YYYY/MM/ │ B/F      │ CNY │          │          │ XXX.XX │ │
│  │ DD       │          │     │          │          │        │ │
│  │ YYYY/MM/ │ INTEREST │ CNY │ X.XX     │          │ XXX.XX │ │
│  │ DD       │ INT      │     │          │          │        │ │
│  │          │ Total Dr.│ CNY │          │ X.XX     │        │ │
│  │          │ Total Cr.│ CNY │ X.XX     │          │        │ │
│  │ ...      │          │ ... │ ...      │          │        │ │
│  └──────────┴──────────┴─────┴──────────┴──────────┴────────┘ │
│                                                               │
│  Fixed Deposit Account 定期存款帐户                           │
│  Account No.帐户号码 [fd_acct_no]                             │
│  ┌──────┬──────────┬──────────┬─────┬─────────┬─────┬───┬────┐ │
│  │SeqNo │Deal Date │Value Date│ CCY │  Amount │Period│...│Bal │ │
│  ├──────┼──────────┼──────────┼─────┼─────────┼─────┼───┼────┤ │
│  │ 0002 │YYYY/MM/DD│YYYY/MM/DD│ SGD │±XX,XXX │3mo  │...│X.XX│ │
│  └──────┴──────────┴──────────┴─────┴─────────┴─────┴───┴────┘ │
│                                                               │
│  ── Reminders 业务提醒 ──                                     │
│  [Category / Relevant A/C / Particulars]                      │
│                                                               │
│  ── Note 注意事项 ──                                          │
│  1. ...                                                       │
│                                                               │
│  Page 1 of 2 XXXXXXXXXXXXXXX                                 │
└──────────────────────────────────────────────────────────────┘
```

### Page 2

Contains continuation of notes (bilingual English/Chinese) and the Deposit Insurance Scheme section. No transaction data.

## Text Anchor Keywords

| Anchor | Purpose |
|--------|---------|
| `Statement Date 结单日期` | Bank detection + statement date extraction |
| `Account Summary 帐户摘要` | Start of summary section |
| `Current Account 往来帐户` | Current Account summary sub-section |
| `Fixed Deposit Account 定期存款帐户` | Fixed Deposit summary + transaction sub-section |
| `Transaction Record 交易记录` | Start of transaction section |
| `Account No.帐户号码` | Account number extraction |
| `B/F` | Opening balance (Brought Forward) row marker |
| `INTERESTINT` | Interest credit in Current Account |
| `TIME DEPO AUTO ROLLOVER` | Multi-line remark start for FD rollover transactions |
| `Total Dr.` | Total Debit summary row per currency group |
| `Total Cr.` | Total Credit summary row per currency group |
| `Reminders` or `业务提醒` | Start of reminder messages |
| `Note ` | Start of notes section |
| `Deposit Insurance` | Start of deposit insurance disclosure |

## Transaction Table Column Structure

### Current Account (6 columns)

| Column | Pattern | Notes |
|--------|---------|-------|
| Date | `YYYY/MM/DD` | ISO-style date |
| Remark | Any text | May span multiple lines (TIME DEPO) |
| CCY | `CNY`, `SGD`, or `USD` | Currency code |
| Deposit Amount | `X,XXX.XX` or empty | Bank-num format |
| Withdrawal Amount | `X,XXX.XX` or empty | Bank-num format |
| Balance | `X,XXX.XX` | Bank-num format |

### Fixed Deposit (10 columns)

| Column | Pattern | Notes |
|--------|---------|-------|
| Seq No. | `\d{7}` | Sequence number |
| Deal Date | `YYYY/MM/DD` | Transaction date |
| Value Date | `YYYY/MM/DD` | Value date |
| CCY | `CNY`, `SGD`, `USD` | Currency |
| Amount | `[+-]X,XXX.XX` | Deposit (+) or withdrawal (-) |
| Period | e.g. `3month` | Deposit period |
| Maturity Date | `YYYY/MM/DD` | Maturity date |
| Rate | e.g. `1.25%` | Interest rate |
| Interest Amount | `XX.XX` | Interest earned |
| Balance | `X.XX` | Running balance |
| Remark | e.g. `CLO` | Closure/status code |

## Multi-line Description Merging

### TIME DEPO AUTO ROLLOVER Pattern

ICBC prints TIME DEPO AUTO ROLLOVER transaction descriptions across multiple lines:

```
TIME DEPO AUTO ROLLOVERTRFXXXX     ← remark line (no date)
2026/06/30 SGD 10,000.00 10,200.86 ← transaction data line (with date)
XXXXXXX                             ← remark continuation
```

The parser collects non-date lines as pending remark text and merges them into the next transaction that has a date line. Post-transaction continuation lines (e.g. `XXXXXXX`) are appended to the current transaction's remark.

**Note**: `extract_text()` may merge adjacent words (e.g. `ROLLOVERTRFXXXX` instead of `ROLLOVER TRF XXXX`) due to tight PDF spacing. This is a cosmetic issue and does not affect parsing correctness.

## Currency Grouping

Transactions are grouped by currency (CNY, SGD, USD). Each group has:
1. A `B/F` opening balance row
2. Regular transaction rows
3. `Total Dr.` and `Total Cr.` summary rows

Currency switches are detected by the `B/F` + different CCY pattern.

## Parsing Strategy

The parser uses `page.extract_text()` (not word coordinates) because:
- ICBC PDFs have good text layer quality
- Bilingual text (English/Chinese) is extracted in reasonable line order
- No rotated margin banners (unlike OCBC)
- No tightly-spaced numeric columns that require x-coordinate classification

Transactions are parsed by regex matching on date patterns (`YYYY/MM/DD`), currency codes, and bank-num formatted values.

## Notes / Gotchas

- **Bilingual labels**: Section headers appear in both English and Chinese (e.g. `Account Summary 帐户摘要`). The parser matches on English keywords only.
- **Page 2**: Contains only notes and legal disclosures — no financial data to extract.
- **Merged words**: `extract_text()` may join words that are tightly spaced in the PDF (e.g. `ROLLOVERTRFXXXX`). This is a cosmetic limitation of the text extraction, not a parsing bug.
- **No sidebar noise**: Unlike OCBC, ICBC statements do not have rotated margin banners that need filtering.
- **Date format**: ICBC uses `YYYY/MM/DD` throughout — unambiguous and easy to match.

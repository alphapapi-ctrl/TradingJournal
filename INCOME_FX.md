# Dividend cash and account-currency P&L

This update changes application code and adds optional trade columns. It does
not import/backfill broker data, edit account 30, change DUMMY records, or place
broker orders. The reconciliation chat owns the November 3 data writes.

## Deploy

Back up the live database with SQLite online backup. Stop the journal and MCP,
update application code, and restart both. Normal startup adds `quote_currency`,
`fx_metadata` and `is_test` columns without recalculating existing record values.
Restart the AI client if it caches MCP tool schemas. Keep the machine's `data/`
folder and credentials; these are not shipped in Git or the source-only zip.

Do not reimport the actual November 3 source as a deployment test. Use the
isolated tests below. Provisional JEPQ entry reconciliation is retained and
reported; this update does not resolve or change that entry.

## TradingView cash imports

`Symbol,Side,Qty,Fill Price,Commission,Closing Time` supports both mixed fills
and dividend-only files. For `Side=Dividend`, Qty is the original cash amount,
not a share quantity. Native income records have zero quantity/prices, closed
status, payment dates in entry/exit dates, and account-currency cash in `pnl`.
Dividends cannot be reopened or merged as price trades.

MCP `import_trades` defaults to `dry_run=true` and `include_dividends=false`.
Opt in to income explicitly. The browser import preview has the same opt-in and
a dated-FX input. TradingView commission is stored separately from gross P&L and
deducted once; entry/exit commissions use their respective date's FX.

Dividend IDs retain the reconciliation scheme:
`TV_DIV_{literal source Symbol}_{literal Closing Time with T}_{Number(Qty)}_{occurrence}`.
Occurrence starts at 1 for identical source events. Exact source rows, source
hash and physical CSV line are retained. Independently reconciled dividend IDs,
AUD cash and original `raw_data.dividend_import_20261103` metadata are preserved
on repeats. Existing income rows are skipped, not overwritten using a newly
supplied FX rate. Reconciled price trades require explicit discrepancy review.
Repeated imports deduplicate within account/broker/ID; no cross-account merging.
Exports containing only a subset of identical duplicate events cannot identify
which occurrence was omitted; retain the complete source event set.

## FX inputs

`quote_currency` identifies instrument prices; account currency comes from the
linked account. The exchange identifies AUD for ASX and USD for supported US
exchanges; an explicit quote override is available. The source export has no
currency column, so exchange-based attribution is an assumption retained in
the import metadata. Unknown exchanges require a quote override.

For cross-currency TradingView imports, supply `fx_rates`, keyed by the requested
event date. Example for a simulated opening-price event:

```json
{
  "2026-11-02": {
    "account_per_quote": 1.4,
    "rate_date": "2026-11-02",
    "source": "Your dated FX source",
    "field": "open",
    "approximate": true
  }
}
```

The example rate is illustrative. Rates are **account units per quote unit**;
for AUD account / USD instrument, this is 1 / AUDUSD. Daily close observations
can use `field=close`. The latest available prior observation within seven
calendar days may cover weekends/holidays; future/stale/nonfinite rates fail
before any records are written. Preserve `rate_date` independently of the event
date. No server-side FX downloads run during import or reporting.

For long stock trades, account P&L = quote sale proceeds × exit FX − quote cost
× entry FX. Short price cashflow has the opposite sign. Dividends use payment
FX. Original quote amounts, commissions and FX observations are retained in
`fx_metadata`/`raw_data`. Rates remain estimates, not broker-confirmed conversion.

Native saves accept `quote_currency` and `fx_metadata` containing `entry`, `exit`
or `payment` FX observations plus `pnl_currency` and `cost_currency`. November 3
legacy reconciliation metadata is recognised without rewriting those records.
Missing cross-currency information is reported as unknown, not labelled AUD.

## Reporting and open positions

MCP `generate_report`/`save_report` exclude cash dividends from trade count,
win rate, profit factor, discipline and playbook/risk statistics. Performance
contains price-trade outcomes; `cashflow` separately includes distributions and
realised cash equity. Exit/payment dates drive realised reports and monthly cash
totals. `date_basis=entry` offers legacy date selection, while cashflow curves
still use actual exit/payment dates.

`include_test=false` is the default. It excludes `is_test=1`, `raw_data.is_test`,
broker DUMMY and DUMMY_ broker IDs. `include_test=true` includes them for an
explicit comparison. A Demo account alone is not test data. Exclusion does not
modify/delete the two historical DUMMY trades. Browser Reports exposes the same
cash/trade separation and a currency selector for general performance.

Open valuation is opt-in for MCP, with `valuations` entries containing
`trade_id`, quote `price`, `quote_currency`, `as_of`, and current `fx`.
`entry_fx` may be supplied when not persisted. Converted entry cost basis and
current converted market value include FX movement, with recorded entry costs
deducted. Missing marks/FX stay null with coverage counts; complete total-return
P&L is withheld until all selected open holdings can be valued.

The journal's open-position panel uses cached provider quotes and dated FX
estimates. Imported cash distributions are displayed once by account currency,
with payment-date coverage. Provider ex-date/current-quantity distribution
estimates appear separately in quote currency and are excluded from P&L totals.
They are not additional cash receipts or unrealised price gains; they are not
subtracted from recorded payments because entitlement and payment coverage differ.
For the authorised demo rebalance provenance pointing to an `_Open.csv` source,
an unstored entry FX estimate uses daily open. Ordinary historical entries use
daily close. A conflicting supplied close rate for that opening-price rebalance
is rejected. These stock/ETF cost-basis marks do not value derivative contracts.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests write only disposable databases. Real source files and reconciliation
plans are read locally for an additional isolated verification and are not
committed or included in deployment archives.

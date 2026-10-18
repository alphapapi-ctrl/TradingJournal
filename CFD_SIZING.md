# Stock and IC Markets CFD position sizing

In **Risk Calculator → Position Size**, select **Instrument type**:

- **Normal stock** sizes whole shares using entry-to-stop distance.
- **CFD** adds an IC Markets contract dropdown: Gold (`XAUUSD.a`) or
  US500 (`US500.a`). Quantity is in lots, not shares.

| Preset | Contract size per lot | USD per full 1.00 price move per lot | Minimum / lot step | Maximum lots |
| --- | --- | --- | --- | --- |
| IC Markets Gold | 100 ounces | 100 | 0.01 / 0.01 | 100 |
| IC Markets US500 | 1 | 1 | 0.1 / 0.1 | 250 |

Sources: [IC Markets AU metals specification](https://cdn.icmarkets.com.au/uploads/AU/Commodity-Specification-Sheet.pdf),
[IC Markets AU indices specification](https://cdn.icmarkets.com.au/uploads/AU/Indices-Product-Specification-Sheet-ASIC.pdf)
and the supplied terminal specifications for exact `.a` symbols and lot steps.
These presets apply to those IC Markets USD CFD contracts, not futures or other
brokers. The CFD form requires a USD account (or manual USD balance when no
account is configured); it never silently treats an AUD balance as USD.

Enter the **current balance**, risk percentage, actual planned entry/stop prices
and a round-trip cost allowance per lot. A zero cost allowance visibly excludes
unrepresented spread, commission and slippage. The calculator does not assume
the account is Raw Spread or automatically apply the published Raw commission.

Cash risk per lot is `abs(entry - stop) × contract_size + cost allowance`.
Divide the risk budget by this value, round **lots down** to the volume step,
and enforce minimum and maximum volume. A budget below minimum returns zero
lots; it never rounds up. Prices retain their fractional differences. Displayed
decimals and a full index-point reference are not treated as proof of a broker
minimum tick. No tick size or tick value is needed for this CFD calculation.
Notional exposure is shown separately from risk and is not a margin estimate.

Saved setups preserve the selected contract, lot quantity, balance, risk and cost
allowance. Older setups remain stocks. Saving a pre-trade plan records lot units
and the contract/cost basis without creating a trade. A CFD preview does not
create an execution; confirmed fills follow the existing journal workflow.

## MCP contract-size mode

`risk_calculator` accepts `instrument_key: "ic_gold"` or `"ic_us500"` for the same
presets and `costs_per_contract` for the per-lot round-trip cash allowance. Those
presets replace generic point/quantity inputs and use USD amounts. Existing
generic stock/point-value calls remain supported.

For persistent candidate sizing, create a new `instrument_specs` version using
`save_instrument_spec` with `sizing_method: "contract_size"`:

```json
{
  "data": {
    "symbol": "XAUUSD.a",
    "version": "icmarkets-contract-size-v1",
    "quote_currency": "USD",
    "sizing_method": "contract_size",
    "contract_multiplier": 100,
    "quantity_step": 0.01,
    "min_quantity": 0.01,
    "max_quantity": 100,
    "source": "IC Markets published metals specification and terminal lot step",
    "verified_at": "<actual offset-aware verification time>"
  },
  "idempotency_key": "<stable logical operation key>"
}
```

For US500 use exact symbol `US500.a`, multiplier 1, minimum/step 0.1 and maximum
250. The server derives `point_value` from the multiplier once; a contradictory
point value is rejected. Omit unknown ticks rather than inventing values or
passing zero. If a known tick pair is supplied, both values must be positive and
consistent, and existing price-grid validation remains active. Default
`sizing_method: "point_value"` retains the old required-tick contract.

`size_candidate` retains account snapshot, explicit regime, FX, risk limits,
revision/idempotency and immutable-plan protections. Contract-size specs with
unknown ticks accept decimal prices, enforce lot minimum/step/maximum and report
`tick_validation_applied: false`. Trades linked to those specs use the saved
contract-size/FX basis for P&L without requiring tick values.

## Install and verify

The remote journal needs the changed application files before this dropdown is
available there. Update the checkout or copy the source-only package generated
by `scripts/package_mcp.py`, then restart the journal and MCP. Startup migration
makes instrument tick fields nullable, retains original spec IDs/values and
append-only protections, and adds saved-setup contract/cost fields. It does not
select, alter or replace existing live instrument bindings automatically. Choose
a newly saved contract-size version explicitly after deployment.

Tests use disposable databases and cover stock/CFD UI switching, setup reload,
pre-trade plans, exact lot rounding, fractional prices, costs, minimum/maximum
volume, native sizing/P&L, legacy tick validation and migration rollback. No real
trades, account snapshots or personal journal records are created by tests.

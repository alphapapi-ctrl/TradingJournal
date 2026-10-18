# Neo indicator configuration

Baseline version 0.1.0. Source: Neo_Indicator_Setup_and_Alert_Guide_v1 (1).pdf, pages 2-5. Settings are recommended in that guide; record deliberate user changes and their versions.

For the actual current TradingView workspace, also read project-root `tradingview-layouts.json`. Its signed-in inventory records newer Neo scripts and different chart/indicator timeframes, including NAS100 Stochastic on 3m. Those observations do not alter the PDF trading rules. Verify the intended demo instrument and resolve material configuration differences before a dependent entry assessment; do not silently apply this PDF parameter list to a different script version.

## Chart layout

Use the 5-minute entry chart with Supply/Demand, Neo Buy/Sell Filter v4 and Neo CVD Divergence Detector v2. Profile levels are optional. Lower panels are CVD, ADX/DI, and ordinary Stochastic reading 15-minute data. Use the Neo Session Dashboard v4 - Medium for conditions and Neo Master Alert Engine v1 for alerts. [I2 visual, I5]

Supply/demand red is a possible short area; green is a possible long area. Green/red squares are reversal buy/sell marks. Green/red triangles are continuation buy/sell marks. All need the baseline checks. [I2]

## Neo Buy Sell Filter v4

| Setting | PDF value |
| --- | --- |
| Signal Mode | Balanced |
| Range Lookback | 80 |
| Extreme Zone % | 25 |
| Confirmation Lookback | 5 |
| Main Signal Cooldown | 15 |
| Continuation Cooldown | 8 |
| Continuation Allowed After Reversal Bars | 30 |
| RSI Length / OB / OS | 14 / 70 / 30 |
| MACD | 14 / 30 / 9 |
| ADX/DI Length inside script | 14 unless adjusted; distinct from dashboard length 6 |
| Minimum ADX inside script | 18 |
| EMA Direction Filter | Off |
| Show EMA | Testing only |

Source I2-3. Keep the script's ADX 14/minimum 18 separate from panel/dashboard 6/20. They serve different components; this is not a conflict to flatten into one setting.

## CVD divergence and lower panels

| CVD Divergence Detector v2 setting | PDF value |
| --- | --- |
| Lower timeframe for CVD | 1 minute |
| Reset | Daily |
| Swing/Pivot Length | 6 |
| Double Top/Bottom Tolerance | 20 ticks |
| Minimum CVD Difference | 8% |
| Max Bars Between CVD Pivots | 120 |
| Labels | On while learning; optional later |
| Lines | On |

Bearish divergence: price retests/pushes up without CVD confirming. Bullish divergence: price retests/pushes down without CVD confirming. Best near supply/demand, and a warning rather than an automatic entry. Rising/falling CVD indicates buying/selling pressure; disagreement with price is a warning. [I3]

ADX/DI panel: length 6, threshold 20. +DI above -DI is bullish pressure; -DI above +DI is bearish. ADX above 20 is active pressure; above 30 is strong. Tangled DI lines indicate chop. Record direction separately from ADX strength. [I3]

Ordinary Stochastic, not Stoch RSI: %K length 5, %K smoothing 4, %D smoothing 4; timeframe 15 minutes; wait for timeframe close enabled. Short timing rolls down from a high area; long timing rolls up from a low area. [I3]

## Session dashboard

| Setting | PDF value |
| --- | --- |
| ATR Length | 72 |
| Chart Minutes | 5 on a 5-minute chart; otherwise match the chart |
| Low Volatility Threshold | -8% |
| High Volatility Threshold | 8% |
| Extreme Volatility Threshold | 18% |
| ADX/DI Length | 6 |
| ADX Threshold | 20 |
| Strong Trend Threshold | 30 |
| Recent Cross Window | 5 |
| Position | Top right or clear of price |

The dashboard reads the chart it is on. TRADEABLE means conditions are acceptable but the full checklist still applies. WAIT means weak/choppy ADX/DI; LOW QUALITY means dead volatility. CAUTION warns of extreme volatility, rather than an automatic all-clear or an invented absolute prohibition. STRONG BEAR/BULL warn against opposing trades. Bull/Bear DI cross reports a recent cross; No recent cross reports none in that window. [I4]

## Alerts

Use HIGH CONFLUENCE Buy/Sell from Neo Master Alert Engine v1, Once Per Bar Close, with app notifications. Optional CVD divergence alerts are early warnings. Avoid alerting every reversal/continuation initially. Pine scripts do not read the values of separate indicators, so the master alert engine repeats its logic internally; confirm consistency rather than assuming it reads the displayed panels. Alerts are prompts to review the full checklist. [I4-5]

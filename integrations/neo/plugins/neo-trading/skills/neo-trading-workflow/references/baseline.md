# Neo trading baseline

Version 0.1.0. Consolidated 18 October 2026. Initial use: demo practice with 1% default account risk, variable by user instruction. Live/prop risk rules will be added separately.

Neo uses market conditions, location, directional pressure, setup signals, CVD, timing and target space together. The reference guides describe a working draft for testing; they do not establish profitability. Signal marks identify areas to assess, rather than sufficient entries by themselves.

## Authority and sources

Explicit user changes take precedence. Otherwise the two current PDF guides govern. `Neo strategy.docx` describes an older system and is historical only. An ambiguity in the PDFs remains an ambiguity until resolved; do not fill it with DOCX rules.

Source page labels below refer to the PDF page number, including the cover:

| Code | Source | Pages used |
| --- | --- | --- |
| T | Neo_Trading_System_Guide_v1 (1).pdf | 1-6; includes visual priority stack on page 2 and flowchart on page 3 |
| I | Neo_Indicator_Setup_and_Alert_Guide_v1 (1).pdf | 1-5; includes indicator map on page 2 |

The full PDF filenames are at the NEO project root. Their consolidation hashes are in root `source-manifest.json`. Indicator parameters are in [indicator settings](indicators.md).

## Market and decision order

The documented instrument is MNQ. Active London and New York periods are preferred. The PDFs do not define a hard session-hours restriction or extend this baseline to copper. Exact contracts, exchange, chart feed, session hours and daylight-saving conversions must be identified for the actual session. [T2]

Prefer active but not completely one-directional conditions, rotations between supply and demand or a reaction at a known area, enough volatility to reach the target, clean directional pressure, meaningful CVD evidence and clear target space. [T2]

Assess in this order, preserving the PDF priority stack [T2 visual]:

1. Market condition: active and suitable for Neo.
2. Trade location: supply, demand, range edge or a valid continuation area.
3. ADX/DI pressure: clean and supportive, rather than choppy.
4. Neo signal: reversal square or continuation triangle.
5. CVD evidence: divergence, exhaustion, absorption or confirmation.
6. Stochastic timing: rolling from the relevant high/low area in the trade direction.
7. Target space: room for around 1R to 1.5R before trouble.
8. Risk, news and mindset: logical stop, clear news context and no forced trade.

The flowchart's shorthand "dot/arrow" refers to the reversal/continuation signal families; the detailed guides identify squares/triangles. Do not treat the old DOCX semafor as an additional required trigger. [T3 visual, T4-5, I2]

## No trade and review conditions

- Dead volatility or dashboard LOW QUALITY. [T2]
- Tangled ADX/DI or dashboard WAIT. [T2]
- No valid location or continuation setup. [T2]
- Insufficient space before the next problem area. [T2]
- A major news event is close; the numerical buffer is not specified. [T3]
- Strong trend pressure directly opposes the idea. [T3]
- A forced trade motivated by wanting action. [T3]
- Two poor trades, emotional wobble or a rule break: stop and review. [T6]

"Poor trade" is not defined as every losing trade. Record the user's assessment and the reason. The PDFs do not prescribe a 12-hour standdown, a four-stop threshold, or a restart rule. Persist the review flag until the user completes the review and explicitly decides how to resume; that persistence is a workflow convention, not a new trading rule.

## Reversal setups

| Check | Long reversal | Short reversal |
| --- | --- | --- |
| Location | Demand or range low | Supply or range high |
| Trigger | Green reversal square | Red reversal square |
| Pressure | ADX/DI supports upside or a recent bullish cross | ADX/DI supports downside or a recent bearish cross |
| Timing | Stochastic rolling up from low area | Stochastic rolling down from high area |
| Preferred CVD | Bullish divergence | Bearish divergence |
| Target area | Range middle, supply or clear profile level | Range middle, demand or clear profile level |

These directional criteria come from T4. Assess the common market, risk/news/mindset and target-space checks too. CVD divergence is preferred, not mandatory; assess other supportive CVD evidence and record any caution. Middle-of-range is generally a target or continuation area, not a fresh reversal location. [T2, T6, I2]

## Continuation setups

| Check | Long continuation | Short continuation |
| --- | --- | --- |
| Origin | Bullish move already started from a valid area | Bearish move already started from a valid area |
| Structure | Pullback rather than full reversal | Pullback rather than full reversal |
| Trigger | Green continuation triangle | Red continuation triangle |
| Pressure | ADX/DI still supports upside | ADX/DI still supports downside |
| CVD | Supportive or does not strongly fight the long | Weak or does not strongly fight the short |
| Space | Enough room for around 1R to 1.5R | Enough room for around 1R to 1.5R |

These criteria come from T5. Apply the general timing and risk/news/mindset checks in T6 as well. A continuation triangle alone does not establish a valid pullback or authorise adding to a position.

## Management and exits

PDF rules [T5-6]:

- Primary target model: around 1R to 1.5R, with actual target space assessed.
- Place the stop beyond the zone or recent structure, not an arbitrary number.
- Do not move the stop emotionally.
- Do not add unless continuation rules are met.
- Skip the entry if target space is unavailable.
- Stop and review after two poor trades, emotional wobble or a rule break.

Operational convention: retain the original entry-to-stop distance and planned cash risk so R-based reviews remain anchored to the original plan. Distinguish planned price R from realised net monetary R, especially for partial fills and fees. Changing a stop does not retroactively change the original R.

The PDFs do not specify a mechanical early exit when stochastic flattens, the buy/sell mark flips or momentum stalls. They also do not define breakeven triggers, trailing stops, partial-profit schedules, time stops, maximum holding time, scale-in sizing or aggregate position risk. Those remain open for the user's practical development. Preserve each management decision with its evidence and label any experiment; do not make it an automatic rule after one trade.

## Risk regime and unresolved decisions

User instruction, 18 October 2026: risk is a percentage of the account; the initial demo default is **1%** and may vary. A different live/prop regime will be added later.

Operational conventions:

- Use current account balance as the proposed percentage basis and record it explicitly. Do not substitute initial balance or equity silently.
- Record a per-trade percentage override without changing the default unless requested.
- Calculate planned cash risk as balance x percentage / 100. Size to the structural stop after verifying contract economics, currency and permitted quantity increment.
- Do not assume contract point value, FX conversion, fees, slippage or a suitable demo account. Existing account records are not evidence of a current balance or the intended Neo account.
- No demo percentage is inherited by a live or prop account. Account/firms' applicable limits must be supplied or verified for that account when the later regime is introduced.

Still to define: selected Neo demo account; numerical news buffer and events in scope; exact operating session hours if restricted; daily loss/trade limits; definition of a poor trade and review/restart process; combined risk and additions; detailed exits. These gaps do not prevent preparation, journalling or post-trade review. Flag the relevant gap before a dependent trade assessment or sizing decision.

## Rule classification for journal playbooks

The initial implementation uses two direction-neutral playbooks, one for reversals and one for continuations. Each has eight required checks corresponding to the decision stack. This grouping is a journalling convention; it does not add eight new indicator rules.

For reversals, the CVD required check records assessed supportive evidence under the common checklist; divergence is a separate optional preference. Absence of divergence alone is not a required-check failure. For continuations, include the valid-origin/pullback criteria within location and the setup's specific CVD test. The Stochastic check remains part of the common entry checklist.

Required checks with a known failure block a candidate. With workflow integration 0.2.0, submit pass/fail/unknown/caution through the journal's native evidence evaluator; never turn missing evidence into fabricated booleans. Required unknown/caution evidence remains incomplete. Unknown optional divergence can leave readiness ready with a null composition score. Optional divergence does not increase the demo risk percentage. Rule scores measure recorded checklist adherence, not probability of success. The trading-rule baseline remains 0.1.0.

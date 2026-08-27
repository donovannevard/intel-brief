# Markets API — reference for other projects

Read-only price data collected and stored by `intel-brief`. Written for
`sovereign-stack` and anything else on this network that wants to know what
something costs today, and be able to say where that number came from.

**Base URL:** `http://<host>:8300` on the LAN, `http://localhost:8300` on
the machine itself (the port is `DASHBOARD_PORT`). No authentication — the service is LAN-bound and unauthenticated
throughout, so treat the network boundary as the security boundary.

## What this is and isn't

- **Daily closes, not live quotes.** Everything is one value per calendar day. If you
  need a tick or an intraday price, this is the wrong source — go to the exchange.
- **Collected every 6 hours**, on a schedule. Requests never trigger an upstream fetch,
  so responses are fast and predictable, and a provider being down never hangs your call.
- **Dates are UTC**, formatted `YYYY-MM-DD`.
- **Today's value appears once the provider publishes it.** For 24/7 markets (crypto)
  that's same-day; for equities and futures it follows the exchange session; FRED series
  can lag weeks (monthly) or a year (annual).

## Endpoints

### `GET /api/markets/price/{id}` — latest value

```json
{
  "instrument": "xmrusd",
  "label": "XMR/USD",
  "unit": "$",
  "date": "2026-08-19",
  "close": 414.5341,
  "raw_close": 414.5341,
  "source": "yfinance",
  "series_id": "XMR-USD",
  "provider": "Yahoo Finance (via the yfinance package)",
  "endpoint": "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
  "fetched_at": "2026-08-19T17:42:38.913925+00:00"
}
```

| field | meaning |
|---|---|
| `close` | The value to use. Unit-scaled and, for FX, inverted so the label matches the number. |
| `raw_close` | Exactly what the provider published, before our transform. Present so you can audit the conversion; identical to `close` for most instruments. |
| `source` / `series_id` | Provenance: which provider, and their identifier for the series. |
| `provider` / `endpoint` | Human-readable attribution — use these if you need to cite the source. |
| `fetched_at` | When we collected it, not when it was published. |

Returns **404** for an unknown id, or for a known instrument with nothing collected yet.

### `GET /api/markets/price/{id}/history?start=&end=`

Same provenance fields plus `count`, a `history` array of `{ts, close}`, and
`source_history` — the record of which provider backed this instrument over time.
`start`/`end` are inclusive `YYYY-MM-DD` and both optional; omit them for everything.

### `GET /api/markets` — discovery

Every instrument with its id, label, group, tab and unit. Use it to enumerate rather
than hardcoding a list.

### `GET /api/markets/{tab}?period=` — a whole tab at once

`tab` is `crypto`, `stocks`, `commodities` or `monetary`. `period` is one of `1mo`,
`3mo`, `1y`, `3y`, `5y`, `10y`, `25y`, `50y`. Returns full history plus computed
changes for every instrument on that tab — this is what the dashboard renders from.

## Direct database access

For bulk history, `markets.db` is a SQLite file holding prices only — no news, nothing
personal — so it can be read directly:

```python
conn = sqlite3.connect("file:/var/lib/intel-brief/app/data/markets.db?mode=ro", uri=True)
conn.execute("SELECT date, close, fetched_at FROM market_prices "
             "WHERE source=? AND series_id=? ORDER BY date", ("yfinance", "XMR-USD"))
```

Tables: `market_prices(source, series_id, date, close, fetched_at)`,
`data_sources(id, provider, endpoint, notes, ...)` describing each provider, and
`instrument_sources(instrument_id, source, series_id, active_from, active_to)` recording
which provider backed which instrument and when.

**Open it read-only.** `intel-brief` is the only writer; a second writer on a SQLite file
is a corruption risk for no benefit.

Note that the database stores **raw provider values** — the API applies unit scaling and
FX inversion on read. If you query the file directly for a scaled instrument (hash rate,
Lightning capacity, central bank balance sheets) or an inverted FX pair (`usdjpy`,
`usdcny`, `usdrub`), you get the provider's units, not the dashboard's.

## Stability contract

- **Instrument ids are stable.** `xmrusd` stays `xmrusd` regardless of who supplies it.
- **`source` may change** if a provider is discontinued. When it does, existing rows keep
  their original attribution and the switch is recorded in `instrument_sources` — so a
  change of upstream is visible rather than silent. Key your code on the instrument id,
  and read `source` as data rather than assuming it.

## The catalogue

### Crypto

| id | what it is | unit | provider | collecting |
|---|---|---|---|---|
| `btcusd` | BTC/USD | $ | yfinance | yes — latest 2026-08-19 |
| `xmrusd` | XMR/USD | $ | yfinance | yes — latest 2026-08-19 |
| `btc_hash` | Hash Rate | EH/s | blockchain.com | yes — latest 2026-08-13 |
| `btc_diff` | Difficulty | T | blockchain.com | yes — latest 2026-08-13 |
| `btc_addr` | Active Addresses | K/day | blockchain.com | yes — latest 2026-08-15 |
| `btc_tx` | Transactions | K/day | blockchain.com | yes — latest 2026-08-15 |
| `btc_mempool_bytes` | Mempool Size | MB | blockchain.com | yes — latest 2026-08-19 |
| `btc_mempool_count` | Mempool Count | txs | blockchain.com | yes — latest 2026-08-19 |
| `btc_block` | Avg Block Size | MB | blockchain.com | yes — latest 2026-08-15 |
| `btc_chainsize` | Blockchain Size | GB | blockchain.com | yes — latest 2026-08-14 |
| `btc_nodes` | Reachable Nodes | nodes | bitnodes.io | yes — latest 2026-08-19 |
| `ln_channels` | Channel Count | channels | mempool.space/ln | yes — latest 2026-08-19 |
| `ln_capacity` | Network Capacity | BTC | mempool.space/ln | yes — latest 2026-08-19 |
| `ln_nodes` | Total Nodes | nodes | mempool.space/ln-latest | yes — latest 2026-08-19 |
| `ln_tor_nodes` | Tor-only Nodes | nodes | mempool.space/ln | yes — latest 2026-08-19 |
| `ln_clearnet_nodes` | Clearnet Nodes | nodes | mempool.space/ln | yes — latest 2026-08-19 |
| `ln_avg_fee` | Avg Fee Rate | ppm | mempool.space/ln-latest | yes — latest 2026-08-19 |

### Stock Market

| id | what it is | unit | provider | collecting |
|---|---|---|---|---|
| `sp500` | S&P 500 | pts | yfinance | yes — latest 2026-08-19 |
| `ndx` | Nasdaq 100 | pts | yfinance | yes — latest 2026-08-19 |
| `ftse` | FTSE 100 | pts | yfinance | yes — latest 2026-08-19 |
| `dax` | DAX | pts | yfinance | yes — latest 2026-08-19 |
| `nikkei` | Nikkei 225 | pts | yfinance | yes — latest 2026-08-18 |
| `shanghai` | Shanghai | pts | yfinance | yes — latest 2026-08-18 |
| `nvda` | NVIDIA | $ | yfinance | yes — latest 2026-08-19 |
| `googl` | Alphabet | $ | yfinance | yes — latest 2026-08-19 |
| `meta` | Meta | $ | yfinance | yes — latest 2026-08-19 |
| `amzn` | Amazon | $ | yfinance | yes — latest 2026-08-19 |
| `aapl` | Apple | $ | yfinance | yes — latest 2026-08-19 |
| `msft` | Microsoft | $ | yfinance | yes — latest 2026-08-19 |
| `mstr` | MicroStrategy | $ | yfinance | yes — latest 2026-08-19 |
| `vix` | VIX | — | yfinance | yes — latest 2026-08-19 |
| `hyspread` | HY Spread | % | — | **not yet** (see caveats) |

### Commodities

| id | what it is | unit | provider | collecting |
|---|---|---|---|---|
| `brent` | Brent Crude | $/bbl | — | **not yet** (see caveats) |
| `natgas` | Natural Gas | $/MMBtu | — | **not yet** (see caveats) |
| `gold` | Gold | $/oz | yfinance | yes — latest 2026-08-19 |
| `silver` | Silver | $/oz | yfinance | yes — latest 2026-08-19 |
| `copper` | Copper | $/lb | yfinance | yes — latest 2026-08-19 |
| `lithium` | Lithium (ETF) | $ | yfinance | yes — latest 2026-08-19 |
| `corn` | Corn | $/bu | yfinance | yes — latest 2026-08-19 |
| `wheat` | Wheat | $/bu | yfinance | yes — latest 2026-08-19 |
| `soy` | Soybeans | $/bu | yfinance | yes — latest 2026-08-19 |
| `coffee` | Coffee | $/lb | yfinance | yes — latest 2026-08-19 |
| `sugar` | Sugar | ¢/lb | yfinance | yes — latest 2026-08-19 |
| `cocoa` | Cocoa | $/t | yfinance | yes — latest 2026-08-19 |

### Monetary System

| id | what it is | unit | provider | collecting |
|---|---|---|---|---|
| `us10y` | US 10Y | % | — | **not yet** (see caveats) |
| `us2y` | US 2Y | % | — | **not yet** (see caveats) |
| `uk10y` | UK 10Y | % | — | **not yet** (see caveats) |
| `de10y` | Germany 10Y | % | — | **not yet** (see caveats) |
| `jp10y` | Japan 10Y | % | — | **not yet** (see caveats) |
| `ru10y` | Russia 10Y | % | — | **not yet** (see caveats) |
| `dxy` | DXY | — | yfinance | yes — latest 2026-08-19 |
| `gbpusd` | GBP/USD | — | yfinance | yes — latest 2026-08-19 |
| `eurusd` | EUR/USD | — | yfinance | yes — latest 2026-08-19 |
| `usdcny` | CNY/USD | — | yfinance | yes — latest 2026-08-19 |
| `usdjpy` | JPY/USD | — | yfinance | yes — latest 2026-08-19 |
| `usdrub` | RUB/USD | — | yfinance | yes — latest 2026-08-19 |
| `wealth_top1` | Top 1% Wealth | % | — | **not yet** (see caveats) |
| `fed_assets` | Fed Balance Sheet | $B | — | **not yet** (see caveats) |
| `cb_ecb` | ECB | €B | — | **not yet** (see caveats) |
| `cb_boj` | BoJ | ¥T | — | **not yet** (see caveats) |
| `debt_usa` | USA | % GDP | — | **not yet** (see caveats) |
| `debt_uk` | UK | % GDP | — | **not yet** (see caveats) |
| `debt_de` | Germany | % GDP | — | **not yet** (see caveats) |
| `debt_cn` | China | % GDP | — | **not yet** (see caveats) |
| `debt_jp` | Japan | % GDP | — | **not yet** (see caveats) |
| `debt_ru` | Russia | % GDP | — | **not yet** (see caveats) |
| `pp_usd` | USD | — | — | **not yet** (see caveats) |
| `pp_gbp` | GBP | — | — | **not yet** (see caveats) |
| `pp_eur` | EUR (DEM) | — | — | **not yet** (see caveats) |
| `pp_jpy` | JPY | — | — | **not yet** (see caveats) |
| `pp_chf` | CHF | — | — | **not yet** (see caveats) |
| `pp_cny` | CNY | — | — | **not yet** (see caveats) |
| `inf_usa` | USA CPI | % | — | **not yet** (see caveats) |
| `inf_uk` | UK CPI | % | — | **not yet** (see caveats) |
| `inf_de` | Germany CPI | % | — | **not yet** (see caveats) |
| `inf_cn` | China CPI | % | — | **not yet** (see caveats) |
| `inf_jp` | Japan CPI | % | — | **not yet** (see caveats) |
| `inf_ru` | Russia CPI | % | — | **not yet** (see caveats) |

## Caveats worth passing on

**31 series are not collecting yet.** FRED (bond yields, inflation, government debt,
central bank balance sheets, currency purchasing power, HY spread, Brent crude, natural
gas) started refusing this IP after I ran three full ingests inside an hour while building
this. It's a temporary block on our side, not a change at FRED. Collection resumes on the
next scheduled run once it lifts; the refresh interval is now 24h with delays between
requests so it can't recur. **Everything from Yahoo Finance, blockchain.com, bitnodes.io
and mempool.space is unaffected** — which includes every currency and crypto price.

**Some upstream series are genuinely discontinued** and will not resume regardless: FRED's
OECD CPI family stopped updating (Japan 2021-06, Russia 2022-03, UK and Germany 2025-03,
China and Switzerland 2025-04), and Russia's 10Y yield ends 2018. Those affect the
inflation and purchasing-power instruments. Replacements haven't been chosen yet — check
`fetched_at` and the date on anything from the Monetary System tab before relying on it.

**Two series can never be backfilled.** `btc_nodes` and `ln_avg_fee` / `ln_nodes` come
from endpoints that only publish a current snapshot, so their history starts when this
dashboard did and gains gaps whenever the machine is off.

**FX pairs are quoted foreign-per-USD.** `gbpusd` and `eurusd` are natural (USD per GBP);
`usdcny`, `usdjpy` and `usdrub` are inverted from how the provider quotes them so that
every pair moves in the same direction — rising means the non-dollar currency strengthened.
`raw_close` shows the provider's original quote.

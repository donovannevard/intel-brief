"""What this dashboard tracks: instrument definitions, the group they belong
to, and which of the four Markets tabs shows them.

Carried over with the descriptions intact -- they are the editorial
substance of the markets view, not boilerplate -- and reorganised from an
earlier two-tab split (Global Markets + BTC Tracker) into four tabs that
mirror how the news side is already categorised.

Each instrument names its own upstream source with a source-specific key
(`yf`, `fred`, `blockchain`, `bitnodes`, `mempool_ln`, `mempool_ln_latest`).
That binding is what `ingest.py` records into `instrument_sources`, so a
future provider switch leaves an audit trail rather than silently changing
what the numbers mean.
"""

from intel_brief.config import settings

# The four Markets tabs. Order here is the order in the sub-navigation.
# TABS below is this list minus anything left with no instruments -- see the
# CRYPTO_ASSETS gate further down.
_ALL_TABS: list[dict[str, str]] = [
    {"id": "crypto", "label": "Crypto",
     "blurb": "Bitcoin and Monero prices, plus the on-chain and Lightning fundamentals underneath them."},
    {"id": "stocks", "label": "Stock Market",
     "blurb": "Major indices, the mega-cap technology companies, and the stress gauges that move with them."},
    {"id": "commodities", "label": "Commodities",
     "blurb": "Energy, metals, and the agricultural commodities that turn into food prices."},
    {"id": "monetary", "label": "Monetary System",
     "blurb": "Inflation, central bank balance sheets, government debt, currency purchasing power, and who ends up owning the assets."},
]

# group name -> tab id. Every group in GROUP_META/BTC_GROUP_META must appear
# here; TABS_BY_GROUP completeness is asserted at import time below.
GROUP_TABS: dict[str, str] = {
    # -- crypto --
    "Price": "crypto",
    "Network Security": "crypto",
    "Network Usage": "crypto",
    "Lightning Network": "crypto",
    "CoinJoin (no free API)": "crypto",
    "Supply & Flow (requires paid data)": "crypto",
    # -- stocks --
    "Equity": "stocks",
    "Technology": "stocks",
    "Bitcoin Treasury": "stocks",
    "Market Stress": "stocks",
    # -- commodities --
    "Rare Earth Metals & Energy": "commodities",
    "Agriculture": "commodities",
    # -- monetary system --
    "Bonds": "monetary",
    "FX": "monetary",
    "Inflation": "monetary",
    "Central Bank Balance Sheets": "monetary",
    "Government Debt": "monetary",
    "Fiat Devaluation": "monetary",
    "Wealth Concentration": "monetary",
}

# ---------------------------------------------------------------------------
# Group metadata  (desc shown below heading; unit_note shown in detail modal)
# ---------------------------------------------------------------------------
GROUP_META: dict[str, dict] = {
    "Equity": {
        "desc": "Stock markets measure the collective value of publicly listed companies. Rising indices reflect corporate earnings growth, credit expansion, or simple momentum.",
        "unit_note": "Index points are a weighted average of constituent stock prices — the absolute level is arbitrary; only percentage change matters.",
    },
    "Bonds": {
        "desc": "Government bond yields are the interest rates governments pay to borrow. Higher yields mean higher borrowing costs economy-wide. The yield curve shape signals growth and inflation expectations.",
        "unit_note": "Yield in % per annum. A 10Y yield of 4.5 % means the government pays 4.5 % interest annually on 10-year debt.",
    },
    "Rare Earth Metals & Energy": {
        "desc": "Critical minerals, precious metals, and energy commodities driving the global economy. Unlike fiat currencies, these finite physical resources cannot be printed — making them structural hedges against monetary debasement and essential inputs for the energy transition.",
        "unit_note": "Oil per barrel, natural gas per million BTU, gold/silver per troy ounce, copper per pound. Lithium shown via Global X LIT ETF (USD) — the most liquid proxy for lithium sector pricing.",
    },
    "FX": {
        "desc": "Foreign exchange rates show how much one currency buys of another. Dollar strength tightens global liquidity; all major fiat currencies are in a long-run race to zero.",
        "unit_note": "EUR/USD and GBP/USD are quoted as USD per foreign unit (higher = stronger EUR/GBP). CNY/USD and JPY/USD are inverted to match — rising means the foreign currency strengthened vs the dollar.",
    },
    # Split out of the source project's combined "Risk & Wealth Concentration"
    # group: the acute market-stress gauges belong beside the equities they
    # measure, while wealth concentration is a slow structural consequence of
    # monetary policy and belongs with the rest of the monetary system.
    "Market Stress": {
        "desc": "Acute stress gauges for equity and credit markets. VIX prices how much volatility options traders expect over the next month; the high-yield spread prices how much extra return investors demand to lend to weaker companies. Both spike before and during dislocations rather than predicting them far ahead.",
        "unit_note": "VIX below 15 = calm, 20–30 = elevated fear, above 40 = panic. HY spread above 5 % historically precedes credit crises.",
    },
    "Wealth Concentration": {
        "desc": "When central banks expand the money supply, asset prices rise first: those who already own assets get richer, those who don't fall further behind. This is the chronic structural consequence of the monetary expansion charted elsewhere on this tab — not a market-stress signal, but the distributional result of decades of it.",
        "unit_note": "Wealth share is % of total US net worth held by each group. Fed Distributional Financial Accounts, quarterly from Q3 1989.",
    },
    "Government Debt": {
        "desc": "Government debt as a share of GDP shows how leveraged major economies have become. Post-2008 and post-COVID debt levels are at peacetime records. This debt can only be resolved via growth, default, or inflation — history shows inflation wins.",
        "unit_note": "Debt-to-GDP of 100 % means a country owes one full year of its entire economic output. Japan exceeds 260 %; the US exceeds 120 %.",
    },
    "Fiat Devaluation": {
        "desc": "Every fiat currency in recorded history has eventually lost all its value. These charts show purchasing power since the earliest available data — all roads lead to zero. The 2 % inflation target means your money halves every 35 years by design.",
        "unit_note": "Purchasing power index: 100 = full value at series start. Lower = more value lost. The USD has lost over 97 % of its 1913 purchasing power.",
    },
    "Agriculture": {
        "desc": "Agricultural commodities are the foundation of global food security. Prices reflect weather shocks, geopolitical supply disruptions, energy costs (fertiliser), and speculative flows. Rising food prices are a first-order inflation signal and a historic trigger for social unrest.",
        "unit_note": "Grain futures priced per bushel (USD). Soft commodities: coffee per pound, sugar in cents per pound, cocoa per metric tonne. Prices represent next front-month delivery contract.",
    },
    "Bitcoin Treasury": {
        "desc": "Listed companies whose share price is driven primarily by bitcoin they hold rather than by their operating business. Watched here for the mechanism as much as the price: these companies fund bitcoin purchases by issuing equity, so the share count itself is part of the story — dilution converted into coins.",
        "unit_note": "Share price in USD (split/dividend adjusted). Share count is shares outstanding, not any personal holding — this dashboard tracks instruments, not positions.",
    },
    "Technology": {
        "desc": "The six dominant technology companies — collectively worth over $13 trillion and responsible for a disproportionate share of global R&D, AI investment, and market returns. Their combined capex now rivals the GDP of mid-sized nations. These stocks are the primary vehicle through which global savings are allocated into AI infrastructure.",
        "unit_note": "All prices in USD (auto-adjusted for splits and dividends). These six companies make up roughly 30% of the S&P 500 by market cap — meaning index-tracking products are heavily concentrated here.",
    },
    "Inflation": {
        "desc": "Consumer Price Index year-over-year across the world's major economies. Inflation is monetary debasement made visible — a planned reduction in purchasing power. Every major central bank targets 2% annual inflation by design, meaning your money is intended to halve in value every 35 years. When actual inflation exceeds the target, the erosion accelerates.",
        "unit_note": "YoY % change in Consumer Price Index. Positive = prices rose vs same month last year. 'Core' CPI strips out food and energy — the things people actually buy.",
    },
    "Central Bank Balance Sheets": {
        "desc": "Total assets held by the world's major central banks — the most direct measure of monetary expansion. When central banks buy government bonds (QE), their balance sheets expand and new reserves are conjured from nothing. The post-2008 and post-COVID expansions represent the largest coordinated monetary creation in human history. Watch the curves: every peak is followed by a tentative unwind; every crisis is met with a new expansion.",
        "unit_note": "Fed in USD billions. ECB in EUR billions. BoJ in JPY trillions. Currencies are not directly comparable — the shapes (expansion speed, drawdown) are the signal. BoJ balance sheet has exceeded 100% of Japan's GDP, an experiment with no modern precedent.",
    },
}

# ---------------------------------------------------------------------------
# Instrument registry
# ---------------------------------------------------------------------------
INSTRUMENTS: list[dict] = [
    # -- Equity --
    {"id": "sp500",    "label": "S&P 500",      "group": "Equity",                         "unit": "pts",     "dp": 0, "yf": "^GSPC",
     "desc": "The 500 largest US companies by market cap. The world's most-watched equity benchmark, now heavily concentrated in a handful of mega-cap tech names (Magnificent 7 ≈ 30% of the index)."},
    {"id": "ndx",      "label": "Nasdaq 100",   "group": "Equity",                         "unit": "pts",     "dp": 0, "yf": "^NDX",
     "desc": "The 100 largest non-financial Nasdaq-listed companies. Technology-dominated — the purest large-cap proxy for growth-stock sentiment and AI/tech cycle health."},
    {"id": "ftse",     "label": "FTSE 100",     "group": "Equity",                         "unit": "pts",     "dp": 0, "yf": "^FTSE",
     "desc": "The 100 largest London-listed companies. Skewed toward energy, mining, banking, and pharma with heavy global revenue exposure — less a UK economic indicator than a global multinational index."},
    {"id": "dax",      "label": "DAX",          "group": "Equity",                         "unit": "pts",     "dp": 0, "yf": "^GDAXI",
     "desc": "Germany's benchmark 40-company index. Heavily weighted toward industrials, chemicals, and automotive — a reliable proxy for European manufacturing and global trade cycle health."},
    {"id": "nikkei",   "label": "Nikkei 225",   "group": "Equity",                         "unit": "pts",     "dp": 0, "yf": "^N225",
     "desc": "225 companies on the Tokyo Stock Exchange. Japan's benchmark, shaped by decades of Bank of Japan intervention including direct ETF purchases and yield-curve control."},
    {"id": "shanghai", "label": "Shanghai",     "group": "Equity",                         "unit": "pts",     "dp": 2, "yf": "000001.SS",
     "desc": "All shares on the Shanghai Stock Exchange. A proxy for China's economy, though state intervention, capital controls, and opaque accounting make it a noisier signal than Western peers."},

    # -- Bonds (FRED) --
    # bond_yield=True: period_change_pct is shown as absolute pp change (not % of a %)
    # so "-0.5" means "yields fell 0.5 percentage points" — avoids confusing "-13%" badges
    # on series whose value is already a percentage.
    {"id": "us10y",    "label": "US 10Y",       "group": "Bonds",                          "unit": "%",       "dp": 2, "fred": "DGS10",          "bond_yield": True, "invert_color": True,
     "desc": "The global 'risk-free rate' benchmark — the single most important price in finance. All other assets are priced relative to it. Fed policy drives the short end; inflation expectations and growth drive the long end."},
    {"id": "us2y",     "label": "US 2Y",        "group": "Bonds",                          "unit": "%",       "dp": 2, "fred": "DGS2",           "bond_yield": True, "invert_color": True,
     "desc": "Closely tracks Fed short-term rate expectations. The 2Y/10Y yield-curve inversion (2Y above 10Y) has preceded every US recession since the 1970s."},
    {"id": "uk10y",    "label": "UK 10Y",       "group": "Bonds",                          "unit": "%",       "dp": 2, "fred": "IRLTLT01GBM156N", "bond_yield": True, "invert_color": True,
     "desc": "UK government Gilt yield. Became systemically important after the 2022 LDI crisis, where rapid yield rises triggered pension fund margin calls and nearly collapsed the UK financial system."},
    {"id": "de10y",    "label": "Germany 10Y",  "group": "Bonds",                          "unit": "%",       "dp": 2, "fred": "IRLTLT01DEM156N", "bond_yield": True, "invert_color": True,
     "desc": "The eurozone 'risk-free' benchmark. German Bunds are the reference rate for EUR-denominated debt — spreads over Bunds are watched as eurozone systemic risk signals."},
    {"id": "jp10y",    "label": "Japan 10Y",    "group": "Bonds",                          "unit": "%",       "dp": 2, "fred": "IRLTLT01JPM156N", "bond_yield": True, "invert_color": True,
     "desc": "Japanese Government Bond yield. The Bank of Japan held 10Y yields near zero via Yield Curve Control for years — one of the largest monetary experiments ever conducted, now being carefully unwound."},
    {"id": "ru10y",    "label": "Russia 10Y",   "group": "Bonds",                          "unit": "%",       "dp": 2, "fred": "IRLTLT01RUM156N", "bond_yield": True, "invert_color": True,
     "desc": "Russian 10-year government bond yield (OECD data, available through mid-2018 — OECD ceased reporting after 2022 sanctions). Russia ran historically high yields reflecting inflation and geopolitical risk premia. Data ends 2018; the post-sanctions yield environment is not captured here."},

    # -- FX --
    {"id": "dxy",      "label": "DXY",          "group": "FX",                             "unit": "",        "dp": 2, "yf": "DX-Y.NYB",
     "desc": "US Dollar Index against EUR (57.6%), JPY (13.6%), GBP (11.9%), CAD (9.1%), SEK (4.2%), CHF (3.6%). Dollar strength tightens global liquidity — most commodities and EM debt are USD-denominated."},
    {"id": "gbpusd",   "label": "GBP/USD",      "group": "FX",                             "unit": "",        "dp": 4, "yf": "GBPUSD=X",
     "desc": "'Cable' — one of the oldest currency pairs. The UK's structural current account deficit and post-Brexit trade frictions mean GBP remains vulnerable to sentiment shifts."},
    {"id": "eurusd",   "label": "EUR/USD",      "group": "FX",                             "unit": "",        "dp": 4, "yf": "EURUSD=X",
     "desc": "The world's most-traded currency pair. ECB vs. Fed policy divergence drives medium-term moves. The euro is structurally challenged by a monetary union without fiscal union."},
    {"id": "usdcny",   "label": "CNY/USD",      "group": "FX",                             "unit": "",        "dp": 4, "yf": "CNY=X", "invert_values": True,
     "desc": "Chinese Yuan priced in USD — rising means the CNY strengthened vs the dollar. The PBOC manages the rate via a daily fixing band, limiting volatility. CNY internationalisation challenges USD reserve-currency dominance; the petrodollar system faces its first serious structural test."},
    {"id": "usdjpy",   "label": "JPY/USD",      "group": "FX",                             "unit": "",        "dp": 6, "yf": "JPY=X", "invert_values": True,
     "desc": "Japanese Yen priced in USD — rising means the JPY strengthened vs the dollar. Japan's near-zero rates make the yen a cheap funding currency. Sharp JPY strengthening (yen carry unwind) triggers global risk-off events, as seen dramatically in August 2024."},
    {"id": "usdrub",   "label": "RUB/USD",      "group": "FX",                             "unit": "",        "dp": 6, "yf": "RUB=X", "invert_values": True,
     "desc": "Russian Ruble priced in USD. The 2022 invasion of Ukraine triggered Western sanctions: the ruble briefly collapsed to ~0.0067 USD before aggressive capital controls and energy export revenues engineered a partial recovery. Post-sanctions, the rate reflects the Moscow Exchange under capital controls rather than a freely traded market. Secular trend: persistent devaluation driven by inflation differentials and geopolitical risk premium."},

    # -- Rare Earth Metals & Energy --
    {"id": "brent",    "label": "Brent Crude",  "group": "Rare Earth Metals & Energy",     "unit": "$/bbl",   "dp": 2, "fred": "DCOILBRENTEU", "invert_color": True,
     "desc": "Global oil benchmark (North Sea blend), daily price via EIA/FRED from 1987. Energy costs ripple through the entire economy — food, transport, manufacturing. OPEC+ supply decisions and geopolitical risk dominate short-term pricing."},
    {"id": "natgas",   "label": "Natural Gas",  "group": "Rare Earth Metals & Energy",     "unit": "$/MMBtu", "dp": 3, "fred": "DHHNGSP",    "invert_color": True,
     "desc": "Henry Hub Natural Gas spot price (EIA/FRED, daily from 1997). Highly volatile, weather-sensitive and regionally fragmented. Europe's energy crisis post-Ukraine invasion showed the systemic risk of gas supply concentration."},
    {"id": "gold",     "label": "Gold",         "group": "Rare Earth Metals & Energy",     "unit": "$/oz",    "dp": 0, "yf": "GC=F",
     "desc": "Monetary metal used as a store of value for 5,000 years. Supply grows only ~1.7% per year (mining). Central banks are net buyers. Rises when real interest rates fall and confidence in fiat erodes."},
    {"id": "silver",   "label": "Silver",       "group": "Rare Earth Metals & Energy",     "unit": "$/oz",    "dp": 2, "yf": "SI=F",
     "desc": "Both monetary and industrial metal — used in solar panels, electronics, and medical devices. More volatile than gold. Historically the gold/silver ratio was 16:1; it now trades around 80:1, implying significant undervaluation."},
    {"id": "copper",   "label": "Copper",       "group": "Rare Earth Metals & Energy",     "unit": "$/lb",    "dp": 3, "yf": "HG=F",
     "desc": "'Dr Copper' — the industrial metal with a PhD in economics. Its price is a leading indicator of global growth due to ubiquitous use in construction, EVs, grid infrastructure, and manufacturing."},
    {"id": "lithium",  "label": "Lithium (ETF)", "group": "Rare Earth Metals & Energy",    "unit": "$",       "dp": 2, "yf": "LIT",
     "desc": "Global X Lithium & Battery Tech ETF — the most liquid proxy for lithium prices, tracking miners, processors, and battery manufacturers across the full value chain. Spot lithium carbonate (used in EV batteries, grid storage, and consumer electronics) has no clean futures market; LIT provides the best available price signal. Lithium is the defining critical mineral of the energy transition."},

    # -- Agriculture --
    {"id": "corn",   "label": "Corn",      "group": "Agriculture", "unit": "$/bu",  "dp": 2, "yf": "ZC=F",
     "desc": "The world's most produced grain — a key input for animal feed, ethanol, and processed food. US Corn Belt weather drives global supply shocks. USDA crop reports and El Niño cycles dominate price action."},
    {"id": "wheat",  "label": "Wheat",     "group": "Agriculture", "unit": "$/bu",  "dp": 2, "yf": "ZW=F",
     "desc": "The staple grain for half the world's population. Ukraine and Russia together supply ~28% of global exports — the 2022 invasion immediately drove wheat to 14-year highs, demonstrating how geopolitical disruption becomes food price inflation."},
    {"id": "soy",    "label": "Soybeans",  "group": "Agriculture", "unit": "$/bu",  "dp": 2, "yf": "ZS=F",
     "desc": "The world's primary protein and vegetable oil crop. Critical for animal feed and biodiesel. Brazil/Argentina dominate production. US-China trade tensions hit soybean exports first — it is often the first agricultural commodity weaponised in trade wars."},
    {"id": "coffee", "label": "Coffee",    "group": "Agriculture", "unit": "$/lb",  "dp": 2, "yf": "KC=F",
     "desc": "Arabica coffee futures — the world's most-traded soft commodity by value. Supply concentrated in Brazil and Vietnam; droughts and frosts trigger sharp spikes. A leading indicator of inflation consumers feel directly."},
    {"id": "sugar",  "label": "Sugar",     "group": "Agriculture", "unit": "¢/lb",  "dp": 2, "yf": "SB=F",
     "desc": "Raw sugar #11 — the global benchmark. Brazil, India, and Thailand dominate supply. Competes with ethanol for sugarcane: when oil prices rise, diversion to ethanol reduces sugar supply and raises prices."},
    {"id": "cocoa",  "label": "Cocoa",     "group": "Agriculture", "unit": "$/t",   "dp": 0, "yf": "CC=F",
     "desc": "Cocoa bean futures, priced per metric tonne. Supply from West Africa (Ivory Coast, Ghana) accounts for ~60% of global output. Climate change, ageing trees, and disease create structural supply vulnerabilities. Reached all-time highs in 2024 driven by historic crop failures."},

    # -- Technology (mega-cap stock prices) --
    {"id": "nvda",  "label": "NVIDIA",    "group": "Technology", "unit": "$", "dp": 2, "yf": "NVDA",
     "desc": "NVIDIA — the dominant supplier of AI accelerator GPUs (H100, H200, B200). Its chips are the primary compute substrate for training and running large language models. Revenue grew from $26B (FY2023) to $130B+ (FY2025), the fastest large-company revenue growth in history. Market cap has periodically exceeded Apple, making it the world's most valuable company."},
    {"id": "googl", "label": "Alphabet",  "group": "Technology", "unit": "$", "dp": 2, "yf": "GOOGL",
     "desc": "Alphabet (Google) — controls ~90% of global search, ~70% of mobile OS via Android, and YouTube. Google Cloud is the #3 cloud provider. Faces the dual risk and opportunity of AI: LLMs threaten search dominance while DeepMind and Google Brain rank among the world's leading AI labs. Advertising revenue is its economic engine."},
    {"id": "meta",  "label": "Meta",      "group": "Technology", "unit": "$", "dp": 2, "yf": "META",
     "desc": "Meta — owns Facebook, Instagram, WhatsApp, and Threads: 3.2+ billion daily users across the world's dominant social media portfolio. Rebuilt its ad-targeting model after iOS 14 privacy changes. Now deploying $50B+ annual capex into AI infrastructure (open-source LLaMA models) and the still-unproven metaverse."},
    {"id": "amzn",  "label": "Amazon",    "group": "Technology", "unit": "$", "dp": 2, "yf": "AMZN",
     "desc": "Amazon — dominates US e-commerce (~40% share) and global cloud computing via AWS (~32% cloud market share). AWS is the profit engine subsidising everything else. Prime membership locks in ~200M households globally. Third-largest digital advertiser after Google and Meta, a fast-growing but underappreciated revenue stream."},
    {"id": "aapl",  "label": "Apple",     "group": "Technology", "unit": "$", "dp": 2, "yf": "AAPL",
     "desc": "Apple — the world's largest consumer tech company. iPhone generates ~50% of revenue but the high-margin Services segment (App Store, iCloud, Apple Pay, licensing) is the growth engine. The $3T+ market cap reflects an unmatched combination of hardware lock-in, software ecosystem, and financial engineering via buybacks. 2B+ active devices globally."},
    {"id": "msft",  "label": "Microsoft", "group": "Technology", "unit": "$", "dp": 2, "yf": "MSFT",
     "desc": "Microsoft — the most strategically positioned company for the enterprise AI era. Azure is #2 in cloud (22% share). The $13B investment in OpenAI gave it first-mover advantage in AI integration across Office 365, Windows, Bing, and GitHub Copilot. LinkedIn, gaming (Activision Blizzard), and developer tools (GitHub, npm) add unique diversification across the technology stack."},

    # -- Bitcoin Treasury (a listed company as a leveraged BTC proxy) --
    {"id": "mstr",  "label": "MicroStrategy", "group": "Bitcoin Treasury", "unit": "$", "dp": 2, "yf": "MSTR",
     "desc": "MicroStrategy (Strategy) — a software company that became a bitcoin holding vehicle, funding purchases by issuing shares and convertible debt. Tracked here as an instrument, not a position: the share price is a leveraged, equity-wrapped proxy for BTC that trades at a premium or discount to the coins behind it, and the share count is the mechanism — every issuance dilutes existing holders to buy more bitcoin."},

    # -- Market Stress + Wealth Concentration (split from one combined group) --
    {"id": "vix",          "label": "VIX",               "group": "Market Stress", "unit": "",   "dp": 2, "yf": "^VIX",
     "no_color": True,
     "thresholds": [
         {"value": 15, "color": "#22c55e", "label": "Calm"},
         {"value": 20, "color": "#facc15", "label": "Elevated"},
         {"value": 30, "color": "#f97316", "label": "High Fear"},
         {"value": 40, "color": "#f87171", "label": "Panic"},
     ],
     "desc": "The CBOE Volatility Index — the market's 'fear gauge.' Derived from S&P 500 option prices; measures expected 30-day volatility. Below 15 = complacency, 20–30 = elevated stress, above 40 = panic (2008, 2020)."},
    {"id": "hyspread",     "label": "HY Spread",         "group": "Market Stress", "unit": "%",  "dp": 2, "fred": "BAMLH0A0HYM2", "invert_color": True,
     "no_color": True,
     "thresholds": [
         {"value": 3, "color": "#22c55e", "label": "Normal"},
         {"value": 5, "color": "#f97316", "label": "Stress"},
         {"value": 8, "color": "#f87171", "label": "Crisis"},
     ],
     "desc": "ICE BofA US High Yield spread over Treasuries. Measures how much extra yield investors demand for credit risk. Historically, spreads above 5 % signal recession; above 8 % signal systemic crisis. Rising spreads signal stress."},
    {"id": "wealth_top1",  "label": "Top 1% Wealth",     "group": "Wealth Concentration", "unit": "%",  "dp": 1, "fred": "WFRBST01134",  "always_full": True, "invert_color": True,
     "desc": "Share of total US net worth held by the wealthiest 1%. Fed Distributional Financial Accounts, from Q3 1989. Quantitative easing — by inflating asset prices — has been the most effective wealth-concentration mechanism in modern history."},
    {"id": "fed_assets",   "label": "Fed Balance Sheet", "group": "Central Bank Balance Sheets", "unit": "$B", "dp": 0, "fred": "WALCL",        "always_full": True, "scale": 0.001,
     "desc": "US Federal Reserve total assets in USD billions. Near-zero before 2008, it reached $9 trillion post-COVID before quantitative tightening began. 'Quantitative Easing' is the mechanism: create reserves from nothing → buy bonds → suppress yields → inflate asset prices → enrich asset owners."},
    {"id": "cb_ecb",       "label": "ECB",               "group": "Central Bank Balance Sheets", "unit": "€B", "dp": 0, "fred": "ECBASSETSW",  "always_full": True, "scale": 0.001,
     "desc": "European Central Bank total assets in EUR billions (weekly data from 1999). Expanded dramatically during the European debt crisis (2011) and COVID QE — peaking around €8.8 trillion in 2022, more than twice Germany's GDP. Since then, QT has been tentative. The ECB simultaneously manages monetary policy for 20 economies with divergent fiscal positions, an inherent structural tension."},
    {"id": "cb_boj",       "label": "BoJ",               "group": "Central Bank Balance Sheets", "unit": "¥T",  "dp": 0, "fred": "JPNASSETS",   "always_full": True, "scale": 0.0001,
     "desc": "Bank of Japan total assets in JPY trillions (monthly from 1998). The most extreme central bank balance sheet in history: at its peak the BoJ owned >50% of all Japanese Government Bonds and directly purchased equities via ETFs. Balance sheet has exceeded 100% of Japan's GDP — unprecedented for any major economy and a structural experiment still being carefully unwound via yield-curve control normalisation."},

    # -- Government Debt (FRED / IMF) --
    # invert_color=True: rising debt is a warning sign, shown in red.
    {"id": "debt_usa", "label": "USA",          "group": "Government Debt", "unit": "% GDP",   "dp": 1, "fred": "GFDEGDQ188S",       "always_full": True, "invert_color": True,
     "desc": "US Federal Debt as % of GDP. Crossed 100 % during COVID and has not come back. Annual interest payments now exceed defence spending — fiscal dominance is becoming a constraint on monetary policy."},
    {"id": "debt_uk",  "label": "UK",           "group": "Government Debt", "unit": "% GDP",   "dp": 1, "fred": "GGGDTAGBA188N",     "always_full": True, "invert_color": True,
     "desc": "UK General Government Gross Debt as % of GDP. Post-GFC and COVID fiscal deficits accumulated rapidly. The 2022 mini-budget crisis showed how quickly markets can lose confidence in UK fiscal credibility."},
    {"id": "debt_de",  "label": "Germany",      "group": "Government Debt", "unit": "% GDP",   "dp": 1, "fred": "GGGDTADEA188N",     "always_full": True, "invert_color": True,
     "desc": "German General Government Debt. Historically the most conservative major economy — the 'Schwarze Null' balanced-budget policy. The constitutional debt brake ('Schuldenbremse') is now being challenged by defence and infrastructure needs."},
    {"id": "debt_cn",  "label": "China",        "group": "Government Debt", "unit": "% GDP",   "dp": 1, "fred": "GGGDTACNA188N",     "always_full": True, "invert_color": True,
     "desc": "Chinese General Government Debt. Official figures exclude local government financing vehicles (LGFVs) and policy banks — true consolidated leverage is estimated significantly higher than reported."},
    {"id": "debt_jp",  "label": "Japan",        "group": "Government Debt", "unit": "% GDP",   "dp": 1, "fred": "GGGDTAJPA188N",     "always_full": True, "invert_color": True,
     "desc": "Japanese General Government Debt — at ~260 % of GDP, the highest of any major economy. Sustained by domestic savings and BoJ ownership of >50 % of JGBs. A structural experiment in how long this can continue."},
    {"id": "debt_ru",  "label": "Russia",       "group": "Government Debt", "unit": "% GDP",   "dp": 1, "fred": "GGGDTARUA188N",     "always_full": True, "invert_color": True,
     "desc": "Russian General Government Debt — historically one of the lowest among major economies, below 20 % of GDP. Sustained by oil revenues and capital controls. Western sanctions post-2022 isolated Russia from international capital markets, making domestic debt self-financing a structural feature rather than a choice."},

    # -- Fiat Devaluation (purchasing-power index; 100 = start of series) --
    {"id": "pp_usd",   "label": "USD",          "group": "Fiat Devaluation", "unit": "",       "dp": 1, "fred": "CPIAUCNS",        "purchasing_power": True, "always_full": True,
     "desc": "US Dollar purchasing power since January 1913, indexed to 100. The Federal Reserve was founded in 1913. The USD has since lost over 97 % of its original purchasing power through deliberate monetary inflation."},
    {"id": "pp_gbp",   "label": "GBP",          "group": "Fiat Devaluation", "unit": "",       "dp": 1, "fred": "GBRCPIALLMINMEI", "purchasing_power": True, "always_full": True,
     "desc": "British Pound purchasing power since available OECD data. The pound, once the global reserve currency, has followed the same trajectory as the USD — gradual but relentless purchasing power erosion."},
    {"id": "pp_eur",   "label": "EUR (DEM)",    "group": "Fiat Devaluation", "unit": "",       "dp": 1, "fred": "CP0000DEM086NEST", "fred_history": "DEUCPIALLMINMEI", "purchasing_power": True, "always_full": True,
     "desc": "German/Eurozone purchasing power since available data. Germany's 1923 hyperinflation (Reichsmark) was history's most famous currency collapse. The DM and later the EUR show the same slow-burn devaluation as all fiat currencies."},
    {"id": "pp_jpy",   "label": "JPY",          "group": "Fiat Devaluation", "unit": "",       "dp": 1, "fred": "JPNCPIALLMINMEI", "purchasing_power": True, "always_full": True,
     "desc": "Japanese Yen purchasing power. Decades of deflation/near-zero inflation from the 1990s made Japan unique — purchasing power held up better than western peers. However, recent BoJ policy changes and yen weakness have resumed the devaluation trend."},
    {"id": "pp_chf",   "label": "CHF",          "group": "Fiat Devaluation", "unit": "",       "dp": 1, "fred": "CP0000CHM086NEST", "fred_history": "CHECPIALLMINMEI", "purchasing_power": True, "always_full": True,
     "desc": "Swiss Franc purchasing power. The CHF is the world's premier 'safe haven' currency — Switzerland's fiscal discipline, trade surplus, and political neutrality have made it the strongest major fiat currency of the modern era. Yet even the CHF has lost significant purchasing power over decades, proving no fiat currency is immune to debasement."},
    {"id": "pp_cny",   "label": "CNY",          "group": "Fiat Devaluation", "unit": "",       "dp": 1, "fred": "CHNCPIALLMINMEI", "purchasing_power": True, "always_full": True,
     "desc": "Chinese Yuan purchasing power. Since 1978's economic opening, China's rapid industrialisation generated both growth and persistent inflation. The PBOC manages the yuan through capital controls and the daily fixing mechanism — but monetary expansion to fund infrastructure and credit growth has steadily eroded purchasing power."},


    # -- Inflation YoY (own group) --
    # Source note: FRED's OECD-derived CPI family (CPIALLMINMEI, and the
    # CPALTT01/CPGRLE01 variants) was retired upstream. Every country series in
    # it stops at 2025-03/04, and Japan's as far back as 2021-06. Agency-sourced
    # series are unaffected, which is why USA (BLS: CPIAUCSL) still updates and
    # why Germany moved to Eurostat's HICP index (CP0000DEM086NEST, live).
    # UK, China, Japan and Russia have no free live replacement on FRED -- every
    # candidate probed was the same retired OECD data under another id. Their
    # collected history is kept and still charts; service.py reports the
    # discontinuation date rather than implying nothing was ever collected.
    {"id": "inf_usa",  "label": "USA CPI",      "group": "Inflation", "unit": "%", "dp": 1, "fred": "CPIAUCSL",        "yoy": True, "bond_yield": True, "invert_color": True,
     "desc": "Official US inflation measure, year-over-year. Critics note it underweights shelter costs, uses substitution effects, and excludes food and energy from 'core' — the things people actually buy. The 2022 spike to 9.1 % was the highest since 1981."},
    {"id": "inf_uk",   "label": "UK CPI",       "group": "Inflation", "unit": "%", "dp": 1, "fred": "GBRCPIALLMINMEI", "yoy": True, "bond_yield": True, "invert_color": True,
     "desc": "UK Consumer Price Index year-over-year. The UK experienced one of the most severe post-COVID inflation surges in the G7 — peaking above 11 % in 2022 — driven by energy exposure, labour shortages, and sterling weakness amplifying import costs."},
    {"id": "inf_de",   "label": "Germany CPI",  "group": "Inflation", "unit": "%", "dp": 1, "fred": "CP0000DEM086NEST", "fred_history": "DEUCPIALLMINMEI", "yoy": True, "bond_yield": True, "invert_color": True,
     "desc": "German Consumer Price Index year-over-year. Germany's exposure to Russian gas made it uniquely vulnerable to energy-driven inflation after 2022, reaching 8.8 % — a shock for an economy with deep cultural aversion to inflation rooted in the 1923 hyperinflation."},
    {"id": "inf_cn",   "label": "China CPI",    "group": "Inflation", "unit": "%", "dp": 1, "fred": "CHNCPIALLMINMEI", "yoy": True, "bond_yield": True, "invert_color": True,
     "desc": "Chinese Consumer Price Index year-over-year. China's post-COVID inflation dynamics have been the inverse of the West — deflationary pressures from weak domestic demand, property sector deleveraging, and excess industrial capacity have suppressed prices, creating deflation risk rather than the inflation plaguing Western economies."},
    {"id": "inf_jp",   "label": "Japan CPI",    "group": "Inflation", "unit": "%", "dp": 1, "fred": "JPNCPIALLMINMEI", "yoy": True, "bond_yield": True, "invert_color": True,
     "desc": "Japanese Consumer Price Index year-over-year. After three decades of deflation, Japan finally saw sustained above-2 % CPI from 2022 — a historic shift that challenged the Bank of Japan's Yield Curve Control policy and triggered the beginning of rate normalisation."},
    {"id": "inf_ru",   "label": "Russia CPI",   "group": "Inflation", "unit": "%", "dp": 1, "fred": "RUSCPIALLMINMEI", "yoy": True, "bond_yield": True, "invert_color": True,
     "desc": "Russian Consumer Price Index year-over-year. Russia has experienced structurally elevated inflation driven by military spending, sanctions-related supply shortages, and rouble devaluation. The central bank responded with aggressive rate hikes (key rate above 16 % by 2024) to combat persistent price pressures."},
]

# ---------------------------------------------------------------------------
# BTC Tracker tab — Bitcoin's own dedicated deep-dive (separate from the
# Global Markets grid above). Price + on-chain network fundamentals, all
# free/no-API-key sources (Blockchain.com charts, Bitnodes.io), plus three
# placeholder cards for metrics that genuinely require a paid data provider
# (Glassnode/CryptoQuant/Arkham) — no free API publishes these.
# ---------------------------------------------------------------------------

BTC_GROUP_META: dict[str, dict] = {
    "Price": {
        "desc": "Bitcoin price in USD. The hardest money ever created — a 21-million-coin cap enforced by mathematics, not central bankers.",
        "unit_note": "USD per BTC, daily close.",
    },
    "Network Security": {
        "desc": "Hash rate and difficulty measure the network's security and miner commitment. A higher hash rate makes a 51% attack exponentially more expensive; difficulty self-adjusts every 2016 blocks to hold the 10-minute block target regardless of how much compute joins or leaves. Reachable node count adds the decentralisation dimension — the number of independent computers validating and enforcing every consensus rule, with no central server. Bitnodes.io only exposes a current snapshot (no free historical API), so that series starts from whenever this dashboard first ran rather than reaching back further.",
        "unit_note": "Hash rate in EH/s (exahashes/sec). Difficulty in trillions (T). Node count is publicly reachable listening nodes (Bitnodes.io crawl); excludes unreachable/private nodes, so the true count is higher.",
    },
    "Network Usage": {
        "desc": "Active addresses and transaction count measure real adoption and demand for blockspace. Mempool size/count show the current backlog of unconfirmed transactions waiting to be mined — a proxy for short-term fee pressure. Average block size and total blockchain size show how that demand compounds over time into disk footprint — the real cost of running a full node.",
        "unit_note": "Addresses and transactions are daily counts (thousands). Mempool size in MB, mempool count in number of pending transactions. Block size in MB (SegWit max ~4MB). Blockchain size in GB, cumulative.",
    },
    "Lightning Network": {
        "desc": "Bitcoin's layer-2 instant-payment network — channels are private payment paths funded by on-chain BTC, letting value move off-chain with near-zero fees. Channel count and network capacity measure how much liquidity is committed; the node/tor/clearnet split shows how much of the network runs over privacy-preserving transport. Avg fee rate is only available as a current snapshot (mempool.space doesn't publish historical fee data), so that series builds forward from whenever this dashboard first ran.",
        "unit_note": "Capacity in BTC (total sats locked across all channels). Fee rate in ppm (parts-per-million of the payment amount) — LN's standard proportional-fee unit.",
    },
    "CoinJoin (no free API)": {
        "desc": "JoinMarket is a decentralised, market-based CoinJoin: 'makers' passively supply liquidity for a fee, 'takers' pay that fee to initiate a mixing round — no central coordinator, unlike Wasabi or Samourai Whirlpool. None of the three major CoinJoin implementations publish a free hosted API with historical order-book or volume stats (JoinMarket's order book is peer-to-peer over IRC/Nostr/Tor with no aggregator; Wasabi/Whirlpool coordinators don't expose one either). Shown here as placeholders — wire these up if you run your own JoinMarket yield-generator/ob-watcher that captures this locally.",
        "unit_note": "Unavailable without running your own JoinMarket node to capture order-book snapshots over time — no third party publishes this data for free.",
    },
    "Supply & Flow (requires paid data)": {
        "desc": "Wallet distribution, exchange flows, and whale tracking are the metrics that would show whether coins are moving toward long-term holders or sloshing around exchanges. None of these have a free, no-API-key data source — every provider that publishes them (Glassnode, CryptoQuant, Arkham) gates them behind a paid subscription. Shown here as placeholders for if/when that changes.",
        "unit_note": "Unavailable without a paid Glassnode/CryptoQuant/Arkham subscription.",
    },
}

BTC_INSTRUMENTS: list[dict] = [
    {"id": "btcusd",      "label": "BTC/USD",          "group": "Price", "unit": "$",      "dp": 0, "yf": "BTC-USD",
     "desc": "Bitcoin price in USD. Decentralised, permissionless, censorship-resistant — transitioning from speculative asset to macro reserve asset as sovereign and institutional adoption accelerates."},

    # Yahoo carries XMR-USD back to 2017 (verified). If it ever drops the pair,
    # Kraken's public OHLC endpoint (https://api.kraken.com/0/public/OHLC?pair=
    # XMRUSD&interval=1440, no API key) was tested as a working fallback --
    # ~720 days of daily candles. A switch means a new `source` value on new
    # rows, which instrument_sources records; existing rows keep saying yfinance.
    {"id": "xmrusd",      "label": "XMR/USD",          "group": "Price", "unit": "$",      "dp": 2, "yf": "XMR-USD",
     "desc": "Monero price in USD. The leading privacy coin: ring signatures, stealth addresses, and confidential transactions make amounts and counterparties opaque by default, where Bitcoin's ledger is fully public. Delisted from most regulated exchanges, which both suppresses liquidity and demonstrates the thing it's designed to resist."},

    {"id": "btc_hash",    "label": "Hash Rate",        "group": "Network Security", "unit": "EH/s",   "dp": 1, "blockchain": "hash-rate",          "scale": 1e-6,
     "desc": "Total computational power dedicated to securing Bitcoin, in exahashes/second (1 EH = 10¹⁸ hashes). Follows price cycles: miners join during bull markets and capitulate during bears, but the secular trend is always upward."},
    {"id": "btc_diff",    "label": "Difficulty",       "group": "Network Security", "unit": "T",      "dp": 1, "blockchain": "difficulty",          "scale": 1e-12,
     "desc": "Mining difficulty in trillions — how hard it is to find the next block. The only autonomous algorithm that has run continuously without human intervention since 2009."},

    {"id": "btc_addr",    "label": "Active Addresses", "group": "Network Usage", "unit": "K/day",  "dp": 0, "blockchain": "n-unique-addresses",  "scale": 0.001,
     "desc": "Daily unique Bitcoin addresses sending or receiving coins (thousands). The cleanest proxy for real network usage and adoption."},
    {"id": "btc_tx",      "label": "Transactions",     "group": "Network Usage", "unit": "K/day",  "dp": 0, "blockchain": "n-transactions",      "scale": 0.001,
     "desc": "Daily confirmed transactions processed by the Bitcoin blockchain (thousands). Lightning Network offloads micropayments off-chain, so on-chain transactions increasingly represent higher-value settlement."},
    {"id": "btc_mempool_bytes", "label": "Mempool Size", "group": "Network Usage", "unit": "MB", "dp": 2, "blockchain": "mempool-size", "scale": 1e-6,
     "desc": "Total size of transactions waiting in the mempool to be confirmed. Rising mempool size signals blockspace demand exceeding current supply — the precursor to rising fees."},
    {"id": "btc_mempool_count", "label": "Mempool Count", "group": "Network Usage", "unit": "txs", "dp": 0, "blockchain": "mempool-count",
     "desc": "Number of unconfirmed transactions currently waiting in the mempool. A sustained high count means blocks are consistently full."},

    {"id": "btc_block",   "label": "Avg Block Size",   "group": "Network Usage", "unit": "MB",     "dp": 3, "blockchain": "avg-block-size",
     "desc": "Average size of each Bitcoin block in megabytes. The theoretical maximum is ~4MB with SegWit. When blocks are consistently near-full, the mempool backlog builds and fee rates rise."},
    {"id": "btc_chainsize", "label": "Blockchain Size", "group": "Network Usage", "unit": "GB", "dp": 2, "blockchain": "blocks-size", "scale": 1e-3,
     "desc": "Cumulative size of the entire Bitcoin blockchain on disk, from the genesis block in 2009 to today. The real cost of running a full node — growing block size or transaction volume compounds this over time."},

    {"id": "btc_nodes",   "label": "Reachable Nodes",  "group": "Network Security", "unit": "nodes", "dp": 0, "bitnodes": True,
     "desc": "Count of publicly reachable Bitcoin full nodes crawled by Bitnodes.io. Each one independently validates every block and transaction against Bitcoin's consensus rules — the mechanism that makes the network trustless. Excludes unreachable/private nodes (e.g. behind NAT/Tor without inbound), so the true count is higher."},

    {"id": "ln_channels",  "label": "Channel Count",     "group": "Lightning Network", "unit": "channels", "dp": 0, "mempool_ln": "channel_count",
     "desc": "Number of open, publicly announced Lightning channels. Each channel is a funded, bidirectional payment path — more channels mean more routes for a payment to find its way across the network."},
    {"id": "ln_capacity", "label": "Network Capacity", "group": "Lightning Network", "unit": "BTC", "dp": 1, "mempool_ln": "total_capacity", "scale": 1e-8,
     "desc": "Total BTC locked across all public Lightning channels. This is capital committed off-chain to enable instant, near-zero-fee routing — the network's aggregate liquidity."},
    {"id": "ln_nodes",     "label": "Total Nodes",      "group": "Lightning Network", "unit": "nodes", "dp": 0, "mempool_ln_latest": "node_count",
     "desc": "Total known Lightning nodes (clearnet + Tor + unannounced). Only available as a current snapshot from mempool.space, so this series builds forward from whenever this dashboard first ran."},
    {"id": "ln_tor_nodes", "label": "Tor-only Nodes",   "group": "Lightning Network", "unit": "nodes", "dp": 0, "mempool_ln": "tor_nodes",
     "desc": "Lightning nodes reachable only via Tor hidden services. Running over Tor hides your node's IP from the public network graph — a meaningful privacy upgrade for anyone self-hosting a node as part of a sovereign stack."},
    {"id": "ln_clearnet_nodes", "label": "Clearnet Nodes", "group": "Lightning Network", "unit": "nodes", "dp": 0, "mempool_ln": "clearnet_nodes",
     "desc": "Lightning nodes reachable over the open internet (IPv4/IPv6), without Tor. Easier to route through and typically more reliable, at the cost of exposing the node's IP address publicly."},
    {"id": "ln_avg_fee",   "label": "Avg Fee Rate",     "group": "Lightning Network", "unit": "ppm", "dp": 0, "mempool_ln_latest": "avg_fee_rate",
     "desc": "Network-wide average routing fee rate, in parts-per-million of the payment amount — LN's standard proportional fee unit. Skewed upward by a long tail of high-fee nodes; the typical (median) node charges far less. Only available as a current snapshot, so this series builds forward from whenever this dashboard first ran."},

    {"id": "cj_makers", "label": "Active Makers", "group": "CoinJoin (no free API)", "unit": "makers", "dp": 0,
     "desc": "Count of active liquidity providers ('makers') on JoinMarket's public order book at any given time — the supply side of its fee market.",
     "unavailable": "JoinMarket's order book is peer-to-peer (IRC/Nostr/Tor messaging), not centrally hosted — no free API aggregates and publishes historical maker counts. Requires running your own yield-generator/ob-watcher instance to capture this."},
    {"id": "cj_maker_fee", "label": "Maker Fee Rate", "group": "CoinJoin (no free API)", "unit": "%", "dp": 4,
     "desc": "Typical relative fee makers charge takers for supplying liquidity to a JoinMarket CoinJoin round — the price of that round's privacy.",
     "unavailable": "Same constraint as Active Makers — JoinMarket's order book has no free, centrally-hosted historical API. Requires running your own node to capture order-book snapshots over time."},
    {"id": "cj_mix_volume", "label": "CoinJoin Volume", "group": "CoinJoin (no free API)", "unit": "BTC", "dp": 0,
     "desc": "Total BTC mixed across CoinJoin coordinators (Wasabi, Samourai Whirlpool, JoinMarket) — the clearest signal of how much of the network is actively using privacy-preserving transactions.",
     "unavailable": "No coordinator publishes a free, aggregated historical API — only static academic research datasets exist (e.g. crocs-muni/coinjoin-analysis), not a live feed this dashboard can poll."},

    {"id": "btc_wallets_1plus", "label": "Wallets ≥ 1 BTC", "group": "Supply & Flow (requires paid data)", "unit": "%", "dp": 2,
     "desc": "Share of Bitcoin addresses holding at least 1 BTC.",
     "unavailable": "Requires a paid Glassnode/CoinMetrics subscription (address balance-tier distribution) — no free API publishes this."},
    {"id": "btc_exchange_flow", "label": "Exchange Netflow", "group": "Supply & Flow (requires paid data)", "unit": "BTC", "dp": 0,
     "desc": "Net BTC flowing into (positive) or out of (negative) known exchange wallets — a proxy for accumulation vs. distribution pressure.",
     "unavailable": "Requires a paid CryptoQuant/Glassnode Exchange Flows subscription — no free API publishes this."},
    {"id": "btc_whale_tracking", "label": "Whale Wallet Activity", "group": "Supply & Flow (requires paid data)", "unit": "BTC", "dp": 0,
     "desc": "Large-holder ('whale') wallet accumulation or distribution activity.",
     "unavailable": "Requires paid entity-clustering data (Arkham/Nansen/Glassnode) — no free API publishes this."},
]

# How many calendar days to look back per period (for yfinance and non-always_full FRED)
_PERIOD_DAYS: dict[str, int] = {
    "1mo":  35,
    "3mo":  95,
    "1y":   375,
    "3y":   1100,
    "5y":   1840,
    "10y":  3660,
    "25y":  9132,
    "50y":  18265,
}


# ---------------------------------------------------------------------------
# Public accessors
#
# The source project kept two separate universes (INSTRUMENTS for the Global
# Markets grid, BTC_INSTRUMENTS for the BTC Tracker) because it had two tabs.
# With four tabs driven by GROUP_TABS, the split no longer carries meaning --
# they are one catalogue, filtered by tab.
# ---------------------------------------------------------------------------

# Which crypto assets are tracked, from CRYPTO_ASSETS (default: BTC only).
#
# Everything on the Crypto tab besides the two price rows is Bitcoin's own
# plumbing -- hashrate, mempool, Lightning, CoinJoin -- so it follows BTC
# rather than standing on its own. XMR is opt-in: which coins someone tracks
# says something about them, and a shipped default should not say it for them.
_CRYPTO_PRICE_ASSET = {"btcusd": "BTC", "xmrusd": "XMR"}
_ENABLED_ASSETS = frozenset(a.upper() for a in settings.crypto_assets)


def _crypto_enabled(inst: dict) -> bool:
    return _CRYPTO_PRICE_ASSET.get(inst["id"], "BTC") in _ENABLED_ASSETS


CRYPTO_INSTRUMENTS: list[dict] = [i for i in BTC_INSTRUMENTS if _crypto_enabled(i)]

ALL_INSTRUMENTS: list[dict] = INSTRUMENTS + CRYPTO_INSTRUMENTS

# A tab with nothing left on it is dropped rather than rendered blank. Only
# reachable through CRYPTO_ASSETS today: switching every crypto asset off
# should take the Crypto tab out of the navigation, not leave an empty page.
TABS: list[dict[str, str]] = [
    t for t in _ALL_TABS
    if any(GROUP_TABS.get(i["group"]) == t["id"] for i in ALL_INSTRUMENTS)
]
ALL_GROUP_META: dict[str, dict] = {**GROUP_META, **BTC_GROUP_META}

PERIOD_DAYS = _PERIOD_DAYS
DEFAULT_PERIOD = "1y"

# Instruments with no upstream source key are documented gaps -- metrics that
# genuinely need a paid provider (Glassnode/CryptoQuant) or a node you run
# yourself (JoinMarket). They render as explained placeholders, and ingest
# skips them rather than logging a failure every six hours.
SOURCE_KEYS = ("yf", "fred", "blockchain", "bitnodes", "mempool_ln", "mempool_ln_latest")


def tab_ids() -> list[str]:
    return [t["id"] for t in TABS]


def tab_by_id(tab_id: str) -> dict | None:
    return next((t for t in TABS if t["id"] == tab_id), None)


def instruments_for_tab(tab_id: str) -> list[dict]:
    return [i for i in ALL_INSTRUMENTS if GROUP_TABS.get(i["group"]) == tab_id]


def groups_for_tab(tab_id: str) -> dict[str, dict]:
    """Group metadata for one tab, in the order the instruments define."""
    seen: dict[str, dict] = {}
    for inst in instruments_for_tab(tab_id):
        if inst["group"] not in seen:
            seen[inst["group"]] = ALL_GROUP_META.get(inst["group"], {})
    return seen


def instrument_by_id(instrument_id: str) -> dict | None:
    return next((i for i in ALL_INSTRUMENTS if i["id"] == instrument_id), None)


def fetchable_instruments() -> list[dict]:
    """Everything ingest should try to fetch (i.e. has an upstream source)."""
    return [i for i in ALL_INSTRUMENTS if any(k in i for k in SOURCE_KEYS)]


def _validate() -> None:
    """Catch registry mistakes at import rather than as a blank tab later."""
    groups = {i["group"] for i in ALL_INSTRUMENTS}
    unmapped = groups - set(GROUP_TABS)
    if unmapped:
        raise ValueError(f"instrument groups missing from GROUP_TABS: {sorted(unmapped)}")

    declared = [t["id"] for t in _ALL_TABS]
    bad_tabs = {g: t for g, t in GROUP_TABS.items() if t not in declared}
    if bad_tabs:
        raise ValueError(f"GROUP_TABS points at unknown tabs: {bad_tabs}")

    ids = [i["id"] for i in ALL_INSTRUMENTS]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate instrument ids: {sorted(dupes)}")

    missing_meta = {g for g in groups if g not in ALL_GROUP_META}
    if missing_meta:
        raise ValueError(f"groups with no metadata: {sorted(missing_meta)}")


_validate()

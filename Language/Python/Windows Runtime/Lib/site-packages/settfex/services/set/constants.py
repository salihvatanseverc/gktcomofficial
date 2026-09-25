"""Constants and configuration for SET (Stock Exchange of Thailand) services."""

# Base URL for all SET API endpoints
SET_BASE_URL = "https://www.set.or.th"

# API endpoints
SET_STOCK_LIST_ENDPOINT = "/api/set/stock/list"
SET_STOCK_HIGHLIGHT_DATA_ENDPOINT = "/api/set/stock/{symbol}/highlight-data"
SET_STOCK_PROFILE_ENDPOINT = "/api/set/stock/{symbol}/profile"
SET_COMPANY_PROFILE_ENDPOINT = "/api/set/company/{symbol}/profile"
SET_CORPORATE_ACTION_ENDPOINT = "/api/set/stock/{symbol}/corporate-action"
SET_STOCK_SHAREHOLDER_ENDPOINT = "/api/set/stock/{symbol}/shareholder"
SET_NVDR_HOLDER_ENDPOINT = "/api/set/stock/{symbol}/nvdr-holder"
SET_BOARD_OF_DIRECTOR_ENDPOINT = "/api/set/company/{symbol}/board-of-director"
SET_TRADING_STAT_ENDPOINT = "/api/set/factsheet/{symbol}/trading-stat"
SET_PRICE_PERFORMANCE_ENDPOINT = "/api/set/factsheet/{symbol}/price-performance"
SET_FINANCIAL_BALANCE_SHEET_ENDPOINT = "/api/set/factsheet/{symbol}/financialstatement"
SET_FINANCIAL_INCOME_STATEMENT_ENDPOINT = "/api/set/factsheet/{symbol}/financialstatement"
SET_FINANCIAL_CASH_FLOW_ENDPOINT = "/api/set/factsheet/{symbol}/financialstatement"
SET_STOCK_CHART_QUOTATION_ENDPOINT = "/api/set/stock/{symbol}/chart-quotation"
SET_STOCK_LATEST_HISTORICAL_TRADING_ENDPOINT = "/api/set/stock/{symbol}/latest-historical-trading"

# Live quote block behind the header of a set.or.th quote page: trading sign (SP/NC/NP/CB/XD...),
# price/OHLC, best bid/offer, and reference data. Serves EVERY listed security type (stocks,
# warrants, DWs, DRs, ETFs), which no other endpoint here does — the stock list has no sign field
# and index compositions cover common stocks only. It has NO language dimension: ?lang=en and
# ?lang=th return byte-identical payloads (both nameEN and nameTH are always present).
# An unknown symbol answers HTTP 404 {"message": "Invalid Stock Name"}.
SET_STOCK_INFO_ENDPOINT = "/api/set/stock/{symbol}/info"

# DR (Depositary Receipt) profile — issuer/underlying/conversion-ratio details plus the
# "Indicative Price" TradingView chart link shown on the DR quote page. Answers non-DR
# symbols (even valid ones like CPALL) with HTTP 404 {"message": "Invalid DR"}.
SET_DR_PROFILE_ENDPOINT = "/api/set/dr/{symbol}/profile"

# News search API (market-wide; uses ?lang=). sourceId=company filters to company disclosures —
# unrecognized sourceId values are silently ignored (returns ALL sources). fromDate/toDate are
# dd/MM/yyyy ONLY (ISO dates -> HTTP 400).
SET_NEWS_SEARCH_ENDPOINT = "/api/set/news/search"

# Market holiday calendar. NOTE: this is the ONLY endpoint on the /api/cms/v1/ prefix — every other
# www.set.or.th endpoint here lives under /api/set/. It takes ?lang= (like the stock and news
# endpoints), NOT ?language= (the index endpoints). The API answers anything it does not like —
# an unrecognized lang value, a missing lang, or a year it does not serve — with a bare HTTP 401
# and an empty body, and it also returns 401 transiently on perfectly valid requests.
# Only the CURRENT year is served (live-probed 2026-07-27: 2026 → 200, 2024/2025/2027/2028 → 401).
SET_HOLIDAY_ENDPOINT = "/api/cms/v1/holidays/year/{year}"

# Market index endpoints (note: the index API uses ?language=, not ?lang= like stock endpoints)
SET_INDEX_LIST_ENDPOINT = "/api/set/index/list"
SET_INDEX_INFO_LIST_ENDPOINT = "/api/set/index/info/list"
SET_INDEX_INFO_ENDPOINT = "/api/set/index/{symbol}/info"
SET_INDEX_COMPOSITION_ENDPOINT = "/api/set/index/{symbol}/composition"
SET_INDEX_CHART_QUOTATION_ENDPOINT = "/api/set/index/{symbol}/chart-quotation"

# Earnings Call (Opportunity Day) calendar API.
# Hosted on a separate, stateless backend (no Incapsula/cookies, no auth) — the public page
# is https://opportunity-day.setgroup.or.th/en/earnings-call.
SET_LCP_BASE_URL = "https://api.lcp.setgroup.or.th/api/v1"
SET_EARNINGS_CALL_SEARCH_ENDPOINT = "/investor/search/archive"
SET_EARNINGS_CALL_DETAIL_ENDPOINT = "/investor/vdo/{id}"
SET_EARNINGS_CALL_FILTER_ENDPOINT = "/investor/filter/{name}"
SET_OPPDAY_ORIGIN = "https://opportunity-day.setgroup.or.th"
SET_OPPDAY_REFERER = "https://opportunity-day.setgroup.or.th/"

# TradingView scanner — quotes for the legs of a DR's indicative-price expression (the
# "Indicative Price" link on SET DR pages points at th.tradingview.com). Foreign,
# unauthenticated, STATELESS host: never route through SessionManager (its auto-detect would
# mis-warm it as SET, and the batch scan is a POST, which the persistent session path does
# not support anyway). Over plain HTTP the `close` column is the last price (~15-min delayed
# for exchange legs); `lp`/`lp_time` come back null (websocket-only) — do not request them.
TRADINGVIEW_SCANNER_BASE_URL = "https://scanner.tradingview.com"
TRADINGVIEW_SCAN_ENDPOINT = "/global/scan"
TRADINGVIEW_ORIGIN = "https://th.tradingview.com"
TRADINGVIEW_REFERER = "https://th.tradingview.com/"

# Settrade (www.settrade.com) — SET Group's retail portal, which serves the IAA analyst-consensus
# research behind https://www.settrade.com/th/equities/quote/{symbol}/analyst-consensus (the
# "tableAnalystConcensus" table). That page is a client-rendered Nuxt app — the table is NOT in
# the server HTML — so these are the JSON endpoints the page's own bundle calls. No HTML parsing.
#
# Bot protection (Incapsula, live-probed 2026-08-16): BOTH a warmed www.settrade.com cookie jar
# AND a Referer on www.settrade.com are required. A warmed session with no Referer -> 403, and a
# session warmed on www.set.or.th -> 403 (Incapsula cookies are per-domain). Hence the dedicated
# SessionManager warmup_site="settrade" — never let a settrade URL fall through to the SET warmup.
# The warm URL does not need to be symbol-specific: one warmed session serves every symbol.
SETTRADE_BASE_URL = "https://www.settrade.com"
SETTRADE_WARMUP_URL = "https://www.settrade.com/th/home"

# The consensus table itself: four aggregate rows (average/median/high/low) plus one row per
# covering broker (broker, analyst, target price, recommendation, research PDF). Takes NO lang
# parameter — the th and en responses are byte-identical and `recommend` is broker-supplied
# English free text. Answers an uncovered symbol with HTTP 500, never 404 — and "uncovered"
# includes perfectly valid SET symbols (ABICO), DRs (GOOG80) and warrants (JAS-W4).
SETTRADE_ANALYST_CONSENSUS_ENDPOINT = "/api/set-fund/consensus/stock/{symbol}/consensus"

# The buy/hold/sell summary shown above the table. Takes ?lang= and an OPTIONAL ?symbol=; with
# no symbol it returns EVERY covered SET stock in one response (~48 KB) — a market-wide consensus
# screener. An unknown symbol is answered silently: HTTP 200 with "overall": [].
SETTRADE_CONSENSUS_OVERALL_ENDPOINT = "/api/set-fund/consensus/stock/overall"

# Referer template — MANDATORY on every settrade API call (see the bot-protection note above).
SETTRADE_QUOTE_REFERER = "https://www.settrade.com/th/equities/quote/{symbol}/analyst-consensus"

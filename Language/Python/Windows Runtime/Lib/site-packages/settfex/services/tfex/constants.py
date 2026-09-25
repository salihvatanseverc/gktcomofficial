"""Constants and configuration for TFEX (Thailand Futures Exchange) services."""

# Base URL for all TFEX API endpoints
TFEX_BASE_URL = "https://www.tfex.co.th"

# API endpoints
TFEX_SERIES_LIST_ENDPOINT = "/api/set/tfex/series/list"
TFEX_TRADING_STATISTICS_ENDPOINT = "/api/set/tfex/series/{symbol}/trading-statistics"
TFEX_UNDERLYING_PRICE_ENDPOINT = "/api/set/tfex/series/{symbol}/underlying-price"

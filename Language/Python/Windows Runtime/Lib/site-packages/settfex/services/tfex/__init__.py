"""TFEX (Thailand Futures Exchange) services."""

from settfex.services.tfex.constants import (
    TFEX_BASE_URL,
    TFEX_SERIES_LIST_ENDPOINT,
    TFEX_TRADING_STATISTICS_ENDPOINT,
    TFEX_UNDERLYING_PRICE_ENDPOINT,
)
from settfex.services.tfex.list import (
    TFEXSeries,
    TFEXSeriesListResponse,
    TFEXSeriesListService,
    get_series_list,
)
from settfex.services.tfex.trading_statistics import (
    TradingStatistics,
    TradingStatisticsService,
    get_trading_statistics,
)
from settfex.services.tfex.underlying_price import (
    TFEXUnderlyingPriceService,
    UnderlyingPrice,
    get_underlying_price,
)

__all__ = [
    # Constants
    "TFEX_BASE_URL",
    "TFEX_SERIES_LIST_ENDPOINT",
    "TFEX_TRADING_STATISTICS_ENDPOINT",
    "TFEX_UNDERLYING_PRICE_ENDPOINT",
    # Series List Service
    "TFEXSeries",
    "TFEXSeriesListResponse",
    "TFEXSeriesListService",
    "get_series_list",
    # Trading Statistics Service
    "TradingStatistics",
    "TradingStatisticsService",
    "get_trading_statistics",
    # Underlying Price Service
    "TFEXUnderlyingPriceService",
    "UnderlyingPrice",
    "get_underlying_price",
]

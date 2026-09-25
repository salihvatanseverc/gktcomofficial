"""SET Stock Info Service - Fetch the live quote block (sign, price, depth) for one symbol.

This is the payload behind the header of a set.or.th quote page: the trading **sign**
(``SP``/``NC``/``NP``/``CB``/``XD``…), the current price/OHLC, the best bid/offer, and the
reference data SET shows alongside them (par, tick size, 52-week range, underlying,
exercise terms for warrants/DWs, iNAV for ETFs).

It is the only endpoint in this package that carries ``sign`` for **every** listed security —
the stock list has no sign field at all, and index compositions only cover common stocks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, computed_field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.asset_type import AssetType
from settfex.services.set.constants import SET_BASE_URL, SET_STOCK_INFO_ENDPOINT

# BidOffer models the same ladder-level shape SET sends on both the index-composition rows and
# this quote block (``price`` arrives as a string); it is reused rather than duplicated.
from settfex.services.set.index.composition import BidOffer
from settfex.services.set.stock.utils import normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_or_raise


def parse_signs(sign: str | None) -> list[str]:
    """Split SET's ``sign`` string into individual uppercase sign codes.

    SET packs every active sign for a symbol into one comma-separated string
    (e.g. ``"SP, CB, CS, CC"``); an untagged security sends ``""``.

    Args:
        sign: Raw ``sign`` value from the API

    Returns:
        Sign codes in payload order, uppercased and stripped (empty list when untagged)

    Example:
        >>> parse_signs("SP, NC, NP")
        ['SP', 'NC', 'NP']
        >>> parse_signs("")
        []
    """
    return [part.strip().upper() for part in (sign or "").split(",") if part.strip()]


class Inav(BaseModel):
    """Indicative NAV block — populated for ETFs only, ``None`` for every other security type."""

    inav: float | None = Field(default=None, description="Indicative net asset value (THB)")
    change: float | None = Field(default=None, description="iNAV change from prior close")
    percent_change: float | None = Field(
        default=None, alias="percentChange", description="iNAV percentage change"
    )

    model_config = ConfigDict(populate_by_name=True)


class StockInfo(BaseModel):
    """Live quote block for a single listed security.

    Covers every security type SET lists — common stocks, foreign shares (``-F``), preferred
    (``-P``/``-Q``), warrants, DWs, DRs, ETFs and unit trusts — with the type-specific fields
    (``exercise_price``, ``maturity_date``, ``inav``, ``moneyness_status``…) left ``None`` where
    they do not apply.

    The field that has no other home in this package is :attr:`sign`; use :attr:`signs`,
    :meth:`has_sign` or :attr:`is_suspended` rather than comparing the raw string, which packs
    multiple codes together (``"SP, CB, CS, CC"``).
    """

    symbol: str = Field(description="Stock symbol/ticker")
    sign: str | None = Field(
        default=None,
        description=(
            "Active trading signs, comma-separated (e.g. 'SP', 'SP, NC'); '' when untagged. "
            "Prefer the `signs` list over parsing this string"
        ),
    )

    # --- Price and trading activity -------------------------------------------------------
    prior: float | None = Field(default=None, description="Prior session's closing price")
    last: float | None = Field(default=None, description="Latest trade price")
    open: float | None = Field(default=None, description="Opening price")
    high: float | None = Field(default=None, description="Session high")
    low: float | None = Field(default=None, description="Session low")
    average: float | None = Field(default=None, description="Volume-weighted average price")
    floor: float | None = Field(default=None, description="Floor price limit")
    ceiling: float | None = Field(default=None, description="Ceiling price limit")
    change: float | None = Field(default=None, description="Price change from prior close")
    percent_change: float | None = Field(
        default=None, alias="percentChange", description="Percentage change from prior close"
    )
    total_volume: float | None = Field(
        default=None, alias="totalVolume", description="Total traded volume (shares)"
    )
    total_value: float | None = Field(
        default=None, alias="totalValue", description="Total traded value (THB)"
    )
    tr_volume: float | None = Field(
        default=None, alias="trVolume", description="Trade-report volume (shares)"
    )
    tr_value: float | None = Field(
        default=None, alias="trValue", description="Trade-report value (THB)"
    )
    aom_volume: float | None = Field(
        default=None, alias="aomVolume", description="Automatic order matching volume (shares)"
    )
    aom_value: float | None = Field(
        default=None, alias="aomValue", description="Automatic order matching value (THB)"
    )
    bids: list[BidOffer] = Field(
        default_factory=list,
        description="Bid ladder, best first (HTTP serves the best level only; [] when halted)",
    )
    offers: list[BidOffer] = Field(
        default_factory=list,
        description="Offer ladder, best first (HTTP serves the best level only; [] when halted)",
    )

    # --- Market state ---------------------------------------------------------------------
    market_status: str | None = Field(
        default=None,
        alias="marketStatus",
        description="Trading state for this symbol ('Open2', 'Closed', 'Suspend', ...)",
    )
    market_date_time: datetime | None = Field(
        default=None,
        alias="marketDateTime",
        description="Server timestamp of this snapshot (tz-aware, Asia/Bangkok +07:00)",
    )

    # --- Reference data -------------------------------------------------------------------
    security_type: str | None = Field(
        default=None,
        alias="securityType",
        description="SET securityType code ('S', 'W', 'V', 'X', 'L', ...)",
    )
    tick_size: float | None = Field(default=None, alias="tickSize", description="Price tick size")
    name_en: str | None = Field(default=None, alias="nameEN", description="Security name (English)")
    name_th: str | None = Field(default=None, alias="nameTH", description="Security name (Thai)")
    market_name: str | None = Field(
        default=None, alias="marketName", description="Market name ('SET' or 'mai')"
    )
    industry_name: str | None = Field(
        default=None,
        alias="industryName",
        description="Industry symbol (e.g. 'SERVICE'); '' for warrants/DWs/DRs/ETFs",
    )
    sector_name: str | None = Field(
        default=None,
        alias="sectorName",
        description="Sector symbol (e.g. 'COMM'); '' for warrants/DWs/DRs/ETFs",
    )
    is_npg: bool | None = Field(
        default=None, alias="isNPG", description="Is in the non-performing group"
    )
    is_iff: bool | None = Field(
        default=None, alias="isIFF", description="Is an infrastructure fund"
    )
    is_pfund: bool | None = Field(default=None, alias="isPFUND", description="Is a property fund")
    high_52_weeks: float | None = Field(
        default=None, alias="high52Weeks", description="52-week high price"
    )
    low_52_weeks: float | None = Field(
        default=None, alias="low52Weeks", description="52-week low price"
    )
    par: float | None = Field(default=None, description="Par value per share (THB)")
    underlying: str | None = Field(
        default=None,
        description="Underlying symbol for warrants/DWs/DRs/ETFs/preferred shares; '' otherwise",
    )

    # --- Derivative / fund specifics ------------------------------------------------------
    inav: Inav | None = Field(
        default=None, description="Indicative NAV block (ETFs only, None otherwise)"
    )
    multiplier: float | None = Field(default=None, description="Contract multiplier (DWs, ETFs)")
    exercise_ratio: float | str | None = Field(
        default=None,
        alias="exerciseRatio",
        description="Exercise/conversion ratio, verbatim (e.g. '1 : 1', '3,000 : 1')",
    )
    exercise_price: float | None = Field(
        default=None, alias="exercisePrice", description="Exercise price (warrants/DWs)"
    )
    exercise_price_unit: str | None = Field(
        default=None,
        alias="exercisePriceUnit",
        description="Currency/unit of the exercise price ('THB', 'USD', 'Point')",
    )
    maturity_date: datetime | None = Field(
        default=None, alias="maturityDate", description="Maturity date (warrants/DWs)"
    )
    last_trading_date: datetime | None = Field(
        default=None, alias="lastTradingDate", description="Last trading date (warrants/DWs)"
    )
    ttm: int | None = Field(default=None, description="Time to maturity in days (warrants/DWs)")
    moneyness_status: str | None = Field(
        default=None, alias="moneynessStatus", description="Moneyness ('ITM', 'ATM', 'OTM')"
    )
    moneyness_percent: float | None = Field(
        default=None, alias="moneynessPercent", description="Moneyness magnitude (%)"
    )

    # --- Valuation ------------------------------------------------------------------------
    statistics_as_of: datetime | None = Field(
        default=None,
        alias="statisticsAsOf",
        description="As-of date of the valuation block (market cap, P/E, P/B, yield)",
    )
    market_cap: float | None = Field(
        default=None, alias="marketCap", description="Market capitalization (THB)"
    )
    pe_ratio: float | None = Field(
        default=None, alias="peRatio", description="Price-to-earnings ratio"
    )
    pb_ratio: float | None = Field(default=None, alias="pbRatio", description="Price-to-book ratio")
    dividend_yield: float | None = Field(
        default=None, alias="dividendYield", description="Dividend yield (%)"
    )
    nvdr_net_volume: float | None = Field(
        default=None, alias="nvdrNetVolume", description="NVDR net volume (shares)"
    )
    listed_share: float | None = Field(
        default=None, alias="listedShare", description="Number of listed shares"
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def signs(self) -> list[str]:
        """Active sign codes as a list (``"SP, NC"`` -> ``['SP', 'NC']``; ``[]`` when untagged).

        A computed field, so the parsed form survives ``model_dump()`` into Parquet/JSON.
        """
        return parse_signs(self.sign)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_suspended(self) -> bool:
        """Whether the ``SP`` (trading suspended) sign is active.

        Derived from :attr:`sign`, not from :attr:`market_status` — ``market_status`` also reads
        ``'Closed'`` outside trading hours, which says nothing about the symbol itself.
        """
        return "SP" in self.signs

    @property
    def asset_type(self) -> AssetType:
        """Asset type derived from ``security_type`` (e.g. ``"X"`` -> depositary receipt)."""
        return AssetType.from_security_type(self.security_type or "")

    @property
    def best_bid(self) -> float | None:
        """Best (highest) bid price, or ``None`` when the book is empty."""
        return self.bids[0].price if self.bids else None

    @property
    def best_offer(self) -> float | None:
        """Best (lowest) offer price, or ``None`` when the book is empty."""
        return self.offers[0].price if self.offers else None

    def has_sign(self, code: str) -> bool:
        """Whether ``code`` is one of this security's active signs (case-insensitive).

        Args:
            code: Sign code to test (e.g. ``'SP'``, ``'NC'``, ``'XD'``)

        Returns:
            True if the code is active

        Example:
            >>> info.has_sign("sp")
            True
        """
        return code.strip().upper() in self.signs


class StockInfoService:
    """
    Service for fetching the live quote block (``/api/set/stock/{symbol}/info``) from SET.

    Works for every listed security type, which makes it the only way to read a symbol's
    trading ``sign`` (``SP``, ``NC``, ``NP``, ``CB``, ``XD``, ...) for warrants, DWs and DRs.

    Note there is deliberately **no** ``lang`` argument: the endpoint has no language
    dimension — ``?lang=en`` and ``?lang=th`` return byte-identical payloads, and both names
    are always present as ``name_en`` / ``name_th``.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the stock info service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> service = StockInfoService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"StockInfoService initialized with base_url={self.base_url}")

    async def fetch_stock_info(self, symbol: str) -> StockInfo:
        """
        Fetch the live quote block for a stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "ptt", "A5-W5", "GOOG80")

        Returns:
            StockInfo with sign, price, depth and reference data

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not found (HTTP 404 "Invalid Stock Name").
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = StockInfoService()
            >>> info = await service.fetch_stock_info("INGRS")
            >>> info.signs, info.is_suspended
            (['SP'], True)
        """
        data = await self.fetch_stock_info_raw(symbol)
        symbol = normalize_symbol(symbol)

        result = validate_or_raise(StockInfo, data, context=f"{symbol} (stock info)")
        logger.info(
            f"Successfully fetched stock info for {symbol}: "
            f"sign={result.sign!r}, last={result.last}, status={result.market_status!r}"
        )
        return result

    async def fetch_stock_info_raw(self, symbol: str) -> dict[str, Any]:
        """
        Fetch the quote block as a raw dictionary without Pydantic validation.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "ptt", "A5-W5", "GOOG80")

        Returns:
            Raw dictionary from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not found (HTTP 404 "Invalid Stock Name").
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = StockInfoService()
            >>> raw = await service.fetch_stock_info_raw("CPALL")
            >>> raw["sign"], raw["last"]
            ('', 45.0)
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            raise InvalidSymbolError("Stock symbol cannot be empty")

        endpoint = SET_STOCK_INFO_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}"

        logger.info(f"Fetching stock info for '{symbol}' from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)
            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                error_msg = f"Failed to fetch stock info for {symbol}: HTTP {response.status_code}"
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            data = decode_json(response.text, context=f"{symbol} (stock info)")
            if not isinstance(data, dict):
                # Guarded because the debug line below used to call .keys() on whatever arrived,
                # so a JSON array surfaced as AttributeError -- a crash, not an error contract.
                error_msg = (
                    f"Expected an object response for {symbol} (stock info), "
                    f"got {type(data).__name__}"
                )
                logger.error(error_msg)
                raise ResponseParseError(error_msg)
            logger.debug(f"Raw response keys: {list(data.keys())}")
            return data


async def get_stock_info(
    symbol: str,
    config: FetcherConfig | None = None,
) -> StockInfo:
    """
    Convenience function to fetch the live quote block for a symbol.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "ptt", "A5-W5", "GOOG80")
        config: Optional fetcher configuration

    Returns:
        StockInfo with sign, price, depth and reference data

    Raises:
        InvalidSymbolError: If the symbol is empty.
        SymbolNotFoundError: If the symbol is not found (HTTP 404 "Invalid Stock Name").
        FetchError: On other HTTP or transport failures.
        ResponseParseError: If the response cannot be parsed.

    Example:
        >>> from settfex.services.set import get_stock_info
        >>> info = await get_stock_info("INGRS")
        >>> print(info.signs, info.is_suspended, info.market_status)
        ['SP'] True Suspend
    """
    service = StockInfoService(config=config)
    return await service.fetch_stock_info(symbol=symbol)

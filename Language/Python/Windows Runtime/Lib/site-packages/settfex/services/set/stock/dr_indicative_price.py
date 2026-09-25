"""DR Indicative Price Service - Compute a DR's fair value from TradingView quotes.

A SET DR's "Indicative Price" is ``underlying price x FX rate / conversion ratio`` in THB —
the expression SET itself publishes per DR (see
:mod:`settfex.services.set.stock.profile_dr`), e.g. GOOG80 →
``NASDAQ:GOOG*FX_IDC:USDTHB/2000.0``. This service fetches every expression leg from
TradingView's scanner in ONE batch request and evaluates the expression.

TradingView specifics (live-probed 2026-08-03):

- ``POST https://scanner.tradingview.com/global/scan`` is unauthenticated and returns the
  ``close`` column as the last price — ~15-minute delayed for exchange legs
  (``update_mode: "delayed_streaming_900"``), streaming for FX_IDC legs. The ``lp``/
  ``lp_time``/``last_price`` columns come back null over plain HTTP (websocket-only) and are
  deliberately not requested.
- The host is foreign and STATELESS: never route it through SessionManager (the batch scan
  is a POST, which the persistent-session path does not support anyway) — the service forces
  ``use_session=False`` for TradingView calls while leaving the SET-host config untouched.
- Unknown tickers do not error: the scan answers HTTP 200 with the row simply missing, so
  missing rows are detected and raised explicitly.

The indicative price moves whenever the underlying market does — including while SET is
closed — so it routinely diverges from the DR's own last traded price on SET (probe: GOOG80
indicative 5.94 THB vs SET last trade 5.75 after a US-session move).
"""

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import FetchError, InvalidSymbolError, raise_for_status
from settfex.services.set.constants import (
    TRADINGVIEW_ORIGIN,
    TRADINGVIEW_REFERER,
    TRADINGVIEW_SCAN_ENDPOINT,
    TRADINGVIEW_SCANNER_BASE_URL,
)
from settfex.services.set.stock.chart_quotation import BANGKOK_TZ, Quotation
from settfex.services.set.stock.profile_dr import DrProfile, DrProfileService
from settfex.services.set.stock.utils import normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_or_raise

# Columns requested from the scanner, in wire order. `close` is the (delayed) last price;
# `lp`/`lp_time` are excluded on purpose — they are null over plain HTTP (websocket-only).
TRADINGVIEW_SCAN_COLUMNS: tuple[str, ...] = ("name", "close", "change", "currency", "update_mode")


def _build_tradingview_headers() -> dict[str, str]:
    """Build browser-like headers for the TradingView scanner.

    No cookie/authorization is required (the host is stateless). ``Content-Type`` is set
    automatically by curl_cffi when a JSON body is sent, so it is intentionally omitted.
    """
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,th;q=0.8",
        "Origin": TRADINGVIEW_ORIGIN,
        "Referer": TRADINGVIEW_REFERER,
        "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
    }


class _TradingViewScanRow(BaseModel):
    """Raw wire row: ticker + positional column values."""

    s: str = Field(description="Ticker as requested, e.g. 'NASDAQ:GOOG'")
    d: list[Any] = Field(default_factory=list, description="Column values, positional")


class _TradingViewScanResponse(BaseModel):
    """Raw wire response of POST /global/scan."""

    total_count: int = Field(default=0, alias="totalCount")
    data: list[_TradingViewScanRow] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class TradingViewQuote(BaseModel):
    """A single TradingView leg quote (delayed for exchange legs, streaming for FX)."""

    ticker: str = Field(description="TradingView ticker, e.g. 'NASDAQ:GOOG' or 'FX_IDC:USDTHB'")
    name: str | None = Field(default=None, description="Short name, e.g. 'GOOG'")
    close: float | None = Field(
        default=None, description="Last price (~15-min delayed for exchange legs)"
    )
    change: float | None = Field(default=None, description="Percent change per TradingView")
    currency: str | None = Field(default=None, description="Quote currency, e.g. 'USD'/'THB'")
    update_mode: str | None = Field(
        default=None, description="e.g. 'delayed_streaming_900' or 'streaming'"
    )

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class DrIndicativePrice(BaseModel):
    """A DR's indicative (fair-value) price computed from TradingView leg quotes."""

    symbol: str = Field(description="DR symbol, e.g. 'GOOG80'")
    indicative_price: float = Field(
        description="Indicative price in THB: product of leg closes / ratio"
    )
    ratio: float = Field(description="Conversion-ratio divisor from the expression (e.g. 2000.0)")
    expression: str = Field(description="Raw TradingView expression that was evaluated")
    tradingview_url: str | None = Field(
        default=None, description="SET's 'Indicative Price' TradingView chart URL"
    )
    legs: list[TradingViewQuote] = Field(description="Quotes for each expression leg, in order")
    as_of: datetime = Field(description="Computation instant (aware, Asia/Bangkok)")

    model_config = ConfigDict(populate_by_name=True)

    @property
    def underlying(self) -> TradingViewQuote | None:
        """The first non-FX leg (the underlying instrument quote)."""
        for leg in self.legs:
            if not leg.ticker.upper().startswith("FX_IDC:"):
                return leg
        return None

    @property
    def fx(self) -> TradingViewQuote | None:
        """The first FX leg (the currency-conversion quote)."""
        for leg in self.legs:
            if leg.ticker.upper().startswith("FX_IDC:"):
                return leg
        return None

    @property
    def is_delayed(self) -> bool:
        """True when any leg is delayed (exchange legs are ~15-min delayed over HTTP)."""
        return any("delayed" in (leg.update_mode or "") for leg in self.legs)

    def to_quotation(self) -> "DrIndicativeQuotation":
        """Adapt to the ``Quotation`` shape used by ``Stock.get_latest_price()``.

        ``price`` is the indicative price and ``quote_datetime`` the computation instant;
        ``volume``/``value``/``change``/``percent_change`` are ``None`` (nothing traded —
        this is a fair value, not a SET trade). The full computation stays available on
        ``.indicative``.
        """
        return DrIndicativeQuotation(
            quote_datetime=self.as_of,
            local_datetime=self.as_of.replace(tzinfo=None),
            price=self.indicative_price,
            volume=None,
            value=None,
            change=None,
            percent_change=None,
            indicative=self,
        )


class DrIndicativeQuotation(Quotation):
    """A synthesized :class:`Quotation` carrying DR indicative-price provenance.

    Returned by ``Stock.get_latest_price()`` for DRs: ``price`` is the TradingView
    indicative price (NOT a SET trade), so ``volume``/``value``/``change``/
    ``percent_change`` are ``None``. Use ``isinstance(q, DrIndicativeQuotation)`` or the
    ``.indicative`` field to tell it apart from a real SET quotation.
    """

    indicative: DrIndicativePrice = Field(
        description="The indicative-price computation behind this quotation"
    )


class DrIndicativePriceService:
    """
    Service computing DR indicative prices from TradingView's scanner API.

    Fetches all legs of a DR's indicative-price expression in one batch request and
    evaluates ``product(closes) / ratio``.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the DR indicative price service.

        Args:
            config: Optional fetcher configuration. Used as-is for the SET-host DR-profile
                fetch; for TradingView calls a copy with ``use_session=False`` is used (the
                host is stateless and must never go through SessionManager).
        """
        self.config = config
        # TradingView is a foreign, stateless host; never warm a SET session for it, and the
        # batch scan is a POST (persistent sessions are GET-only).
        self.tv_config = (config or FetcherConfig()).model_copy(update={"use_session": False})
        self.base_url = TRADINGVIEW_SCANNER_BASE_URL
        logger.info(f"DrIndicativePriceService initialized with base_url={self.base_url}")

    async def _scan(self, tickers: Sequence[str]) -> tuple[list[str], Any]:
        """POST one batch scan for ``tickers``; returns (deduped tickers, decoded JSON)."""
        requested = [ticker.strip() for ticker in tickers if ticker and ticker.strip()]
        if not requested:
            raise ValueError("tickers cannot be empty")
        deduped = list(dict.fromkeys(requested))

        url = f"{self.base_url}{TRADINGVIEW_SCAN_ENDPOINT}"
        body = {"symbols": {"tickers": deduped}, "columns": list(TRADINGVIEW_SCAN_COLUMNS)}

        logger.info(f"Fetching TradingView quotes for {deduped} from {url}")

        async with AsyncDataFetcher(config=self.tv_config) as fetcher:
            response = await fetcher.fetch(
                url, headers=_build_tradingview_headers(), method="POST", json_body=body
            )

        # AsyncDataFetcher retries exceptions only, never a non-2xx status — check explicitly
        # so a 429/5xx surfaces as FetchError instead of a misleading JSON parse error.
        if response.status_code != 200:
            error_msg = f"TradingView scan failed (HTTP {response.status_code}) for {deduped}"
            logger.error(error_msg)
            raise_for_status(response.status_code, error_msg, suggest=False)

        return deduped, decode_json(response.text, context=f"tradingview scan {deduped}")

    async def fetch_quotes(self, tickers: Sequence[str]) -> dict[str, TradingViewQuote]:
        """
        Fetch quotes for TradingView tickers in one batch scan request.

        Args:
            tickers: TradingView tickers (e.g. ``["NASDAQ:GOOG", "FX_IDC:USDTHB"]``);
                duplicates are deduped for the request.

        Returns:
            Dict keyed by UPPERCASED ticker → :class:`TradingViewQuote`.

        Raises:
            ValueError: If ``tickers`` is empty.
            FetchError: On non-2xx/transport failures, or when the scan answers 200 but a
                requested ticker's row is missing (TradingView's "unknown ticker" behavior).
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = DrIndicativePriceService()
            >>> quotes = await service.fetch_quotes(["NASDAQ:GOOG", "FX_IDC:USDTHB"])
            >>> print(quotes["NASDAQ:GOOG"].close, quotes["FX_IDC:USDTHB"].close)
        """
        deduped, data = await self._scan(tickers)
        scan = validate_or_raise(
            _TradingViewScanResponse, data, context=f"tradingview scan {deduped}"
        )

        quotes: dict[str, TradingViewQuote] = {}
        for row in scan.data:
            # Zip positional values with the requested columns, padding short rows with None.
            values = list(row.d[: len(TRADINGVIEW_SCAN_COLUMNS)])
            values += [None] * (len(TRADINGVIEW_SCAN_COLUMNS) - len(values))
            payload = {"ticker": row.s, **dict(zip(TRADINGVIEW_SCAN_COLUMNS, values, strict=True))}
            quotes[row.s.upper()] = validate_or_raise(
                TradingViewQuote, payload, context=f"tradingview quote {row.s}"
            )

        missing = [ticker for ticker in deduped if ticker.upper() not in quotes]
        if missing:
            raise FetchError(
                f"TradingView returned no data for ticker(s) {missing} (requested {deduped})"
            )
        return quotes

    async def fetch_quotes_raw(self, tickers: Sequence[str]) -> dict[str, Any]:
        """
        Fetch the raw batch-scan response as a dictionary without per-row validation.

        Args:
            tickers: TradingView tickers to scan.

        Returns:
            Raw scan response, e.g. ``{"totalCount": 2, "data": [{"s": ..., "d": [...]}]}``.

        Raises:
            ValueError: If ``tickers`` is empty.
            FetchError: On non-2xx or transport failures.
            ResponseParseError: If the response cannot be parsed or is not a JSON object.
        """
        deduped, data = await self._scan(tickers)
        if not isinstance(data, dict):
            raise ResponseParseError(
                f"Expected a JSON object for tradingview scan {deduped}, got {type(data).__name__}"
            )
        return data

    async def fetch_indicative_price(
        self, symbol: str, *, profile: DrProfile | None = None
    ) -> DrIndicativePrice:
        """
        Compute the indicative price for a DR symbol.

        Args:
            symbol: DR symbol (e.g., "GOOG80", "MICRON01")
            profile: Optional pre-fetched :class:`DrProfile` (skips the profile request —
                pass it when you already hold one, e.g. from ``Stock.get_dr_profile()``)

        Returns:
            DrIndicativePrice with the THB fair value, the per-leg quotes, and provenance

        Raises:
            InvalidSymbolError: If the symbol is empty.
            SymbolNotFoundError: If the symbol is not a DR (profile fetch 404).
            FetchError: When no usable expression exists, a leg quote is missing or has a
                null close, or on HTTP/transport failures.
            ResponseParseError: If a response cannot be parsed.

        Example:
            >>> service = DrIndicativePriceService()
            >>> price = await service.fetch_indicative_price("GOOG80")
            >>> print(f"{price.symbol}: {price.indicative_price:.2f} THB "
            ...       f"(underlying {price.underlying.close} {price.underlying.currency})")
        """
        symbol = normalize_symbol(symbol)
        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        if profile is None:
            profile = await DrProfileService(config=self.config).fetch_dr_profile(symbol)

        expression = profile.indicative_expression
        if expression is None:
            raise FetchError(
                f"No usable indicative price expression for DR '{symbol}' "
                f"(indicativePriceSymbol and indicativePriceUrl both missing/unparseable)",
                symbol=symbol,
            )

        quotes = await self.fetch_quotes(expression.tickers)

        closes: list[float] = []
        null_legs: list[str] = []
        for ticker in expression.tickers:
            close = quotes[ticker.upper()].close
            if close is None:
                null_legs.append(ticker)
            else:
                closes.append(close)
        if null_legs:
            raise FetchError(
                f"TradingView returned a null close for leg(s) {null_legs} of DR '{symbol}' "
                f"(expression {expression.expression!r})",
                symbol=symbol,
            )

        indicative = math.prod(closes) / expression.ratio
        result = DrIndicativePrice(
            symbol=symbol,
            indicative_price=indicative,
            ratio=expression.ratio,
            expression=expression.expression,
            tradingview_url=profile.indicative_price_url,
            legs=[quotes[ticker.upper()] for ticker in expression.tickers],
            as_of=datetime.now(BANGKOK_TZ),
        )
        logger.info(
            f"Indicative price for {symbol}: {indicative:.4f} THB "
            f"(legs={[(leg.ticker, leg.close) for leg in result.legs]}, ratio={expression.ratio})"
        )
        return result


# Convenience function for quick access
async def get_dr_indicative_price(
    symbol: str,
    config: FetcherConfig | None = None,
) -> DrIndicativePrice:
    """
    Convenience function to compute a DR's indicative price from TradingView.

    Args:
        symbol: DR symbol (e.g., "GOOG80", "MICRON01")
        config: Optional fetcher configuration

    Returns:
        DrIndicativePrice with the THB fair value and per-leg quotes

    Example:
        >>> from settfex.services.set.stock import get_dr_indicative_price
        >>> price = await get_dr_indicative_price("GOOG80")
        >>> print(f"{price.symbol}: {price.indicative_price:.2f} THB (delayed={price.is_delayed})")
    """
    service = DrIndicativePriceService(config=config)
    return await service.fetch_indicative_price(symbol)

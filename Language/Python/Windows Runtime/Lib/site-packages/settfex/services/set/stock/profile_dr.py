"""SET DR Profile Service - Fetch Depositary Receipt details incl. the TradingView link.

The ``/api/set/dr/{symbol}/profile`` endpoint backs the DR quote page
(https://www.set.or.th/en/market/product/dr/quote/GOOG80/price) and returns issuer and
underlying details, the conversion ratio, and the "Indicative Price" TradingView chart link —
an ``underlying x FX / ratio`` fair-value expression such as
``NASDAQ:GOOG*FX_IDC:USDTHB/2000.0``.

Quirks (live-probed 2026-08-03):

- Non-DR symbols — including perfectly valid listed ones like ``CPALL`` — get HTTP 404 with
  body ``{"message": "Invalid DR"}``. A 404 here therefore means "not a DR", not "unknown
  symbol", so the raised :class:`~settfex.exceptions.SymbolNotFoundError` deliberately skips
  the "did you mean?" suggestion (it would suggest the symbol back to you).
- ``indicativePriceSymbol`` is sometimes ``null`` (e.g. HERMES80, BYDCOM80, NDX01) while
  ``indicativePriceUrl`` was always present — :attr:`DrProfile.indicative_expression`
  recovers the expression from the URL's ``symbol`` query parameter in that case.
"""

from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.asset_type import AssetType
from settfex.services.set.constants import SET_BASE_URL, SET_DR_PROFILE_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_or_raise


class IndicativePriceExpression(BaseModel):
    """Parsed form of a DR indicative-price expression (``NASDAQ:GOOG*FX_IDC:USDTHB/2000.0``)."""

    expression: str = Field(description="Raw TradingView expression")
    tickers: list[str] = Field(
        description="Ordered TradingView tickers (the multiplied legs, 1..n)"
    )
    ratio: float = Field(description="Trailing conversion-ratio divisor (1.0 when absent)")

    model_config = ConfigDict(populate_by_name=True)


def parse_indicative_price_expression(expression: str) -> IndicativePriceExpression:
    """Parse a TradingView indicative-price expression defensively.

    Grammar observed live: ``{EXCHANGE}:{TICKER}*FX_IDC:{CCY}THB/{ratio}`` — only the
    structure is assumed: an optional trailing ``/<float>`` conversion-ratio divisor and one
    or more ``*``-separated TradingView tickers. Exchange names and the FX-leg shape are not
    validated (HKEX tickers are numeric, e.g. ``HKEX:1211``; future DRs may differ).

    Args:
        expression: Raw expression string.

    Returns:
        IndicativePriceExpression with tickers and ratio.

    Raises:
        ValueError: If the expression is empty, has no tickers, or carries a non-numeric or
            non-positive ratio.
    """
    raw = expression.strip()
    if not raw:
        raise ValueError("Indicative price expression is empty")
    head, sep, tail = raw.rpartition("/")
    if sep:
        try:
            ratio = float(tail.strip())
        except ValueError as exc:
            raise ValueError(
                f"Invalid ratio {tail!r} in indicative price expression {raw!r}"
            ) from exc
        if ratio <= 0:
            raise ValueError(f"Non-positive ratio {ratio} in indicative price expression {raw!r}")
    else:
        head = raw
        ratio = 1.0
    tickers = [ticker.strip() for ticker in head.split("*") if ticker.strip()]
    if not tickers:
        raise ValueError(f"No tickers in indicative price expression {raw!r}")
    return IndicativePriceExpression(expression=raw, tickers=tickers, ratio=ratio)


def _expression_from_url(url: str | None) -> str | None:
    """Recover the raw expression from a TradingView chart URL's ``symbol`` query parameter.

    ``https://th.tradingview.com/chart/?symbol=NASDAQ%3AGOOG*FX_IDC%3AUSDTHB%2F2000.0`` →
    ``NASDAQ:GOOG*FX_IDC:USDTHB/2000.0`` (percent-decoding is handled by ``parse_qs``).
    """
    if not url:
        return None
    try:
        query = urlsplit(url).query
    except ValueError:
        return None
    values = parse_qs(query).get("symbol")
    return values[0] if values else None


class DrProfile(BaseModel):
    """Model for DR (Depositary Receipt) profile data.

    Every field except ``symbol`` is optional — the payload shape was live-probed but is not
    documented by SET, so absent/null keys must not break validation.
    """

    symbol: str = Field(description="DR symbol/ticker (e.g. 'GOOG80')")
    name: str | None = Field(default=None, description="DR name")
    market: str | None = Field(default=None, description="Market (SET)")
    issuer: str | None = Field(default=None, description="Issuer symbol (e.g. 'KTB')")
    issuer_name: str | None = Field(
        default=None, alias="issuerName", description="Issuer full name"
    )
    url: str | None = Field(default=None, description="Issuer website URL")
    address: str | None = Field(default=None, description="Issuer address")
    telephone: str | None = Field(default=None, description="Issuer telephone")
    fax: str | None = Field(default=None, description="Issuer fax")
    security_type: str | None = Field(
        default=None, alias="securityType", description="Security type code ('X' for DRs)"
    )
    security_type_name: str | None = Field(
        default=None, alias="securityTypeName", description="Security type display name"
    )
    status: str | None = Field(default=None, description="Listing status (Listed, etc.)")
    first_trade_date: datetime | None = Field(
        default=None, alias="firstTradeDate", description="Date of first trade"
    )
    conversion_ratio: str | None = Field(
        default=None,
        alias="conversionRatio",
        description="DR-per-underlying ratio, verbatim (e.g. '2,000 : 1')",
    )
    ipo: float | None = Field(default=None, description="IPO price")
    par: float | None = Field(default=None, description="Par value")
    listed_share: int | None = Field(
        default=None, alias="listedShare", description="Number of listed DR units"
    )
    currency: str | None = Field(default=None, description="Trading currency (THB)")
    isin: str | None = Field(default=None, description="ISIN code")
    dr_type: str | None = Field(default=None, alias="drType", description="DR type description")
    offering_type: str | None = Field(
        default=None, alias="offeringType", description="Offering type (e.g. 'Direct Listing')"
    )
    underlying: str | None = Field(
        default=None, description="Underlying instrument symbol (e.g. 'GOOG')"
    )
    underlying_name: str | None = Field(
        default=None, alias="underlyingName", description="Underlying instrument full name"
    )
    underlying_class_name: str | None = Field(
        default=None,
        alias="underlyingClassName",
        description="Underlying class (e.g. 'Foreign Common Stock')",
    )
    underlying_exchange: str | None = Field(
        default=None, alias="underlyingExchange", description="Underlying's home exchange"
    )
    underlying_url: str | None = Field(
        default=None, alias="underlyingUrl", description="Underlying info URL"
    )
    fractional_trade: bool | None = Field(
        default=None, alias="fractionalTrade", description="True for fractional (DRx) trading"
    )
    outstanding_share: float | None = Field(
        default=None, alias="outstandingShare", description="Outstanding DR units"
    )
    outstanding_date: datetime | None = Field(
        default=None, alias="outstandingDate", description="Outstanding units as-of date"
    )
    listing_detail: Any = Field(
        default=None, alias="listingDetail", description="Listing detail (null in all probes)"
    )
    memorandum_url: str | None = Field(
        default=None, alias="memorandumUrl", description="Listing memorandum PDF URL"
    )
    trading_session: str | None = Field(
        default=None,
        alias="tradingSession",
        description="Trading session (e.g. 'Day & Night Session')",
    )
    indicative_price_symbol: str | None = Field(
        default=None,
        alias="indicativePriceSymbol",
        description=(
            "TradingView fair-value expression (e.g. 'NASDAQ:GOOG*FX_IDC:USDTHB/2000.0'); "
            "sometimes null — see indicative_expression"
        ),
    )
    indicative_price_url: str | None = Field(
        default=None,
        alias="indicativePriceUrl",
        description="TradingView chart URL for the indicative price ('Indicative Price' menu)",
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )

    @property
    def tradingview_url(self) -> str | None:
        """The SET "Indicative Price" TradingView chart URL for this DR."""
        return self.indicative_price_url

    @property
    def indicative_expression(self) -> IndicativePriceExpression | None:
        """Parsed indicative-price expression, or ``None`` when unavailable/unparseable.

        Prefers ``indicative_price_symbol``; when that is null (it sometimes is), recovers
        the expression from ``indicative_price_url``'s ``symbol`` query parameter. Logs and
        returns ``None`` on a parse failure — never raises.
        """
        raw = self.indicative_price_symbol or _expression_from_url(self.indicative_price_url)
        if not raw:
            return None
        try:
            return parse_indicative_price_expression(raw)
        except ValueError as exc:
            logger.warning(f"Unparseable indicative price expression for {self.symbol}: {exc}")
            return None

    @property
    def asset_type(self) -> AssetType:
        """Asset type derived from ``security_type`` (a DR profile is normally ``X`` → dr)."""
        return AssetType.from_security_type(self.security_type)


class DrProfileService:
    """
    Service for fetching DR (Depositary Receipt) profile data from the SET API.

    Provides issuer/underlying details, the conversion ratio, and the TradingView
    "Indicative Price" link for DR symbols such as GOOG80 or MICRON01.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the DR profile service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = DrProfileService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"DrProfileService initialized with base_url={self.base_url}")

    async def _fetch_payload(self, symbol: str, lang: Language) -> tuple[str, Any]:
        """Fetch and decode the DR profile payload; shared by both fetch tiers.

        Returns the normalized symbol together with the decoded JSON. The status check runs
        on BOTH tiers so a 404 surfaces as ``SymbolNotFoundError``, never as a JSON parse
        error on the HTML/error body.
        """
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        endpoint = SET_DR_PROFILE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching DR profile for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Symbol-specific referer on the DR quote page is what the site itself sends;
            # critical for the Incapsula bot-detection bypass.
            referer = f"https://www.set.or.th/en/market/product/dr/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            response = await fetcher.fetch(url, headers=headers)

            if response.status_code != 200:
                if response.status_code == 404:
                    # The endpoint 404s for every non-DR symbol (body {"message":"Invalid DR"}),
                    # including valid listed stocks — suggest=False, or the suggester would
                    # answer "CPALL not found — did you mean 'CPALL'?".
                    error_msg = (
                        f"Failed to fetch DR profile for {symbol}: HTTP 404 — "
                        f"'{symbol}' is not a DR (the endpoint answers non-DR symbols "
                        f"with 'Invalid DR')"
                    )
                    # Logged at debug, not error: a 404 here means "not a DR", which is a routine
                    # answer — Stock.get_latest_price() uses it as the DR probe for EVERY symbol,
                    # so logging it as an error would cry wolf on every ordinary stock.
                    logger.debug(error_msg)
                else:
                    error_msg = (
                        f"Failed to fetch DR profile for {symbol}: HTTP {response.status_code}"
                    )
                    logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol, suggest=False)

            return symbol, decode_json(response.text, context=f"{symbol} (dr-profile)")

    async def fetch_dr_profile(self, symbol: str, lang: Language = "en") -> DrProfile:
        """
        Fetch DR profile data for a specific symbol.

        Args:
            symbol: DR symbol (e.g., "GOOG80", "MICRON01", "goog80")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            DrProfile with issuer/underlying details, conversion ratio, and the
            TradingView indicative-price link

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not a DR (HTTP 404 — no suggestion attached).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = DrProfileService()
            >>> profile = await service.fetch_dr_profile("GOOG80")
            >>> print(f"{profile.underlying} @ {profile.underlying_exchange}")
            >>> print(f"TradingView: {profile.tradingview_url}")
        """
        symbol, data = await self._fetch_payload(symbol, lang)
        profile = validate_or_raise(DrProfile, data, context=f"{symbol} (dr-profile)")
        logger.info(
            f"Successfully fetched DR profile for {symbol}: "
            f"underlying={profile.underlying} ({profile.underlying_exchange}), "
            f"ratio={profile.conversion_ratio}, "
            f"indicative_expression={profile.indicative_price_symbol}"
        )
        return profile

    async def fetch_dr_profile_raw(self, symbol: str, lang: Language = "en") -> dict[str, Any]:
        """
        Fetch DR profile data as a raw dictionary without Pydantic validation.

        Useful for debugging or accessing fields not yet modeled. Unlike the raw tier of
        some older services, this still checks the HTTP status first, so a non-DR symbol
        raises ``SymbolNotFoundError`` instead of a JSON parse error.

        Args:
            symbol: DR symbol (e.g., "GOOG80")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw dictionary from the API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not a DR (HTTP 404 — no suggestion attached).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed or is not a JSON object.
        """
        symbol, data = await self._fetch_payload(symbol, lang)
        if not isinstance(data, dict):
            raise ResponseParseError(
                f"Expected a JSON object for {symbol} (dr-profile), got {type(data).__name__}"
            )
        logger.debug(f"Raw DR profile keys for {symbol}: {list(data.keys())}")
        return data


# Convenience function for quick access
async def get_dr_profile(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> DrProfile:
    """
    Convenience function to fetch DR profile data.

    Args:
        symbol: DR symbol (e.g., "GOOG80", "MICRON01")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        DrProfile with issuer/underlying details, conversion ratio, and the TradingView
        indicative-price link

    Example:
        >>> from settfex.services.set.stock import get_dr_profile
        >>> profile = await get_dr_profile("GOOG80")
        >>> print(f"{profile.symbol}: {profile.tradingview_url}")
    """
    service = DrProfileService(config=config)
    return await service.fetch_dr_profile(symbol=symbol, lang=lang)

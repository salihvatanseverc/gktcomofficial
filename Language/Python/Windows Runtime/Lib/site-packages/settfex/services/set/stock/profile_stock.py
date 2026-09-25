"""SET Stock Profile Service - Fetch profile data for individual stock symbols."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.asset_type import AssetType
from settfex.services.set.constants import SET_BASE_URL, SET_STOCK_PROFILE_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise


class StockProfile(BaseModel):
    """Model for stock profile data."""

    symbol: str = Field(description="Stock symbol/ticker")
    name: str = Field(description="Company name")
    market: str = Field(description="Market (SET, mai, etc.)")
    industry: str = Field(description="Industry code")
    industry_name: str = Field(alias="industryName", description="Industry name in readable format")
    sector: str = Field(description="Sector code")
    sector_name: str = Field(alias="sectorName", description="Sector name in readable format")
    security_type: str = Field(alias="securityType", description="Security type code")
    security_type_name: str = Field(
        alias="securityTypeName", description="Security type name in readable format"
    )
    status: str = Field(description="Listing status (Listed, Delisted, etc.)")
    listed_date: datetime | None = Field(
        alias="listedDate", description="Date when the stock was listed"
    )
    first_trade_date: datetime | None = Field(
        alias="firstTradeDate", description="Date of first trade"
    )
    last_trade_date: datetime | None = Field(
        alias="lastTradeDate", description="Date of last trade (if delisted)"
    )
    maturity_date: datetime | None = Field(
        alias="maturityDate", description="Maturity date (for bonds/warrants)"
    )
    fiscal_year_end: str | None = Field(
        alias="fiscalYearEnd", description="Fiscal year end date (DD/MM format)"
    )
    fiscal_year_end_display: str | None = Field(
        alias="fiscalYearEndDisplay", description="Fiscal year end date in display format"
    )
    account_form: str | None = Field(alias="accountForm", description="Accounting form type")
    par: float | None = Field(description="Par value per share")
    currency: str | None = Field(description="Currency code (THB, USD, etc.)")
    listed_share: int | None = Field(alias="listedShare", description="Number of listed shares")
    ipo: float | None = Field(description="Initial Public Offering price")
    isin_local: str | None = Field(alias="isinLocal", description="ISIN code for local trading")
    isin_foreign: str | None = Field(
        alias="isinForeign", description="ISIN code for foreign trading"
    )
    isin_nvdr: str | None = Field(alias="isinNVDR", description="ISIN code for NVDR")
    percent_free_float: float | None = Field(
        alias="percentFreeFloat", description="Percentage of free float shares"
    )
    foreign_limit_as_of: datetime | None = Field(
        alias="foreignLimitAsOf", description="Foreign limit data as of date"
    )
    percent_foreign_room: float | None = Field(
        alias="percentForeignRoom", description="Percentage of foreign room available"
    )
    percent_foreign_limit: float | None = Field(
        alias="percentForeignLimit", description="Percentage of foreign ownership limit"
    )
    foreign_available: int | None = Field(
        alias="foreignAvailable", description="Number of shares available for foreign ownership"
    )
    underlying: str | None = Field(description="Underlying security (for derivatives)")
    exercise_price: float | None = Field(
        alias="exercisePrice", description="Exercise price (for warrants)"
    )
    exercise_ratio: str | None = Field(
        alias="exerciseRatio", description="Exercise ratio (for warrants)"
    )
    reserved_share: int | None = Field(
        alias="reservedShare", description="Number of reserved shares"
    )
    converted_share: int | None = Field(
        alias="convertedShare", description="Number of converted shares"
    )
    last_exercise_date: datetime | None = Field(
        alias="lastExerciseDate", description="Last exercise date (for warrants)"
    )
    issued_share: int | None = Field(alias="issuedShare", description="Number of issued shares")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )

    @property
    def asset_type(self) -> AssetType:
        """Asset type derived from ``security_type`` (e.g. ``"X"`` → depositary receipt)."""
        return AssetType.from_security_type(self.security_type)


class StockProfileService:
    """
    Service for fetching stock profile data from SET API.

    This service provides async methods to fetch comprehensive profile information
    for individual stock symbols from the Stock Exchange of Thailand (SET), including
    company details, listing information, share structure, and foreign ownership limits.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the stock profile service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = StockProfileService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"StockProfileService initialized with base_url={self.base_url}")

    async def fetch_profile(self, symbol: str, lang: Language = "en") -> StockProfile:
        """
        Fetch profile data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "PTT", "CPALL", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            StockProfile containing comprehensive company and listing information

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = StockProfileService()
            >>> profile = await service.fetch_profile("PTT", lang="en")
            >>> print(f"Company: {profile.name}")
            >>> print(f"Market: {profile.market}, Sector: {profile.sector_name}")
            >>> print(f"Listed Date: {profile.listed_date}")
            >>> print(f"IPO Price: {profile.ipo} {profile.currency}")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_STOCK_PROFILE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching profile data for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = f"Failed to fetch profile for {symbol}: HTTP {response.status_code}"
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (stock-profile)")

            # Validate response using Pydantic (context-rich on failure)
            profile = validate_or_raise(StockProfile, data, context=f"{symbol} (stock-profile)")

            logger.info(
                f"Successfully fetched profile for {symbol}: "
                f"Name={profile.name}, Market={profile.market}, "
                f"Sector={profile.sector_name}, Status={profile.status}"
            )

            return profile

    async def fetch_profile_raw(self, symbol: str, lang: Language = "en") -> dict[str, Any]:
        """
        Fetch profile data as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "PTT", "CPALL", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw dictionary from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = StockProfileService()
            >>> raw_data = await service.fetch_profile_raw("PTT")
            >>> print(raw_data.keys())
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_STOCK_PROFILE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw profile data for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # SessionManager handles cookies automatically — no manual cookie needed
            data = await fetcher.fetch_json(url, headers=headers)
            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_profile(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> StockProfile:
    """
    Convenience function to fetch stock profile data.

    Args:
        symbol: Stock symbol (e.g., "PTT", "CPALL", "kbank")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        StockProfile with comprehensive company and listing information

    Example:
        >>> from settfex.services.set.stock import get_profile
        >>> # Uses SessionManager for automatic cookie handling
        >>> profile = await get_profile("PTT")
        >>> print(f"{profile.symbol}: {profile.name}")
    """
    service = StockProfileService(config=config)
    return await service.fetch_profile(symbol=symbol, lang=lang)

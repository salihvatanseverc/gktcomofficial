"""SET Stock Corporate Action Service - Fetch corporate action data for stock symbols."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_CORPORATE_ACTION_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_list_or_raise


class CorporateAction(BaseModel):
    """Model for individual corporate action."""

    symbol: str = Field(description="Stock symbol/ticker")
    name: str = Field(description="Company name (may be empty)")
    ca_type: str = Field(alias="caType", description="Corporate action type (e.g., XD, XM)")
    type: str = Field(description="Action type")

    # Common fields
    book_close_date: datetime | None = Field(
        default=None, alias="bookCloseDate", description="Book closure date"
    )
    record_date: datetime | None = Field(
        default=None, alias="recordDate", description="Record date"
    )
    remark: str | None = Field(default=None, description="Additional remarks or notes")
    x_date: datetime | None = Field(
        default=None, alias="xdate", description="Ex-date (ex-dividend or ex-rights)"
    )
    x_session: str | None = Field(default=None, alias="xSession", description="Ex-date session")

    # Dividend-specific fields (XD type)
    payment_date: datetime | None = Field(
        default=None, alias="paymentDate", description="Dividend payment date"
    )
    begin_operation: datetime | None = Field(
        default=None, alias="beginOperation", description="Operation period start date"
    )
    end_operation: datetime | None = Field(
        default=None, alias="endOperation", description="Operation period end date"
    )
    source_of_dividend: str | None = Field(
        default=None, alias="sourceOfDividend", description="Source of dividend (e.g., Net Profit)"
    )
    dividend: float | None = Field(default=None, description="Dividend amount per share")
    currency: str | None = Field(default=None, description="Currency code (e.g., Baht)")
    ratio: str | None = Field(default=None, description="Dividend ratio (e.g., '15 : 1')")
    dividend_type: str | None = Field(
        default=None, alias="dividendType", description="Type of dividend (e.g., Cash Dividend)"
    )
    approximate_payment_date: datetime | None = Field(
        default=None, alias="approximatePaymentDate", description="Approximate payment date"
    )
    tentative_dividend_flag: bool | None = Field(
        default=None, alias="tentativeDividendFlag", description="Flag for tentative dividend"
    )
    tentative_dividend: float | None = Field(
        default=None, alias="tentativeDividend", description="Tentative dividend amount"
    )
    dividend_payment: str | None = Field(
        default=None, alias="dividendPayment", description="Dividend payment amount as string"
    )

    # Meeting-specific fields (XM type)
    meeting_date: datetime | None = Field(
        default=None, alias="meetingDate", description="Meeting date and time"
    )
    agenda: str | None = Field(default=None, description="Meeting agenda items")
    venue: str | None = Field(default=None, description="Meeting venue location")
    meeting_type: str | None = Field(
        default=None, alias="meetingType", description="Type of meeting (e.g., AGM, EGM)"
    )
    inquiry_date: datetime | None = Field(
        default=None, alias="inquiryDate", description="Inquiry date"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class CorporateActionService:
    """
    Service for fetching corporate action data from SET API.

    This service provides async methods to fetch corporate action data for individual
    stock symbols from the Stock Exchange of Thailand (SET), including dividend
    announcements (XD), shareholder meetings (XM), and other corporate events.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the corporate action service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = CorporateActionService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"CorporateActionService initialized with base_url={self.base_url}")

    async def fetch_corporate_actions(
        self, symbol: str, lang: Language = "en"
    ) -> list[CorporateAction]:
        """
        Fetch corporate actions for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "AOT", "PTT", "cpall")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of CorporateAction objects with corporate action data

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = CorporateActionService()
            >>> actions = await service.fetch_corporate_actions("AOT", lang="en")
            >>> for action in actions:
            ...     if action.ca_type == "XD":
            ...         print(f"Dividend: {action.dividend} {action.currency}")
            ...         print(f"XD Date: {action.x_date}")
            ...     elif action.ca_type == "XM":
            ...         print(f"Meeting: {action.meeting_type}")
            ...         print(f"Agenda: {action.agenda}")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_CORPORATE_ACTION_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching corporate actions for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch corporate actions for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (corporate-action)")

            # Data should be a list
            if not isinstance(data, list):
                error_msg = f"Expected list response, got {type(data).__name__}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            # Validate each corporate action using Pydantic (context-rich on failure)
            corporate_actions = validate_list_or_raise(
                CorporateAction, data, context=f"{symbol} (corporate-action)"
            )

            logger.info(
                f"Successfully fetched {len(corporate_actions)} corporate action(s) for {symbol}"
            )

            # Log summary by type
            type_counts: dict[str, int] = {}
            for action in corporate_actions:
                type_counts[action.ca_type] = type_counts.get(action.ca_type, 0) + 1

            if type_counts:
                summary = ", ".join([f"{k}={v}" for k, v in type_counts.items()])
                logger.debug(f"Corporate action summary for {symbol}: {summary}")

            return corporate_actions

    async def fetch_corporate_actions_raw(
        self, symbol: str, lang: Language = "en"
    ) -> list[dict[str, Any]]:
        """
        Fetch corporate actions as raw list of dictionaries without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "AOT", "PTT", "cpall")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            Raw list of dictionaries from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = CorporateActionService()
            >>> raw_data = await service.fetch_corporate_actions_raw("AOT")
            >>> print(f"Found {len(raw_data)} actions")
            >>> for action in raw_data:
            ...     print(action.keys())
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_CORPORATE_ACTION_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw corporate actions for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/overview"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch corporate actions for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (corporate-action)")

            # Data should be a list
            if not isinstance(data, list):
                error_msg = f"Expected list response, got {type(data).__name__}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            logger.debug(f"Raw response contains {len(data)} corporate action(s)")
            return data


# Convenience function for quick access
async def get_corporate_actions(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> list[CorporateAction]:
    """
    Convenience function to fetch corporate action data.

    Args:
        symbol: Stock symbol (e.g., "AOT", "PTT", "cpall")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        List of CorporateAction objects with corporate action data

    Example:
        >>> from settfex.services.set.stock import get_corporate_actions
        >>> # Uses SessionManager for automatic cookie handling
        >>> actions = await get_corporate_actions("AOT")
        >>> for action in actions:
        ...     print(f"{action.ca_type}: {action.x_date}")
    """
    service = CorporateActionService(config=config)
    return await service.fetch_corporate_actions(symbol=symbol, lang=lang)

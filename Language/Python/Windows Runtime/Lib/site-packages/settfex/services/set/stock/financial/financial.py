"""SET Financial Service - Fetch balance sheet, income statement, and cash flow data."""

from datetime import datetime
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import (
    SET_BASE_URL,
    SET_FINANCIAL_BALANCE_SHEET_ENDPOINT,
    SET_FINANCIAL_CASH_FLOW_ENDPOINT,
    SET_FINANCIAL_INCOME_STATEMENT_ENDPOINT,
)
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import ResponseParseError, decode_json, validate_list_or_raise


class Account(BaseModel):
    """Model for individual financial account line item."""

    account_code: str = Field(alias="accountCode", description="Account code/identifier")
    account_name: str = Field(alias="accountName", description="Account name/description")
    amount: float | None = Field(description="Account amount value (in thousands)")
    adjusted: bool = Field(description="Whether the account has been adjusted")
    level: int = Field(description="Hierarchy level (-1 for totals, 0+ for details)")
    divider: int = Field(description="Divider for amount scaling (usually 1000)")
    format: str = Field(description="Display format hint (e.g., 'BU' for bold underline)")

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class FinancialStatement(BaseModel):
    """Base model for financial statement data."""

    symbol: str = Field(description="Stock symbol/ticker")
    quarter: str = Field(description="Quarter code (e.g., '6M' for half year, 'Q9' for full year)")
    year: int = Field(description="Fiscal year")
    begin_date: datetime = Field(alias="beginDate", description="Statement period begin date")
    end_date: datetime = Field(alias="endDate", description="Statement period end date")
    fs_type: str = Field(alias="fsType", description="Financial statement type (C=Consolidated)")
    account_form_id: str = Field(alias="accountFormId", description="Account form identifier")
    download_url: str = Field(alias="downloadUrl", description="URL to download full statement")
    fs_type_description: str = Field(
        alias="fsTypeDescription", description="Financial statement type description"
    )
    status: str = Field(description="Statement status (Audited/Reviewed/Unaudited)")
    is_fs_comp: bool = Field(alias="isFSComp", description="Is comparison financial statement")
    has_adjusted_account: bool = Field(
        alias="hasAdjustedAccount", description="Whether statement has adjusted accounts"
    )
    accounts: list[Account] = Field(description="List of financial account line items")
    is_restatement: bool = Field(
        alias="isRestatement", description="Whether this is a restated statement"
    )
    restatement_date: datetime | None = Field(
        alias="restatementDate", description="Restatement date if applicable"
    )

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class BalanceSheet(FinancialStatement):
    """Balance sheet financial statement."""

    pass


class IncomeStatement(FinancialStatement):
    """Income statement financial statement."""

    pass


class CashFlow(FinancialStatement):
    """Cash flow financial statement."""

    pass


class FinancialService:
    """
    Service for fetching financial data from SET API.

    This service provides async methods to fetch balance sheet, income statement,
    and cash flow data for individual stock symbols from the Stock Exchange of
    Thailand (SET).
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the financial service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> service = FinancialService()
            >>> balance_sheets = await service.fetch_balance_sheet("CPALL")
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"FinancialService initialized with base_url={self.base_url}")

    async def _fetch_financial_data(
        self,
        symbol: str,
        account_type: Literal["balance_sheet", "income_statement", "cash_flow"],
        lang: Language = "en",
    ) -> list[dict[str, Any]]:
        """
        Internal method to fetch financial data from SET API.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT")
            account_type: Type of financial statement
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of raw financial statement dictionaries

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol=symbol)
        lang = normalize_language(lang=lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Select endpoint based on account type
        endpoint_map = {
            "balance_sheet": SET_FINANCIAL_BALANCE_SHEET_ENDPOINT,
            "income_statement": SET_FINANCIAL_INCOME_STATEMENT_ENDPOINT,
            "cash_flow": SET_FINANCIAL_CASH_FLOW_ENDPOINT,
        }
        endpoint = endpoint_map[account_type].format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?accountType={account_type}&lang={lang}"

        logger.info(f"Fetching {account_type} for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/price"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url=url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch {account_type} for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} ({account_type})")

            # Validate response is a list
            if not isinstance(data, list):
                error_msg = f"Expected list response, got {type(data)}"
                logger.error(error_msg)
                raise ResponseParseError(error_msg)

            logger.info(f"Successfully fetched {len(data)} {account_type} statements for {symbol}")

            return data

    async def fetch_balance_sheet(self, symbol: str, lang: Language = "en") -> list[BalanceSheet]:
        """
        Fetch balance sheet data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of BalanceSheet statements (multiple periods)

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = FinancialService()
            >>> statements = await service.fetch_balance_sheet("CPALL", lang="en")
            >>> for stmt in statements:
            ...     print(f"{stmt.quarter} {stmt.year}: {len(stmt.accounts)} accounts")
        """
        data = await self._fetch_financial_data(
            symbol=symbol, account_type="balance_sheet", lang=lang
        )
        balance_sheets = validate_list_or_raise(
            BalanceSheet, data, context=f"{symbol} (balance-sheet)"
        )
        logger.info(f"Parsed {len(balance_sheets)} balance sheet statements for {symbol}")
        return balance_sheets

    async def fetch_balance_sheet_raw(
        self, symbol: str, lang: Language = "en"
    ) -> list[dict[str, Any]]:
        """
        Fetch balance sheet data as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of raw dictionaries from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = FinancialService()
            >>> raw_data = await service.fetch_balance_sheet_raw("CPALL")
            >>> print(f"Periods: {len(raw_data)}")
        """
        return await self._fetch_financial_data(
            symbol=symbol, account_type="balance_sheet", lang=lang
        )

    async def fetch_income_statement(
        self, symbol: str, lang: Language = "en"
    ) -> list[IncomeStatement]:
        """
        Fetch income statement data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of IncomeStatement statements (multiple periods)

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = FinancialService()
            >>> statements = await service.fetch_income_statement("CPALL", lang="en")
            >>> for stmt in statements:
            ...     print(f"{stmt.quarter} {stmt.year}: Status={stmt.status}")
        """
        data = await self._fetch_financial_data(
            symbol=symbol, account_type="income_statement", lang=lang
        )
        income_statements = validate_list_or_raise(
            IncomeStatement, data, context=f"{symbol} (income-statement)"
        )
        logger.info(f"Parsed {len(income_statements)} income statement statements for {symbol}")
        return income_statements

    async def fetch_income_statement_raw(
        self, symbol: str, lang: Language = "en"
    ) -> list[dict[str, Any]]:
        """
        Fetch income statement data as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of raw dictionaries from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = FinancialService()
            >>> raw_data = await service.fetch_income_statement_raw("CPALL")
            >>> print(f"Periods: {len(raw_data)}")
        """
        return await self._fetch_financial_data(
            symbol=symbol, account_type="income_statement", lang=lang
        )

    async def fetch_cash_flow(self, symbol: str, lang: Language = "en") -> list[CashFlow]:
        """
        Fetch cash flow data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of CashFlow statements (multiple periods)

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = FinancialService()
            >>> statements = await service.fetch_cash_flow("CPALL", lang="en")
            >>> for stmt in statements:
            ...     print(f"{stmt.quarter} {stmt.year}: {stmt.status}")
        """
        data = await self._fetch_financial_data(symbol=symbol, account_type="cash_flow", lang=lang)
        cash_flows = validate_list_or_raise(CashFlow, data, context=f"{symbol} (cash-flow)")
        logger.info(f"Parsed {len(cash_flows)} cash flow statements for {symbol}")
        return cash_flows

    async def fetch_cash_flow_raw(self, symbol: str, lang: Language = "en") -> list[dict[str, Any]]:
        """
        Fetch cash flow data as raw dictionary without Pydantic validation.

        Useful for debugging or when you need the raw API response.

        Args:
            symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            List of raw dictionaries from API

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = FinancialService()
            >>> raw_data = await service.fetch_cash_flow_raw("CPALL")
            >>> print(f"Periods: {len(raw_data)}")
        """
        return await self._fetch_financial_data(symbol=symbol, account_type="cash_flow", lang=lang)


# Convenience functions for quick access
async def get_balance_sheet(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> list[BalanceSheet]:
    """
    Convenience function to fetch balance sheet data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        List of BalanceSheet statements (multiple periods)

    Example:
        >>> from settfex.services.set.stock.financial import get_balance_sheet
        >>> statements = await get_balance_sheet("CPALL")
        >>> latest = statements[0]
        >>> print(f"{latest.quarter} {latest.year}: {len(latest.accounts)} accounts")
    """
    service = FinancialService(config=config)
    return await service.fetch_balance_sheet(symbol=symbol, lang=lang)


async def get_income_statement(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> list[IncomeStatement]:
    """
    Convenience function to fetch income statement data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        List of IncomeStatement statements (multiple periods)

    Example:
        >>> from settfex.services.set.stock.financial import get_income_statement
        >>> statements = await get_income_statement("CPALL")
        >>> latest = statements[0]
        >>> print(f"{latest.quarter} {latest.year}: {latest.status}")
    """
    service = FinancialService(config=config)
    return await service.fetch_income_statement(symbol=symbol, lang=lang)


async def get_cash_flow(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> list[CashFlow]:
    """
    Convenience function to fetch cash flow data.

    Args:
        symbol: Stock symbol (e.g., "CPALL", "PTT", "kbank")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        List of CashFlow statements (multiple periods)

    Example:
        >>> from settfex.services.set.stock.financial import get_cash_flow
        >>> statements = await get_cash_flow("CPALL")
        >>> latest = statements[0]
        >>> print(f"{latest.quarter} {latest.year}: {len(latest.accounts)} accounts")
    """
    service = FinancialService(config=config)
    return await service.fetch_cash_flow(symbol=symbol, lang=lang)

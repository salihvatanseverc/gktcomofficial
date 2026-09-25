"""SET Company Profile Service - Fetch company profile data for individual stock symbols."""

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from settfex.exceptions import InvalidSymbolError, raise_for_status
from settfex.services.set.constants import SET_BASE_URL, SET_COMPANY_PROFILE_ENDPOINT
from settfex.services.set.stock.utils import Language, normalize_language, normalize_symbol
from settfex.utils.data_fetcher import AsyncDataFetcher, FetcherConfig
from settfex.utils.parsing import decode_json, validate_or_raise


class Auditor(BaseModel):
    """Model for auditor information."""

    name: str = Field(description="Auditor's name")
    company: str = Field(description="Audit company/firm name")
    audit_end_date: datetime = Field(alias="auditEndDate", description="Audit period end date")

    model_config = ConfigDict(populate_by_name=True)


class Management(BaseModel):
    """Model for management/executive information."""

    position_code: int = Field(alias="positionCode", description="Position code")
    position: str = Field(description="Position title")
    name: str = Field(description="Executive's name")
    start_date: datetime | None = Field(
        alias="startDate",
        description="Start date in position (null when the seat is vacant or undisclosed)",
    )

    model_config = ConfigDict(populate_by_name=True)


class Capital(BaseModel):
    """Model for capital information (common or preferred)."""

    authorized_capital: float | None = Field(
        alias="authorizedCapital", description="Authorized capital amount"
    )
    paidup_capital: float | None = Field(
        alias="paidupCapital", description="Paid-up capital amount"
    )
    par: float | None = Field(description="Par value per share")
    currency: str | None = Field(description="Currency code (Baht, USD, etc.)")

    model_config = ConfigDict(populate_by_name=True)


class VotingRight(BaseModel):
    """Model for voting rights information."""

    symbol: str = Field(description="Stock symbol")
    paidup_share: int = Field(alias="paidupShare", description="Number of paid-up shares")
    ratio: str | None = Field(description="Voting ratio (e.g., '1 : 1')")

    model_config = ConfigDict(populate_by_name=True)


class VotingShare(BaseModel):
    """Model for voting shares as of a specific date."""

    as_of_date: datetime = Field(alias="asOfDate", description="Data as of date")
    share: int = Field(description="Number of voting shares")

    model_config = ConfigDict(populate_by_name=True)


class ShareStructure(BaseModel):
    """Model for share structure (common or preferred)."""

    listed_share: int | None = Field(alias="listedShare", description="Number of listed shares")
    voting_rights: list[VotingRight] = Field(
        alias="votingRights", description="Voting rights information"
    )
    treasury_shares: int | list[VotingShare] | None = Field(
        alias="treasuryShares",
        description="Number of treasury shares or list of treasury shares by date",
    )
    voting_shares: list[VotingShare] | None = Field(
        alias="votingShares", description="Voting shares by date"
    )

    model_config = ConfigDict(populate_by_name=True)


class CompanyProfile(BaseModel):
    """Model for comprehensive company profile data."""

    symbol: str = Field(description="Stock symbol/ticker")
    name: str = Field(description="Company name")
    name_remark: str = Field(alias="nameRemark", description="Additional name remarks")
    market: str = Field(description="Market (SET, mai, etc.)")
    industry: str = Field(description="Industry code")
    industry_name: str = Field(alias="industryName", description="Industry name in readable format")
    sector: str = Field(description="Sector code")
    sector_name: str = Field(alias="sectorName", description="Sector name in readable format")
    logo_url: str = Field(alias="logoUrl", description="Company logo URL")
    business_type: str = Field(alias="businessType", description="Business type description")
    url: str = Field(description="Company website URL")
    address: str = Field(description="Company address")
    telephone: str = Field(description="Contact telephone number")
    fax: str = Field(description="Contact fax number")
    email: str = Field(description="Contact email address")
    dividend_policy: str = Field(alias="dividendPolicy", description="Dividend policy details")
    cg_score: int | None = Field(alias="cgScore", description="Corporate Governance score")
    cg_remark: str = Field(alias="cgRemark", description="Corporate Governance remarks")
    cac_flag: bool = Field(alias="cacFlag", description="CAC (anti-corruption) certification flag")
    setesg_rating: str | None = Field(
        alias="setesgRating", description="SET ESG rating (AAA, AA, A, etc.)"
    )
    setesg_rating_remark: str = Field(
        alias="setesgRatingRemark", description="SET ESG rating remarks"
    )
    established_date: str = Field(alias="establishedDate", description="Company established date")
    audit_end: datetime = Field(alias="auditEnd", description="Audit period end date")
    audit_choice: str = Field(alias="auditChoice", description="Audit opinion type")
    auditors: list[Auditor] = Field(description="List of auditors")
    managements: list[Management] = Field(description="List of management/executives")
    common_capital: Capital = Field(alias="commonCapital", description="Common stock capital")
    commons_share: ShareStructure = Field(
        alias="commonsShare", description="Common share structure"
    )
    preferred_capital: Capital | None = Field(
        alias="preferredCapital", description="Preferred stock capital"
    )
    preferred_share: ShareStructure | None = Field(
        alias="preferredShare", description="Preferred share structure"
    )

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class CompanyProfileService:
    """
    Service for fetching company profile data from SET API.

    This service provides async methods to fetch comprehensive company profile information
    for individual stock symbols from the Stock Exchange of Thailand (SET), including
    company details, contact information, governance scores, auditors, management,
    and capital structure.
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the company profile service.

        Args:
            config: Optional fetcher configuration (uses defaults if None)

        Example:
            >>> # Default: Uses SessionManager for automatic cookie handling
            >>> service = CompanyProfileService()
        """
        self.config = config or FetcherConfig()
        self.base_url = SET_BASE_URL
        logger.info(f"CompanyProfileService initialized with base_url={self.base_url}")

    async def fetch_company_profile(self, symbol: str, lang: Language = "en") -> CompanyProfile:
        """
        Fetch company profile data for a specific stock symbol.

        Args:
            symbol: Stock symbol (e.g., "PTT", "CPALL", "kbank")
            lang: Language for response ('en' or 'th', default: 'en')

        Returns:
            CompanyProfile containing comprehensive company information

        Raises:
            InvalidSymbolError: If the symbol is empty.
            InvalidLanguageError: If the language is not recognized.
            SymbolNotFoundError: If the symbol is not found (HTTP 404).
            FetchError: On other HTTP or transport failures.
            ResponseParseError: If the response cannot be parsed.

        Example:
            >>> service = CompanyProfileService()
            >>> profile = await service.fetch_company_profile("CPN", lang="en")
            >>> print(f"Company: {profile.name}")
            >>> print(f"Sector: {profile.sector_name}")
            >>> print(f"CG Score: {profile.cg_score}")
            >>> print(f"ESG Rating: {profile.setesg_rating}")
            >>> print(f"Management: {len(profile.managements)} executives")
        """
        # Normalize and validate inputs
        symbol = normalize_symbol(symbol)
        lang = normalize_language(lang)

        if not symbol:
            error_msg = "Stock symbol cannot be empty"
            logger.error(error_msg)
            raise InvalidSymbolError(error_msg)

        # Build URL with symbol and language parameters
        endpoint = SET_COMPANY_PROFILE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching company profile for symbol '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/company"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch raw response - SessionManager handles cookies automatically
            response = await fetcher.fetch(url, headers=headers)

            # Check for errors
            if response.status_code != 200:
                error_msg = (
                    f"Failed to fetch company profile for {symbol}: HTTP {response.status_code}"
                )
                logger.error(error_msg)
                raise_for_status(response.status_code, error_msg, symbol=symbol)

            # Parse JSON
            data = decode_json(response.text, context=f"{symbol} (company-profile)")

            # Validate response using Pydantic (context-rich on failure)
            profile = validate_or_raise(CompanyProfile, data, context=f"{symbol} (company-profile)")

            logger.info(
                f"Successfully fetched company profile for {symbol}: "
                f"Name={profile.name}, Sector={profile.sector_name}, "
                f"CG Score={profile.cg_score}, ESG Rating={profile.setesg_rating}"
            )

            return profile

    async def fetch_company_profile_raw(self, symbol: str, lang: Language = "en") -> dict[str, Any]:
        """
        Fetch company profile data as raw dictionary without Pydantic validation.

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
            >>> service = CompanyProfileService()
            >>> raw_data = await service.fetch_company_profile_raw("CPN")
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
        endpoint = SET_COMPANY_PROFILE_ENDPOINT.format(symbol=symbol)
        url = f"{self.base_url}{endpoint}?lang={lang}"

        logger.info(f"Fetching raw company profile for '{symbol}' (lang={lang}) from {url}")

        async with AsyncDataFetcher(config=self.config) as fetcher:
            # Get optimized headers for SET API with symbol-specific referer
            # This is critical for bypassing Incapsula bot detection
            referer = f"https://www.set.or.th/en/market/product/stock/quote/{symbol}/company"
            headers = AsyncDataFetcher.get_set_api_headers(referer=referer)

            # Fetch JSON data - SessionManager handles cookies automatically
            data = await fetcher.fetch_json(url, headers=headers)
            logger.debug(
                f"Raw response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            return data  # type: ignore[no-any-return]


# Convenience function for quick access
async def get_company_profile(
    symbol: str,
    lang: Language = "en",
    config: FetcherConfig | None = None,
) -> CompanyProfile:
    """
    Convenience function to fetch company profile data.

    Args:
        symbol: Stock symbol (e.g., "PTT", "CPALL", "kbank")
        lang: Language for response ('en' or 'th', default: 'en')
        config: Optional fetcher configuration

    Returns:
        CompanyProfile with comprehensive company information

    Example:
        >>> from settfex.services.set.stock import get_company_profile
        >>> # Uses SessionManager for automatic cookie handling
        >>> profile = await get_company_profile("CPN")
        >>> print(f"{profile.symbol}: {profile.name}")
        >>> print(f"Website: {profile.url}")
        >>> print(f"ESG Rating: {profile.setesg_rating}")
    """
    service = CompanyProfileService(config=config)
    return await service.fetch_company_profile(symbol=symbol, lang=lang)

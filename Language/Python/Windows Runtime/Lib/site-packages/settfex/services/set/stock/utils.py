"""Utility functions for SET stock services."""

from typing import Literal

from loguru import logger

from settfex.exceptions import InvalidLanguageError

Language = Literal["en", "th"]
"""Static type for the two accepted language codes.

``normalize_language`` still accepts aliases like ``english``/``thai`` at runtime.
"""


def normalize_symbol(symbol: str) -> str:
    """
    Normalize stock symbol to uppercase.

    Args:
        symbol: Stock symbol to normalize (e.g., "cpall", "CPALL", "pTt")

    Returns:
        Normalized symbol in uppercase (e.g., "CPALL", "PTT")

    Example:
        >>> normalize_symbol("cpall")
        'CPALL'
        >>> normalize_symbol("KBANK")
        'KBANK'
        >>> normalize_symbol("pTt")
        'PTT'
    """
    normalized = symbol.strip().upper()
    logger.debug(f"Normalized symbol '{symbol}' to '{normalized}'")
    return normalized


def normalize_language(lang: str) -> Language:
    """
    Normalize language code to 'en' or 'th'.

    Args:
        lang: Language code to normalize (e.g., "en", "EN", "th", "TH", "english", "thai")

    Returns:
        Normalized language code: 'en' or 'th'

    Raises:
        InvalidLanguageError: If language code is not recognized

    Example:
        >>> normalize_language("en")
        'en'
        >>> normalize_language("EN")
        'en'
        >>> normalize_language("th")
        'th'
        >>> normalize_language("TH")
        'th'
        >>> normalize_language("english")
        'en'
        >>> normalize_language("thai")
        'th'
    """
    lang_lower = lang.strip().lower()

    # Map various language inputs to 'en' or 'th'
    lang_map: dict[str, Language] = {
        "en": "en",
        "eng": "en",
        "english": "en",
        "th": "th",
        "tha": "th",
        "thai": "th",
    }

    normalized = lang_map.get(lang_lower)
    if normalized is None:
        error_msg = (
            f"Invalid language '{lang}'. Must be 'en' (English) or 'th' (Thai). "
            f"Accepted values: en, eng, english, th, tha, thai"
        )
        logger.error(error_msg)
        raise InvalidLanguageError(error_msg)

    logger.debug(f"Normalized language '{lang}' to '{normalized}'")
    return normalized

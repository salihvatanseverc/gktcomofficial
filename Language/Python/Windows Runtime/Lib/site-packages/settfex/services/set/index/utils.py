"""Utility functions for SET market index services."""

from loguru import logger


def normalize_index_symbol(symbol: str) -> str:
    """
    Normalize a market index symbol by stripping surrounding whitespace.

    Unlike stock symbols, index symbols are NOT uppercased: display casing is significant
    (``sSET``, ``mai``) and mai industry query symbols carry a lowercase ``-m`` suffix
    (``AGRO-m``). The SET index API resolves path symbols case-insensitively (``sset``
    resolves to sSET), so preserving the caller's casing is always safe.

    Args:
        symbol: Index symbol to normalize (e.g., "SET50", " sSET ", "AGRO-m")

    Returns:
        Symbol with surrounding whitespace removed, casing preserved

    Example:
        >>> normalize_index_symbol(" SET50 ")
        'SET50'
        >>> normalize_index_symbol("sSET")
        'sSET'
        >>> normalize_index_symbol("AGRO-m")
        'AGRO-m'
    """
    normalized = symbol.strip()
    logger.debug(f"Normalized index symbol '{symbol}' to '{normalized}'")
    return normalized

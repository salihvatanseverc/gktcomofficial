"""
Session manager for persistent curl_cffi sessions with automatic cookie handling.

This module provides a singleton session that mimics real browser behavior by:
1. Storing cookies automatically across requests (in memory + disk cache)
2. Making initial "warm-up" requests to get Incapsula cookies
3. Reusing the same session for all API calls
4. Caching cookies to disk for fast reuse across program restarts

Key features:
- **Fast Path**: Reuse cached cookies from disk (no warmup needed)
- **Auto-Refresh**: Re-warm and update cache on expiration or failure
- **Persistent**: Survives program restarts
- **Thread-Safe**: Safe for concurrent access
"""

import asyncio
import time
from pathlib import Path
from typing import Any, ClassVar

from curl_cffi import requests
from loguru import logger

from settfex.utils.session_cache import SessionCache, get_global_cache

# Warmup site -> (homepage to visit for cookies, human label for logs).
#
# Incapsula cookies are PER-DOMAIN: a session warmed on one host is useless on another
# (live-probed 2026-08-16 — a set.or.th-warmed session gets HTTP 403 on www.settrade.com), which
# is why each host gets its own SessionManager instance and its own disk-cache entry. The homepage
# does not need to be section- or symbol-specific: one warmed session serves every URL on it.
WARMUP_URLS: dict[str, tuple[str, str]] = {
    "set": ("https://www.set.or.th/en/home", "SET"),
    "tfex": ("https://www.tfex.co.th/en/home", "TFEX"),
    "settrade": ("https://www.settrade.com/th/home", "Settrade"),
}

# Host substring -> warmup site, for get_session_for_url(). Anything unmatched uses the SET
# warmup. Order is irrelevant today (no host matches two entries) but the tuple keeps it stable.
_URL_WARMUP_SITES: tuple[tuple[str, str], ...] = (
    ("tfex.co.th", "tfex"),
    ("settrade.com", "settrade"),
)


class SessionManager:
    """
    Singleton session manager for curl_cffi with automatic cookie persistence and caching.

    This class maintains a single curl_cffi session that automatically stores
    and reuses cookies, just like a real browser. Cookies are cached to disk
    for fast reuse across program restarts.

    Architecture:
        1. **Check Cache**: Look for valid cached cookies (FAST PATH)
        2. **Use Cached**: If found and valid, create session with cached cookies
        3. **Warm Up**: If cache miss or expired, visit SET homepage
        4. **Update Cache**: Store new cookies for next time

    Performance:
        - First run: ~2-3 seconds (warmup needed)
        - Subsequent runs: ~100ms (use cached cookies)
        - After expiry: ~2-3 seconds (re-warm and cache)

    Key features:
    - Disk-based cookie cache (survives restarts)
    - Automatic cache refresh on expiration
    - Fallback to warmup on cache miss or bot detection
    - Thread-safe singleton pattern
    - Configurable browser impersonation

    Example:
        >>> # First call: warms up + caches cookies (~2-3s)
        >>> manager = SessionManager.get_instance()
        >>> await manager.ensure_initialized()
        >>> response = await manager.get("https://www.set.or.th/api/...")
        >>>
        >>> # Next call (even after restart): uses cache (~100ms)
        >>> manager = SessionManager.get_instance()
        >>> await manager.ensure_initialized()  # Fast - uses cached cookies!
        >>> response = await manager.get("https://www.set.or.th/api/...")
    """

    _instances: ClassVar[dict[str, "SessionManager"]] = {}
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(
        self,
        browser: str = "chrome120",
        cache_dir: str | Path | None = None,
        cache_ttl: int = 3600,
        enable_cache: bool = True,
        warmup_site: str = "set",
    ) -> None:
        """
        Initialize session manager.

        Args:
            browser: Browser to impersonate (default: chrome120)
            cache_dir: Directory for session cache (default: ~/.settfex/cache)
            cache_ttl: Cache time-to-live in seconds (default: 3600 = 1 hour)
            enable_cache: Enable disk caching (default: True)
            warmup_site: Site to warmup with - 'set', 'tfex' or 'settrade' (default: 'set').
                Unrecognized values fall back to the SET warmup.
        """
        self.browser = browser
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.enable_cache = enable_cache
        self.warmup_site = warmup_site.lower()
        self._session: requests.Session[Any] | None = None
        self._initialized = False
        # Serializes ensure_initialized() so a concurrent cold-start stampede warms once.
        self._init_lock = asyncio.Lock()
        self._last_warmup_time = 0.0
        self._warmup_interval = cache_ttl
        self._cache: SessionCache | None = None
        self._cache_key = f"{warmup_site}_session_{browser}"
        logger.info(
            f"SessionManager created with browser={browser}, warmup_site={warmup_site}, "
            f"cache={'enabled' if enable_cache else 'disabled'}"
        )

    @classmethod
    async def get_instance(
        cls, browser: str = "chrome120", warmup_site: str = "set"
    ) -> "SessionManager":
        """
        Get singleton instance of SessionManager.

        Creates a separate instance per site (SET / TFEX / Settrade) so each keeps its own
        cookie jar - Incapsula cookies are per-domain and are not interchangeable.

        Args:
            browser: Browser to impersonate (default: chrome120)
            warmup_site: Site to warmup with - 'set', 'tfex' or 'settrade' (default: 'set')

        Returns:
            SessionManager instance
        """
        instance_key = f"{warmup_site}_{browser}"
        async with cls._lock:
            if instance_key not in cls._instances:
                cls._instances[instance_key] = cls(browser=browser, warmup_site=warmup_site)
            return cls._instances[instance_key]

    @classmethod
    def reset_instance(cls, warmup_site: str | None = None) -> None:
        """
        Reset singleton instance (useful for testing).

        Args:
            warmup_site: Specific site to reset ('set', 'tfex' or 'settrade'), or None to
                reset all
        """
        if warmup_site:
            # Reset specific site. Match on "<site>_" and NOT on the bare site name: instance
            # keys are f"{warmup_site}_{browser}", and "settrade_chrome120".startswith("set")
            # is True - resetting SET would silently close the Settrade session as well.
            prefix = f"{warmup_site}_"
            for key in list(cls._instances.keys()):
                if key.startswith(prefix):
                    instance = cls._instances[key]
                    if instance._session:
                        instance._session.close()
                    del cls._instances[key]
            logger.debug(f"SessionManager instance for {warmup_site} reset")
        else:
            # Reset all instances
            for instance in cls._instances.values():
                if instance._session:
                    instance._session.close()
            cls._instances.clear()
            logger.debug("All SessionManager instances reset")

    async def _get_cache(self) -> SessionCache:
        """Get or create cache instance."""
        if self._cache is None:
            self._cache = await get_global_cache(
                cache_dir=self.cache_dir, default_ttl=self.cache_ttl
            )
        return self._cache

    async def _try_load_from_cache(self) -> bool:
        """
        Try to load session from cache.

        Returns:
            True if loaded from cache, False if cache miss
        """
        if not self.enable_cache:
            return False

        try:
            cache = await self._get_cache()
            cached = cache.get(self._cache_key)

            if cached is None:
                logger.debug("Cache miss - no cached session found")
                return False

            # Check if expired
            if cache.is_expired(self._cache_key, max_age=self.cache_ttl):
                logger.debug("Cache expired - will re-warm")
                cache.delete(self._cache_key)
                return False

            # Load cookies from cache
            cookies_dict = cached.get("cookies", {})
            browser = cached.get("browser", self.browser)

            if not cookies_dict:
                logger.warning("Cached session has no cookies")
                return False

            # Create new session with cached cookies
            if self._session is None:
                self._session = requests.Session()

            # Restore cookies to session
            for name, value in cookies_dict.items():
                self._session.cookies.set(name, value)

            cookie_count = len(cookies_dict)
            age = time.time() - cached.get("cached_at", 0)
            logger.success(
                f"✓ Loaded session from cache: {cookie_count} cookies "
                f"(age={age:.0f}s, browser={browser})"
            )

            self._initialized = True
            self._last_warmup_time = cached.get("warmup_time", time.time())
            return True

        except Exception as e:
            logger.error(f"Failed to load from cache: {e}")
            return False

    async def _save_to_cache(self) -> None:
        """Save current session to cache."""
        if not self.enable_cache or self._session is None:
            return

        try:
            cache = await self._get_cache()

            # Extract cookies from session
            # curl_cffi cookies can be a dict-like object or Cookie objects
            cookies_dict = {}

            if hasattr(self._session.cookies, "items"):
                # If it's a dict-like object
                for name, value in self._session.cookies.items():
                    cookies_dict[name] = value
            else:
                # If it's an iterable of Cookie objects
                for cookie in self._session.cookies:
                    if hasattr(cookie, "name") and hasattr(cookie, "value"):
                        cookies_dict[cookie.name] = cookie.value
                    else:
                        # If it's already a dict or something else, skip
                        logger.warning(f"Unknown cookie format: {type(cookie)}")
                        continue

            if not cookies_dict:
                logger.warning("No cookies to cache")
                return

            # Save to cache
            cache.set(
                self._cache_key,
                {
                    "cookies": cookies_dict,
                    "browser": self.browser,
                    "warmup_time": self._last_warmup_time,
                    "cookie_count": len(cookies_dict),
                },
                ttl=self.cache_ttl,
            )

        except Exception as e:
            logger.error(f"Failed to save to cache: {e}")
            logger.debug(f"Cookie type: {type(self._session.cookies) if self._session else 'None'}")

    async def ensure_initialized(self, force_warmup: bool = False) -> None:
        """
        Ensure session is initialized and warmed up.

        This method follows a two-path strategy:
        1. **Fast Path**: Try to load from cache (if enabled)
        2. **Slow Path**: Warm up by visiting SET homepage, then cache

        Initialization is serialized with a per-instance lock so a concurrent
        cold-start stampede warms up at most once instead of firing N duplicate
        warmup round-trips (which also risks tripping bot detection).

        Args:
            force_warmup: Force a new warm-up even if cache available
        """
        async with self._init_lock:
            await self._initialize_locked(force_warmup=force_warmup)

    async def _initialize_locked(self, force_warmup: bool = False) -> None:
        """Run the cache-load / warm-up sequence once. Caller must hold ``self._init_lock``."""
        # Fast path: Try cache first (unless forcing warmup)
        if not force_warmup and not self._initialized and await self._try_load_from_cache():
            logger.debug("Using cached session (fast path)")
            return

        # Check if warmup needed
        now = time.time()
        needs_warmup = (
            force_warmup
            or not self._initialized
            or (now - self._last_warmup_time) > self._warmup_interval
        )

        if not needs_warmup:
            return

        # Slow path: Warm up session
        # Determine warmup URL based on site (unknown sites fall back to SET, as before)
        warmup_url, site_name = WARMUP_URLS.get(self.warmup_site, WARMUP_URLS["set"])

        logger.info(f"Warming up session with {site_name} homepage visit...")

        try:
            # Create session if needed
            if self._session is None:
                self._session = requests.Session()
                logger.debug("Created new curl_cffi Session")

            # Warm-up: Visit homepage to get Incapsula cookies
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            }

            # Run in thread since curl_cffi Session is sync
            def do_warmup() -> Any:
                assert self._session is not None
                response = self._session.get(
                    warmup_url,
                    headers=headers,
                    impersonate=self.browser,
                    timeout=30,
                )
                return response

            response: Any = await asyncio.to_thread(do_warmup)

            if response.status_code == 200:
                # Check if we got cookies
                cookie_count = len(response.cookies)
                logger.success(f"✓ Session warmed up successfully (got {cookie_count} cookies)")
                self._initialized = True
                self._last_warmup_time = now

                # Save to cache for next time
                await self._save_to_cache()
            else:
                logger.warning(f"Warmup request returned {response.status_code}, continuing anyway")
                self._initialized = True
                self._last_warmup_time = now

        except Exception as e:
            logger.error(f"Failed to warm up session: {e}")
            # Don't fail - continue with unwarmed session
            self._initialized = True

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        auto_retry_on_bot_detection: bool = True,
    ) -> Any:
        """
        Make GET request using persistent session.

        If request fails with bot detection (HTTP 403/452), automatically
        re-warms the session and retries once.

        Args:
            url: URL to fetch
            headers: Optional headers to include
            timeout: Request timeout in seconds
            auto_retry_on_bot_detection: Auto re-warm and retry on bot detection

        Returns:
            Response object from curl_cffi

        Raises:
            Exception: If session not initialized or request fails after retry
        """
        await self.ensure_initialized()

        if self._session is None:
            raise RuntimeError("Session not initialized")

        logger.debug(f"GET {url} (using persistent session with cookies)")

        def do_request() -> Any:
            assert self._session is not None
            return self._session.get(
                url,
                headers=headers or {},
                impersonate=self.browser,
                timeout=timeout,
            )

        response: Any = await asyncio.to_thread(do_request)
        logger.debug(
            f"Response: {response.status_code}, cookies in session: {len(self._session.cookies)}"
        )

        # Check for bot detection and auto-retry
        if auto_retry_on_bot_detection and response.status_code in [403, 452]:
            logger.warning(
                f"Bot detection detected (HTTP {response.status_code}), "
                "re-warming session and retrying..."
            )

            # Clear cache and force re-warm
            if self.enable_cache:
                cache = await self._get_cache()
                cache.delete(self._cache_key)

            await self.ensure_initialized(force_warmup=True)

            # Retry once
            response = await asyncio.to_thread(do_request)
            logger.debug(f"Retry response: {response.status_code}")

        return response

    def close(self) -> None:
        """Close the session."""
        if self._session:
            self._session.close()
            self._session = None
            self._initialized = False
            logger.debug("Session closed")


# Convenience functions for quick access
async def get_shared_session(
    browser: str = "chrome120", warmup_site: str = "set"
) -> SessionManager:
    """
    Get the shared SessionManager instance.

    This is the recommended way to get a session with automatic cookie handling.

    Args:
        browser: Browser to impersonate (default: chrome120)
        warmup_site: Site to warmup with - 'set', 'tfex' or 'settrade' (default: 'set')

    Returns:
        Initialized SessionManager instance

    Example:
        >>> # For SET APIs
        >>> session = await get_shared_session(warmup_site="set")
        >>> response = await session.get("https://www.set.or.th/api/...")
        >>>
        >>> # For TFEX APIs
        >>> session = await get_shared_session(warmup_site="tfex")
        >>> response = await session.get("https://www.tfex.co.th/api/...")
        >>>
        >>> # For Settrade APIs (analyst consensus)
        >>> session = await get_shared_session(warmup_site="settrade")
        >>> response = await session.get("https://www.settrade.com/api/set-fund/...")
    """
    manager = await SessionManager.get_instance(browser=browser, warmup_site=warmup_site)
    await manager.ensure_initialized()
    return manager


async def get_session_for_url(url: str, browser: str = "chrome120") -> SessionManager:
    """
    Get SessionManager instance appropriate for the given URL.

    Automatically detects the warmup site (SET / TFEX / Settrade) from the URL's host. A
    settrade.com URL must never fall through to the SET warmup: Incapsula cookies are
    per-domain, and set.or.th cookies are rejected with HTTP 403 on www.settrade.com.

    Args:
        url: URL to fetch (e.g., "https://www.set.or.th/api/...", "https://www.tfex.co.th/api/..."
            or "https://www.settrade.com/api/set-fund/...")
        browser: Browser to impersonate (default: chrome120)

    Returns:
        Initialized SessionManager instance

    Example:
        >>> session = await get_session_for_url("https://www.set.or.th/api/...")
        >>> # Automatically uses SET warmup
        >>>
        >>> session = await get_session_for_url("https://www.tfex.co.th/api/...")
        >>> # Automatically uses TFEX warmup
        >>>
        >>> session = await get_session_for_url("https://www.settrade.com/api/set-fund/...")
        >>> # Automatically uses Settrade warmup
    """
    # Auto-detect warmup site based on URL host; anything unmatched uses the SET warmup.
    lowered = url.lower()
    warmup_site = next((site for host, site in _URL_WARMUP_SITES if host in lowered), "set")

    return await get_shared_session(browser=browser, warmup_site=warmup_site)

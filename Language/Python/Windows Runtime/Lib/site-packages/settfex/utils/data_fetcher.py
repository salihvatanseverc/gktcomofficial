"""Async data fetcher with Unicode/Thai language support for SET and TFEX APIs."""

import asyncio
import time
from typing import Any

from curl_cffi import requests
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from settfex.exceptions import FetchError, HTTPStatusError
from settfex.utils.parsing import decode_json

# Static default request headers, built once at import and copied per request. Copying a
# module constant is cheaper than re-materializing the literal on every fetch() call and
# keeps per-request mutations isolated (no shared-state race across concurrent fetches).
_DEFAULT_FETCH_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/json,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",  # noqa: E501
    "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
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


class FetcherConfig(BaseModel):
    """Configuration for the data fetcher."""

    browser_impersonate: str = Field(
        default="chrome120",
        description="Browser version to impersonate (e.g., 'chrome120', 'safari17')",
    )
    timeout: int = Field(default=30, ge=1, le=300, description="Request timeout in seconds")
    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum retry attempts")
    retry_delay: float = Field(
        default=1.0, ge=0.1, le=30.0, description="Base delay between retries in seconds"
    )
    rate_limit_delay: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="Delay between consecutive requests in seconds (0 = no rate limiting)",
    )
    use_session: bool = Field(
        default=True,
        description=(
            "Use persistent session with automatic cookie handling (recommended for ~100% success)"
        ),
    )
    user_agent: str | None = Field(
        default=None, description="Custom User-Agent header (auto-generated if None)"
    )

    @field_validator("browser_impersonate")
    @classmethod
    def validate_browser(cls, v: str) -> str:
        """Validate browser impersonation value."""
        valid_browsers = [
            "chrome99",
            "chrome100",
            "chrome101",
            "chrome104",
            "chrome107",
            "chrome110",
            "chrome116",
            "chrome119",
            "chrome120",
            "safari15_3",
            "safari15_5",
            "safari17_0",
            "safari17_2_1",
            "edge99",
            "edge101",
        ]
        if v not in valid_browsers:
            logger.warning(
                f"Browser '{v}' not in validated list, using anyway. "
                f"Valid options: {', '.join(valid_browsers)}"
            )
        return v


class FetchResponse(BaseModel):
    """Response from a fetch operation."""

    status_code: int = Field(description="HTTP status code")
    content: bytes = Field(description="Raw response content")
    text: str = Field(description="Response text decoded as UTF-8")
    headers: dict[str, str] = Field(default_factory=dict, description="Response headers")
    url: str = Field(description="Final URL after redirects")
    elapsed: float = Field(description="Request duration in seconds")
    encoding: str = Field(default="utf-8", description="Response encoding")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("text")
    @classmethod
    def ensure_unicode(cls, v: str) -> str:
        """Ensure text is properly decoded Unicode."""
        # This validator ensures the text field is always valid Unicode
        # If there are encoding issues, they should be caught during initialization
        return v


class AsyncDataFetcher:
    """
    Async data fetcher with Unicode/Thai language support.

    This fetcher is specifically designed for SET and TFEX APIs, providing:
    - Browser impersonation to bypass bot detection
    - Proper Unicode/Thai character handling
    - Randomized cookies for session management
    - Async-first design with sync fallback
    - Automatic retries with exponential backoff
    """

    def __init__(self, config: FetcherConfig | None = None) -> None:
        """
        Initialize the async data fetcher.

        Args:
            config: Configuration for the fetcher (uses defaults if None)
        """
        self.config = config or FetcherConfig()
        self._last_request_time: float = 0.0  # Track last request time for rate limiting
        logger.info(
            f"AsyncDataFetcher initialized with browser={self.config.browser_impersonate}, "
            f"timeout={self.config.timeout}s, rate_limit={self.config.rate_limit_delay}s"
        )

    @staticmethod
    def get_set_api_headers(referer: str = "https://www.set.or.th/en/home") -> dict[str, str]:
        """
        Get optimized headers for SET (Stock Exchange of Thailand) API requests.

        These headers are based on successful browser requests and include all
        necessary Incapsula/Imperva bot detection bypass headers.

        Args:
            referer: Referer URL (default: SET home page)

        Returns:
            Dictionary of HTTP headers optimized for SET API

        Example:
            >>> fetcher = AsyncDataFetcher()
            >>> headers = fetcher.get_set_api_headers()
            >>> response = await fetcher.fetch(url, headers=headers)
        """
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9,th-TH;q=0.8,th;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "Referer": referer,
            "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
        }

    async def _make_request(
        self,
        url: str,
        headers: dict[str, str],
        *,
        method: str = "GET",
        json_body: Any | None = None,
        data: Any | None = None,
    ) -> Any:
        """
        Make an HTTP request using either a persistent session or a standalone request.

        Uses SessionManager for automatic cookie handling if use_session=True,
        otherwise makes a standalone request. Automatically detects SET vs TFEX URLs
        and uses the appropriate warmup strategy.

        Args:
            url: URL to fetch
            headers: HTTP headers to include
            method: HTTP method, "GET" (default) or "POST"
            json_body: JSON-serializable body for a JSON POST (ignored for GET)
            data: Form body for an ``application/x-www-form-urlencoded`` POST — a dict of
                fields or a pre-encoded string (ignored for GET). Takes precedence over
                ``json_body`` when both are given (e.g. ASP.NET WebForms postbacks).

        Returns:
            Response object from curl_cffi

        Raises:
            NotImplementedError: If a non-GET method is requested with use_session=True
                (persistent sessions are GET-only; use FetcherConfig(use_session=False)).
            Exception: If request fails
        """
        if self.config.use_session:
            # Persistent (cookie-warmed) sessions support GET only. Non-GET targets — e.g.
            # the cookieless opportunity-day API — must use a standalone fetcher
            # (FetcherConfig(use_session=False)). Fail loudly rather than silently GET.
            if method != "GET":
                raise NotImplementedError(
                    f"{method} via persistent session is not supported; "
                    "use FetcherConfig(use_session=False) for non-GET requests"
                )
            # Use persistent session with automatic cookie handling
            # Auto-detects SET vs TFEX based on URL
            from settfex.utils.session_manager import get_session_for_url

            session = await get_session_for_url(url, browser=self.config.browser_impersonate)
            response = await session.get(url, headers=headers, timeout=self.config.timeout)
            logger.debug(f"Request via session: status={response.status_code}, url={url}")
            return response
        else:
            # Make standalone request (no cookie persistence)
            logger.debug(f"Making standalone {method} request to {url}")

            def do_request() -> Any:
                if method == "POST":
                    # Form-encoded body (data) takes precedence over JSON body; curl_cffi
                    # sets the matching Content-Type automatically for whichever is used.
                    if data is not None:
                        return requests.post(
                            url,
                            data=data,
                            headers=headers,
                            timeout=self.config.timeout,
                            impersonate=self.config.browser_impersonate,
                        )
                    return requests.post(
                        url,
                        json=json_body,
                        headers=headers,
                        timeout=self.config.timeout,
                        impersonate=self.config.browser_impersonate,
                    )
                return requests.get(
                    url,
                    headers=headers,
                    timeout=self.config.timeout,
                    impersonate=self.config.browser_impersonate,
                )

            try:
                response = await asyncio.to_thread(do_request)
                logger.debug(f"Request completed: status={response.status_code}, url={url}")
                return response
            except Exception as e:
                logger.error(f"Request failed: {e}", exc_info=True)
                raise

    async def fetch(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        method: str = "GET",
        json_body: Any | None = None,
        data: Any | None = None,
        decode_text: bool = True,
    ) -> FetchResponse:
        """
        Fetch data from a URL asynchronously with proper Unicode handling.

        This is the main entry point for fetching data. It automatically:
        - Uses SessionManager for automatic cookie handling (if use_session=True)
        - Handles Unicode/Thai characters correctly
        - Retries on failure with exponential backoff
        - Logs all operations for debugging

        Args:
            url: URL to fetch
            headers: Optional custom headers (merged with defaults)
            method: HTTP method, "GET" (default) or "POST". POST requires
                FetcherConfig(use_session=False).
            json_body: JSON-serializable body for a JSON POST (ignored for GET)
            data: Form body for an ``application/x-www-form-urlencoded`` POST — a dict of
                fields or a pre-encoded string. Takes precedence over ``json_body``
                (ignored for GET).
            decode_text: When True (default) decode the body to ``FetchResponse.text``
                (UTF-8, latin1 fallback). Set False for binary payloads (zip/xlsx/pdf) to
                skip the wasteful decode — ``text`` is then ``""`` and the raw bytes are
                available on ``FetchResponse.content``.

        Returns:
            FetchResponse with status, content, and metadata

        Raises:
            Exception: If request fails after all retries

        Example:
            >>> async with AsyncDataFetcher() as fetcher:
            ...     response = await fetcher.fetch("https://api.set.or.th/data")
            ...     print(response.text)  # Properly decoded Thai text
        """
        # Prepare headers (copy module defaults so per-request mutations stay isolated)
        default_headers = dict(_DEFAULT_FETCH_HEADERS)

        # Add custom user agent if provided
        if self.config.user_agent:
            default_headers["User-Agent"] = self.config.user_agent

        # Merge custom headers
        if headers:
            default_headers.update(headers)

        # Rate limiting: Wait if needed to respect rate_limit_delay
        if self.config.rate_limit_delay > 0:
            elapsed_since_last = time.time() - self._last_request_time
            if elapsed_since_last < self.config.rate_limit_delay:
                delay = self.config.rate_limit_delay - elapsed_since_last
                logger.debug(f"Rate limiting: sleeping {delay:.3f}s")
                await asyncio.sleep(delay)

        # Retry loop with exponential backoff
        last_exception: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                start_time = time.time()

                # Update last request time for rate limiting
                self._last_request_time = start_time

                # Make request (either via persistent session or standalone)
                response = await self._make_request(
                    url, default_headers, method=method, json_body=json_body, data=data
                )

                elapsed = time.time() - start_time

                if decode_text:
                    # Decode content with UTF-8 for Thai/Unicode support
                    try:
                        text = response.content.decode("utf-8")
                    except UnicodeDecodeError:
                        # Fallback to latin1 if UTF-8 fails
                        logger.warning(f"UTF-8 decode failed for {url}, trying latin1")
                        text = response.content.decode("latin1")
                        encoding = "latin1"
                    else:
                        encoding = "utf-8"
                else:
                    # Binary payload (zip/xlsx/pdf): skip decode; use .content for bytes.
                    text = ""
                    encoding = "binary"

                # Create response object
                fetch_response = FetchResponse(
                    status_code=response.status_code,
                    content=response.content,
                    text=text,
                    headers=dict(response.headers),
                    url=str(response.url),
                    elapsed=elapsed,
                    encoding=encoding,
                )

                logger.info(
                    f"Fetch successful: url={url}, status={response.status_code}, "
                    f"elapsed={elapsed:.2f}s, size={len(response.content)} bytes"
                )

                return fetch_response

            except Exception as e:
                last_exception = e
                logger.warning(
                    f"Fetch attempt {attempt + 1}/{self.config.max_retries + 1} failed: {e}"
                )

                # Don't sleep after the last attempt
                if attempt < self.config.max_retries:
                    # Exponential backoff
                    delay = self.config.retry_delay * (2**attempt)
                    logger.debug(f"Retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(f"All fetch attempts failed for {url}")
        raise FetchError(
            f"Failed to fetch {url} after {self.config.max_retries + 1} attempts"
        ) from last_exception  # noqa: E501

    async def fetch_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        method: str = "GET",
        json_body: Any | None = None,
    ) -> Any:
        """
        Fetch JSON data from a URL.

        Convenience method that fetches data and parses it as JSON.
        Handles Thai/Unicode characters in JSON responses.

        Args:
            url: URL to fetch
            headers: Optional custom headers
            method: HTTP method, "GET" (default) or "POST". POST requires
                FetcherConfig(use_session=False).
            json_body: JSON-serializable body for POST requests (ignored for GET)

        Returns:
            Parsed JSON data (dict, list, or primitive)

        Raises:
            HTTPStatusError: If the response status is not 2xx. Checked here rather than at each
                of the ~23 call sites, because the audit behind issue #135 found that none of them
                checked it: a JSON service was protected only *incidentally*, by the error body
                failing to parse or validate. That protection holds only where a model has a
                required field, and an HTTP 503 arrived as "validation failed" rather than as a
                transport error. No settfex service gives a non-2xx the meaning "no data", so
                raising here is uniformly correct.
            ResponseParseError: If the response body is not valid JSON or contains a
                NaN/Infinity literal (rejected to avoid silent financial-data corruption).
            Exception: If the request itself fails after all retries.
        """
        # Add JSON accept header
        json_headers = {"Accept": "application/json"}
        if headers:
            json_headers.update(headers)

        response = await self.fetch(url, headers=json_headers, method=method, json_body=json_body)

        if not 200 <= response.status_code < 300:
            message = (
                f"Request failed: HTTP {response.status_code} for {url}. The body was not "
                f"parsed — an error payload can validate into an empty result, which is then "
                f"indistinguishable from 'no data'."
            )
            logger.error(message)
            raise HTTPStatusError(message, status_code=response.status_code, url=url)

        # Decode via the shared helper: rejects NaN/Infinity (silent-corruption guard)
        # and raises ResponseParseError (a ValueError) with URL context on bad JSON.
        data = decode_json(response.text, context=url)
        logger.debug(f"Parsed JSON response from {url}")
        return data

    async def __aenter__(self) -> "AsyncDataFetcher":
        """Enter async context manager."""
        logger.debug("AsyncDataFetcher context entered")
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context manager."""
        logger.debug("AsyncDataFetcher context exited")

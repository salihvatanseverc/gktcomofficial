"""HTTP utilities and helpers using curl_cffi for requests."""

from typing import Any

from curl_cffi.requests import AsyncSession, Response
from loguru import logger


class HTTPClient:
    """Async HTTP client using curl_cffi with browser impersonation."""

    def __init__(
        self,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        timeout: int = 30,
        impersonate: str = "chrome",
    ) -> None:
        """
        Initialize HTTP client.

        Args:
            base_url: Base URL for all requests
            headers: Default headers to include in all requests
            timeout: Request timeout in seconds
            impersonate: Browser to impersonate (chrome, safari, edge, etc.)
        """
        self.base_url = base_url.rstrip("/")
        self.default_headers = headers or {}
        self.timeout = timeout
        self.impersonate = impersonate
        self._session: AsyncSession[Response] | None = None
        logger.debug(
            f"HTTPClient initialized with base_url={base_url}, "
            f"timeout={timeout}, impersonate={impersonate}"
        )

    async def __aenter__(self) -> "HTTPClient":
        """Enter async context manager."""
        self._session = AsyncSession(impersonate=self.impersonate)
        logger.debug("HTTP session opened")
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context manager."""
        if self._session:
            await self._session.close()
            self._session = None
            logger.debug("HTTP session closed")

    def _get_url(self, path: str) -> str:
        """Get full URL from path."""
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _merge_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        """Merge default headers with request-specific headers."""
        merged = self.default_headers.copy()
        if headers:
            merged.update(headers)
        return merged

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """
        Perform GET request.

        Args:
            path: Request path or full URL
            params: Query parameters
            headers: Additional headers for this request

        Returns:
            Response object from curl_cffi

        Raises:
            Exception: If request fails
        """
        if not self._session:
            raise RuntimeError("HTTPClient must be used as async context manager")

        url = self._get_url(path)
        merged_headers = self._merge_headers(headers)

        logger.debug(f"GET request to {url} with params={params}")
        try:
            response: Response = await self._session.get(
                url, params=params, headers=merged_headers, timeout=self.timeout
            )
            logger.debug(f"GET {url} returned status {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"GET request to {url} failed: {e}")
            raise

    async def post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """
        Perform POST request.

        Args:
            path: Request path or full URL
            data: Form data to send
            json: JSON data to send
            headers: Additional headers for this request

        Returns:
            Response object from curl_cffi

        Raises:
            Exception: If request fails
        """
        if not self._session:
            raise RuntimeError("HTTPClient must be used as async context manager")

        url = self._get_url(path)
        merged_headers = self._merge_headers(headers)

        logger.debug(f"POST request to {url}")
        try:
            response: Response = await self._session.post(
                url, data=data, json=json, headers=merged_headers, timeout=self.timeout
            )
            logger.debug(f"POST {url} returned status {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"POST request to {url} failed: {e}")
            raise

    async def put(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """
        Perform PUT request.

        Args:
            path: Request path or full URL
            data: Form data to send
            json: JSON data to send
            headers: Additional headers for this request

        Returns:
            Response object from curl_cffi

        Raises:
            Exception: If request fails
        """
        if not self._session:
            raise RuntimeError("HTTPClient must be used as async context manager")

        url = self._get_url(path)
        merged_headers = self._merge_headers(headers)

        logger.debug(f"PUT request to {url}")
        try:
            response: Response = await self._session.put(
                url, data=data, json=json, headers=merged_headers, timeout=self.timeout
            )
            logger.debug(f"PUT {url} returned status {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"PUT request to {url} failed: {e}")
            raise

    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """
        Perform DELETE request.

        Args:
            path: Request path or full URL
            params: Query parameters
            headers: Additional headers for this request

        Returns:
            Response object from curl_cffi

        Raises:
            Exception: If request fails
        """
        if not self._session:
            raise RuntimeError("HTTPClient must be used as async context manager")

        url = self._get_url(path)
        merged_headers = self._merge_headers(headers)

        logger.debug(f"DELETE request to {url} with params={params}")
        try:
            response: Response = await self._session.delete(
                url, params=params, headers=merged_headers, timeout=self.timeout
            )
            logger.debug(f"DELETE {url} returned status {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"DELETE request to {url} failed: {e}")
            raise

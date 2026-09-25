"""Fetch YouTube transcripts (e.g. Thai OPPDAY captions) as plain strings.

A thin async wrapper around the optional ``youtube-transcript-api`` library (the ``transcript``
extra). Returns a single concatenated string suitable for feeding to an LLM, or ``None`` when the
video has no matching captions, they are disabled, or the request is blocked/rate-limited.

The underlying library is synchronous (``requests``-based) and talks to YouTube directly — no SET
cookies / curl_cffi are involved — so the blocking call is wrapped in ``asyncio.to_thread``.
"""

import asyncio
from collections.abc import Sequence

from loguru import logger

__all__ = ["fetch_youtube_transcript"]


async def fetch_youtube_transcript(
    video_id: str,
    *,
    languages: Sequence[str] = ("th",),
    join_with: str = " ",
    preserve_formatting: bool = False,
    proxies: dict[str, str] | None = None,
) -> str | None:
    """Fetch a YouTube transcript as a single string, or ``None`` if unavailable.

    Args:
        video_id: YouTube video id (the ``v=`` value), e.g. ``"eOC0S8A4QEE"``.
        languages: Language codes in **descending priority** (default Thai). For each code a
            manually-created transcript is preferred, then an auto-generated one.
        join_with: Separator used to join caption snippets into one string.
        preserve_formatting: Keep basic inline formatting (``<i>``/``<b>``) if present.
        proxies: Optional ``{"http": url, "https": url}`` to route through a proxy — helps when
            YouTube blocks the host IP (common from cloud servers).

    Returns:
        The concatenated transcript text, or ``None`` when no matching transcript exists, the
        captions are disabled, the video is unavailable, or YouTube blocks/limits the request.

    Raises:
        ImportError: If the optional ``youtube-transcript-api`` dependency is not installed.

    Example:
        >>> text = await fetch_youtube_transcript("eOC0S8A4QEE", languages=("th",))
        >>> text[:50] if text else None
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise ImportError(
            "youtube-transcript-api is required for transcript fetching. Install it with "
            "'pip install \"settfex[transcript]\"'."
        ) from exc

    proxy_config = None
    if proxies:
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy_config = GenericProxyConfig(
            http_url=proxies.get("http"), https_url=proxies.get("https")
        )

    def _fetch() -> str | None:
        try:
            api = YouTubeTranscriptApi(proxy_config=proxy_config)
            fetched = api.fetch(
                video_id,
                languages=list(languages),
                preserve_formatting=preserve_formatting,
            )
            snippets = fetched.to_raw_data()
            text = join_with.join(s["text"] for s in snippets if s.get("text"))
            return text or None
        except Exception as exc:  # noqa: BLE001 - any retrieval failure -> "no transcript"
            logger.warning(
                f"No transcript for video {video_id} (languages={list(languages)}): "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    return await asyncio.to_thread(_fetch)

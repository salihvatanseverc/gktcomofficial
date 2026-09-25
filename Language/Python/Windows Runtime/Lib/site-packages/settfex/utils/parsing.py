"""Context-rich JSON decoding and Pydantic validation for SET/TFEX API responses.

These helpers centralize the *decode -> validate* step shared by every service so that
malformed financial data fails **loudly** with the originating symbol/endpoint context
instead of surfacing as a bare, context-free ``ValidationError`` or ``AssertionError``.

Hardening guarantees:

- ``NaN`` / ``Infinity`` / ``-Infinity`` JSON literals are **rejected**. Python's default
  ``json.loads`` accepts them, which would let a non-finite price, P/E, or margin flow
  silently into a model -- the primary silent-corruption vector for a financial library.
- Decode and structural-validation failures are re-raised with the ``symbol``/``endpoint``
  context that produced them, so logs and tracebacks are actionable.
- Untrusted payloads always go through **full** Pydantic validation
  (``model_validate``); no ``model_construct`` / validation bypass is used.
"""

import json
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from settfex.exceptions import ParseError

__all__ = [
    "ResponseParseError",
    "decode_json",
    "validate_list_or_raise",
    "validate_or_raise",
]

ModelT = TypeVar("ModelT", bound=BaseModel)


class ResponseParseError(ParseError, ValueError):
    """Raised when an API response cannot be decoded or fails structural validation.

    **Both** a :class:`~settfex.exceptions.ParseError` and a :class:`ValueError`, deliberately.

    It began as a plain ``ValueError`` to match the service layer's documented
    ``Raises: ValueError`` contract. That was right about compatibility and wrong about reach: it
    is the exception a malformed or non-JSON response body actually produces — the single most
    common real failure — and as a bare ``ValueError`` it escaped ``except FetchError``, the
    handler every service documents. The library therefore had two unrelated "parse error" types,
    and the one that fires most often was outside the family. A fault-injection sweep put a number
    on it: 49 of 165 injected-fault cells raised something no documented handler catches.

    Adding :class:`~settfex.exceptions.ParseError` as a base is purely additive — nothing is
    removed from the MRO, so ``except ValueError`` keeps working exactly as before, and
    ``except FetchError`` / ``except ParseError`` now work too.
    """


def _reject_nonfinite(token: str) -> float:
    """``json.loads`` ``parse_constant`` hook: reject NaN/Infinity rather than accept them."""
    raise ValueError(f"non-finite JSON constant {token!r} is not valid in financial data")


def decode_json(text: str, *, context: str) -> Any:
    """Decode a JSON response body, rejecting non-finite numbers, with error context.

    Args:
        text: Raw response body.
        context: Human-readable origin used in any raised error/log line, e.g.
            ``"CPALL (balance_sheet)"`` or the request URL.

    Returns:
        The decoded JSON value (``dict``, ``list``, or primitive).

    Raises:
        ResponseParseError: If the body is not valid JSON, or contains a
            ``NaN``/``Infinity``/``-Infinity`` literal.
    """
    try:
        # parse_constant fires only for NaN/Infinity/-Infinity tokens, so it adds no
        # measurable overhead on well-formed numeric payloads.
        return json.loads(text, parse_constant=_reject_nonfinite)
    except ValueError as exc:  # JSONDecodeError and the parse_constant guard are ValueErrors
        logger.error(f"Failed to decode JSON response for {context}: {exc}")
        logger.debug(f"Undecodable response body for {context} (first 500 chars): {text[:500]}")
        raise ResponseParseError(f"Failed to decode JSON response for {context}: {exc}") from exc


def validate_or_raise(model_cls: type[ModelT], data: Any, *, context: str) -> ModelT:
    """Validate ``data`` into ``model_cls``, logging symbol/endpoint context on failure.

    The ``pydantic.ValidationError`` is wrapped in a :class:`ResponseParseError` (which is a
    ``ParseError``, a ``FetchError`` and a ``ValueError``) and chained as ``__cause__``, so a
    malformed response is catchable with the same ``except FetchError`` every service documents.
    Until 0.24.0 it was re-raised unchanged, which kept ``except ValidationError`` working but put
    the library's most common real failure outside the documented handler.

    Args:
        model_cls: Target Pydantic model.
        data: Decoded payload to validate.
        context: Human-readable origin (symbol/endpoint) for the log line.

    Returns:
        A validated ``model_cls`` instance.

    Raises:
        ResponseParseError: If ``data`` does not satisfy the model. It is both a
            :class:`~settfex.exceptions.ParseError` and a :class:`ValueError`, and it chains the
            original ``pydantic.ValidationError`` as ``__cause__``.
    """
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        # Wrapped rather than re-raised. A response that arrives intact and does not match the
        # model is a PARSE failure, and callers are told to handle those with `except FetchError`
        # -- a bare pydantic ValidationError escapes that, and says "validation failed" for what is
        # usually a 200 carrying an error page. The original is chained, so the field-level detail
        # is one `__cause__` away and `except ValidationError` on the cause still works.
        #
        # This is RESPONSE validation only. User input keeps its own contract: InvalidSymbolError /
        # InvalidDateError / InvalidLanguageError, and a user-built FetcherConfig still raises
        # pydantic's ValidationError, because none of those pass through here.
        message = f"Validation failed for {context} ({model_cls.__name__})"
        logger.error(message)
        raise ResponseParseError(f"{message}: {exc}") from exc


def validate_list_or_raise(model_cls: type[ModelT], data: Any, *, context: str) -> list[ModelT]:
    """Validate a JSON array into a list of ``model_cls`` instances, with per-item context.

    Args:
        model_cls: Target Pydantic model for each element.
        data: Decoded payload expected to be a ``list``.
        context: Human-readable origin (symbol/endpoint) for any log line.

    Returns:
        A list of validated ``model_cls`` instances (empty if ``data`` is an empty list).

    Raises:
        ResponseParseError: If ``data`` is not a list.
        ResponseParseError: If ``data`` is not an array, or any element fails validation
            (logged with its index; the pydantic error is chained as ``__cause__``).
    """
    if not isinstance(data, list):
        raise ResponseParseError(f"Expected a JSON array for {context}, got {type(data).__name__}")
    validated: list[ModelT] = []
    for index, item in enumerate(data):
        try:
            validated.append(model_cls.model_validate(item))
        except ValidationError as exc:
            # Same reasoning as validate_or_raise: a response that does not match the model is a
            # parse failure, and belongs in the family callers are told to catch.
            message = (
                f"Validation failed for {context} ({model_cls.__name__}) at item index {index}"
            )
            logger.error(message)
            raise ResponseParseError(f"{message}: {exc}") from exc
    return validated

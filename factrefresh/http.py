"""One HTTP GET, no redirects followed, the body read up to a byte cap.

The refresh decides about redirects itself (every hop must stay on a listed host), so the transport never
follows one. Tests replace UrllibTransport with a transport that replays recorded responses.
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


@dataclass
class Response:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)  # names lower-cased
    body: bytes = b""
    truncated: bool = False  # the body was longer than the cap it was read with


class TransportError(Exception):
    """The request got no HTTP response: DNS, connection, TLS or timeout."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


class UrllibTransport:
    """Sequential requests, one at a time, so never more than one connection to a host.

    `timeout` is a total wall-clock budget for the whole request, not only for each socket operation: the
    body is read in chunks, every blocking read waits at most for the time left, and a body still arriving
    when the budget is spent fails, so a server dripping bytes cannot hold a request open longer."""

    def __init__(self, opener=None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._opener = opener or urllib.request.build_opener(_NoRedirect())
        self._monotonic = monotonic

    def get(self, url: str, headers: Dict[str, str], timeout: float, max_bytes: int) -> Response:
        deadline = self._monotonic() + timeout
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            resp = self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as err:  # 3xx (not followed), 4xx, 5xx
            resp = err
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as err:
            raise TransportError(_describe(err)) from err
        try:
            body = self._read(resp, max_bytes + 1, deadline, timeout) if resp.fp is not None else b""
        except (socket.timeout, ConnectionError, OSError) as err:
            raise TransportError(_describe(err)) from err
        finally:
            resp.close()
        hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
        return Response(status=resp.status if hasattr(resp, "status") else resp.code,
                        headers=hdrs, body=body[:max_bytes], truncated=len(body) > max_bytes)

    def _read(self, resp, limit: int, deadline: float, timeout: float) -> bytes:
        read = getattr(resp, "read1", None) or resp.read
        chunks, size = [], 0
        while size < limit:
            left = deadline - self._monotonic()
            if left <= 0:
                raise TransportError(f"no complete response within the total timeout of {timeout:g} seconds")
            _shrink_timeout(resp, left)
            chunk = read(min(CHUNK, limit - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks)


CHUNK = 65536


def _shrink_timeout(resp, seconds: float) -> None:
    """Best effort: let the next blocking read wait no longer than the time left."""
    sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.settimeout(max(seconds, 0.001))
        except OSError:
            pass


def _describe(err: BaseException) -> str:
    reason: Optional[object] = getattr(err, "reason", None)
    text = str(reason if reason is not None else err) or type(err).__name__
    return text[:200]

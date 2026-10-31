"""One HTTP GET, no redirects followed, the body read up to a byte cap.

The refresh decides about redirects itself (every hop must stay on a listed host), so the transport never
follows one. Tests replace UrllibTransport with a transport that replays recorded responses.
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Optional


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
    """Sequential requests, one at a time, so never more than one connection to a host."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())

    def get(self, url: str, headers: Dict[str, str], timeout: float, max_bytes: int) -> Response:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            resp = self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as err:  # 3xx (not followed), 4xx, 5xx
            resp = err
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as err:
            raise TransportError(_describe(err)) from err
        try:
            body = resp.read(max_bytes + 1) if resp.fp is not None else b""
        except (socket.timeout, ConnectionError, OSError) as err:
            raise TransportError(_describe(err)) from err
        finally:
            resp.close()
        hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
        return Response(status=resp.status if hasattr(resp, "status") else resp.code,
                        headers=hdrs, body=body[:max_bytes], truncated=len(body) > max_bytes)


def _describe(err: BaseException) -> str:
    reason: Optional[object] = getattr(err, "reason", None)
    text = str(reason if reason is not None else err) or type(err).__name__
    return text[:200]

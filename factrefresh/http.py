"""One HTTP GET, no redirects followed, the body read up to a byte cap.

The refresh decides about redirects itself (every hop must stay on a listed host), so the transport never
follows one. Tests replace UrllibTransport with a transport that replays recorded responses.
"""
from __future__ import annotations

import http.client
import io
import re
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

    `timeout` is a total wall-clock budget for the whole request, not only for each socket operation. Once
    connected, the socket is wrapped so that every read, of the status line, the headers and the body alike,
    waits at most for the time left and fails once it is spent: a server dripping header lines or body bytes
    cannot hold a request open longer. Connecting and the TLS handshake keep the per-operation timeout."""

    def __init__(self, opener=None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._fixed_opener = opener  # tests pass one; otherwise each request gets its own, bound to its deadline
        self._monotonic = monotonic

    def get(self, url: str, headers: Dict[str, str], timeout: float, max_bytes: int) -> Response:
        deadline = self._monotonic() + timeout
        req = urllib.request.Request(url, headers=headers, method="GET")
        opener = self._fixed_opener or urllib.request.build_opener(
            _NoRedirect(), _DeadlineHTTPSHandler(deadline_connection(deadline, self._monotonic, timeout)))
        try:
            resp = opener.open(req, timeout=timeout)
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
        status = resp.status if hasattr(resp, "status") else resp.code
        if 200 <= status < 300:
            self._check_length(resp, status, hdrs, body, max_bytes)
        return Response(status=status, headers=hdrs, body=body[:max_bytes], truncated=len(body) > max_bytes)

    @staticmethod
    def _check_length(resp, status: int, hdrs: Dict[str, str], body: bytes, max_bytes: int) -> None:
        """A 2xx page must be whole before its quote is looked for, so it can never read as `changed` when it
        was only cut off. http.client's read1 returns a short body without an error, so the body read is
        compared with Content-Length here.

        Exempt: 204 and 205 (no content by definition), a chunked body (http.client reads it by its chunks
        and ignores Content-Length) and a body we cut ourselves at maxBytes (reported as truncated).
        Content-Length may be repeated, or a comma-separated list, as long as every member is the same
        number (RFC 9110, 8.6); members that disagree, or a member that is not plain ASCII digits, are a
        transport error rather than a reason to skip the check. Numbers are compared as digit strings,
        never converted, so no length of header can raise."""
        if status in (204, 205):
            return
        chunked = getattr(resp, "chunked", None)
        if chunked is None:  # not an http.client response: the rule http.client itself uses
            chunked = hdrs.get("transfer-encoding", "").strip().lower() == "chunked"
        if chunked:
            return  # http.client reads a chunked body by its chunks and ignores Content-Length
        get_all = getattr(resp.headers, "get_all", None)
        if get_all is not None:
            raw = list(get_all("content-length") or [])
        else:
            raw = [hdrs["content-length"]] if "content-length" in hdrs else []
        if not raw:
            return
        members = [m.strip() for v in raw for m in v.split(",")]
        if not all(_DIGITS.fullmatch(m) for m in members):
            raise TransportError(f"malformed Content-Length {', '.join(raw)[:40]!r}")
        numbers = {m.lstrip("0") for m in members}  # "" stands for zero
        if len(numbers) > 1:
            raise TransportError(f"conflicting Content-Length values: {', '.join(raw)[:80]}")
        declared = numbers.pop()
        got = str(len(body))
        shorter = len(got) < len(declared) or (len(got) == len(declared) and got < declared)
        if len(body) <= max_bytes and shorter:
            shown = declared if len(declared) <= 20 else declared[:20] + "..."
            raise TransportError(f"the body ended after {len(body)} of the {shown} bytes Content-Length declares")

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
_DIGITS = re.compile(r"[0-9]+")  # ASCII digits only: no sign, no space, no comma list


class _DeadlineSocket:
    """A connected socket whose every read waits at most for the time left before a fixed deadline, and
    fails with socket.timeout once the deadline has passed. Everything else goes to the socket itself."""

    def __init__(self, sock, deadline: float, monotonic: Callable[[], float], timeout: float) -> None:
        self._sock = sock
        self._deadline = deadline
        self._monotonic = monotonic
        self._timeout = timeout
        self._io_refs = 0  # files made by makefile and not yet closed
        self._closed = False

    def _arm(self) -> None:
        left = self._deadline - self._monotonic()
        if left <= 0:
            raise socket.timeout(f"no complete response within the total timeout of {self._timeout:g} seconds")
        self._sock.settimeout(left)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self._arm()
        return self._sock.recv_into(buffer, nbytes, flags)

    def recv(self, bufsize, flags=0):
        self._arm()
        return self._sock.recv(bufsize, flags)

    def makefile(self, mode="rb", buffering=None, **_kwargs):
        # http.client reads the status line, headers and body through this file, so through recv_into above.
        self._io_refs += 1
        return io.BufferedReader(socket.SocketIO(self, "rb"))

    # urllib closes the connection's socket as soon as the headers are read; as with socket.socket, the
    # real close waits until the response's file is closed too, or the body could not be read.
    def _decref_socketios(self):
        if self._io_refs > 0:
            self._io_refs -= 1
        if self._closed and self._io_refs == 0:
            self._sock.close()

    def close(self):
        self._closed = True
        if self._io_refs == 0:
            self._sock.close()

    def __getattr__(self, name):
        return getattr(self._sock, name)


def deadline_connection(deadline: float, monotonic: Callable[[], float], timeout: float):
    """An HTTPSConnection class whose socket, once connected, is a _DeadlineSocket."""

    class DeadlineHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            self.sock = _DeadlineSocket(self.sock, deadline, monotonic, timeout)

    return DeadlineHTTPSConnection


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, connection_class) -> None:
        super().__init__()
        self._connection_class = connection_class

    def https_open(self, req):
        kwargs = {"context": self._context}
        if hasattr(self, "_check_hostname"):  # Python before 3.12
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(self._connection_class, req, **kwargs)


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

"""The real transport's total deadline, with a fake connection: no network."""
from __future__ import annotations

import http.client
import unittest
from unittest import mock

from factrefresh.http import TransportError, UrllibTransport


class FakeSock:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, seconds):
        self.timeouts.append(seconds)


class Drip:
    """A response that hands over one byte per read, each read taking `per_read` seconds."""

    def __init__(self, clock, per_read):
        self.clock, self.per_read = clock, per_read
        self.status, self.headers = 200, {"content-type": "text/plain"}
        self.sock = FakeSock()
        self.fp = type("Fp", (), {"raw": type("Raw", (), {"_sock": self.sock})()})()
        self.reads = 0

    def read1(self, n):
        self.reads += 1
        self.clock[0] += self.per_read
        return b"x"

    def close(self):
        pass


class Opener:
    def __init__(self, resp):
        self.resp = resp

    def open(self, req, timeout):
        return self.resp


class TotalDeadline(unittest.TestCase):
    def test_a_slow_drip_cannot_outlast_the_total_timeout(self):
        clock = [0.0]
        resp = Drip(clock, per_read=10.0)
        t = UrllibTransport(opener=Opener(resp), monotonic=lambda: clock[0])
        with self.assertRaises(TransportError) as err:
            t.get("https://docs.example.com/x", {}, timeout=30, max_bytes=1000)
        self.assertIn("total timeout", str(err.exception))
        self.assertEqual(resp.reads, 3)  # 10, 20, 30 seconds: no read starts once the budget is spent
        self.assertLessEqual(clock[0], 30.0)

    def test_each_read_waits_at_most_for_the_time_left(self):
        clock = [0.0]
        resp = Drip(clock, per_read=10.0)
        t = UrllibTransport(opener=Opener(resp), monotonic=lambda: clock[0])
        with self.assertRaises(TransportError):
            t.get("https://docs.example.com/x", {}, timeout=30, max_bytes=1000)
        self.assertEqual(resp.sock.timeouts, [30.0, 20.0, 10.0])

    def test_a_fast_body_is_read_whole(self):
        clock = [0.0]
        resp = Drip(clock, per_read=0.01)
        t = UrllibTransport(opener=Opener(resp), monotonic=lambda: clock[0])
        got = t.get("https://docs.example.com/x", {}, timeout=30, max_bytes=5)
        self.assertEqual(got.body, b"xxxxx")
        self.assertTrue(got.truncated)


class Body:
    """A response whose body is `data`, handed over in one read."""

    def __init__(self, data):
        self.status, self.headers, self.fp = 200, {"content-type": "text/plain"}, None
        self.fp = type("Fp", (), {})()
        self._data = data

    def read1(self, n):
        out, self._data = self._data[:n], self._data[n:]
        return out

    def close(self):
        pass


class MaxBytes(unittest.TestCase):
    def get(self, size, cap):
        t = UrllibTransport(opener=Opener(Body(b"x" * size)), monotonic=lambda: 0.0)
        return t.get("https://docs.example.com/x", {}, timeout=30, max_bytes=cap)

    def test_a_body_of_exactly_max_bytes_is_whole(self):
        got = self.get(100, 100)
        self.assertFalse(got.truncated)
        self.assertEqual(len(got.body), 100)

    def test_a_body_one_byte_over_max_bytes_is_truncated(self):
        got = self.get(101, 100)
        self.assertTrue(got.truncated)
        self.assertEqual(len(got.body), 100)


class FakeServer:
    """The socket of a connection: answers with `chunks`, one per read, each read taking `per_read` seconds
    of the fake clock. Reading after close fails, as on a real socket."""

    def __init__(self, chunks, clock, per_read):
        self.chunks, self.clock, self.per_read = list(chunks), clock, per_read
        self.timeouts, self.closed, self.reads = [], False, 0

    def sendall(self, data):
        pass

    def settimeout(self, seconds):
        self.timeouts.append(seconds)

    def recv_into(self, buf, nbytes=0, flags=0):
        if self.closed:
            raise OSError(9, "Bad file descriptor")
        self.reads += 1
        self.clock[0] += self.per_read
        if not self.chunks:
            return 0
        chunk = self.chunks.pop(0)
        n = min(len(buf), len(chunk))
        buf[:n] = chunk[:n]
        if n < len(chunk):
            self.chunks.insert(0, chunk[n:])
        return n

    def close(self):
        self.closed = True


def through_urllib(chunks, per_read, timeout=3):
    """A GET through the real urllib and http.client code, with only the connected socket faked."""
    clock = [0.0]
    server = FakeServer(chunks, clock, per_read)

    def connect(self):
        self.sock = server

    with mock.patch.object(http.client.HTTPSConnection, "connect", connect):
        t = UrllibTransport(monotonic=lambda: clock[0])
        try:
            return t.get("https://docs.example.com/x", {}, timeout=timeout, max_bytes=1000), server, clock
        except TransportError as err:
            return err, server, clock


class HeaderDeadline(unittest.TestCase):
    def test_a_server_dripping_header_lines_cannot_outlast_the_total_timeout(self):
        drip = [b"HTTP/1.1 200 OK\r\n"] + [f"X-Drip-{i}: x\r\n".encode() for i in range(100)] + [b"\r\n"]
        got, server, clock = through_urllib(drip, per_read=1.0, timeout=3)
        self.assertIsInstance(got, TransportError)
        self.assertIn("total timeout", str(got))
        self.assertLessEqual(clock[0], 3.0)
        self.assertLess(server.reads, 10)

    def test_a_normal_response_is_read_whole_after_urllib_closes_the_socket(self):
        reply = [b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\n", b"hello"]
        got, server, _ = through_urllib(reply, per_read=0.01)
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertEqual((got.status, got.body, got.truncated), (200, b"hello", False))
        self.assertTrue(server.closed)  # closed once the response was done with, not before

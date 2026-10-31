"""The real transport's total deadline, with a fake connection: no network."""
from __future__ import annotations

import http.client
import socket
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
    """A response whose body is `data`, handed over in one read. Not an http.client response: its headers
    are a plain dict and it has no `chunked` flag."""

    def __init__(self, data, headers=None, status=200):
        self.status, self.fp = status, type("Fp", (), {})()
        self.headers = headers if headers is not None else {"content-type": "text/plain"}
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


STALL = object()  # the server sends nothing: the read blocks for the socket's current timeout, then times out
IO_ERROR = object()  # the read fails with an OSError that is neither a timeout nor a ConnectionError


class FakeServer:
    """The socket of a connection: answers with `chunks`, one per read, each read taking `per_read` seconds
    of the fake clock. Reading after close fails, as on a real socket."""

    def __init__(self, chunks, clock, per_read, timeout=None):
        self.chunks, self.clock, self.per_read = list(chunks), clock, per_read
        self.timeouts, self.closed, self.reads = [], False, 0
        self.current_timeout = timeout  # what the connection was opened with, until settimeout changes it

    def sendall(self, data):
        pass

    def settimeout(self, seconds):
        self.timeouts.append(seconds)
        self.current_timeout = seconds

    def recv_into(self, buf, nbytes=0, flags=0):
        if self.closed:
            raise OSError(9, "Bad file descriptor")
        self.reads += 1
        chunk = self.chunks.pop(0) if self.chunks else b""
        if chunk is STALL:  # blocks for exactly the socket's timeout
            self.clock[0] += self.current_timeout
            raise socket.timeout("timed out")
        self.clock[0] += self.per_read
        if chunk is IO_ERROR:
            raise OSError(5, "Input/output error")
        if not chunk:
            return 0
        n = min(len(buf), len(chunk))
        buf[:n] = chunk[:n]
        if n < len(chunk):
            self.chunks.insert(0, chunk[n:])
        return n

    def close(self):
        self.closed = True


def through_urllib(chunks, per_read, timeout=3, max_bytes=1000):
    """A GET through the real urllib and http.client code, with only the connected socket faked."""
    clock = [0.0]
    server = FakeServer(chunks, clock, per_read, timeout)

    def connect(self):
        self.sock = server

    with mock.patch.object(http.client.HTTPSConnection, "connect", connect):
        t = UrllibTransport(monotonic=lambda: clock[0])
        try:
            return t.get("https://docs.example.com/x", {}, timeout=timeout, max_bytes=max_bytes), server, clock
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


OK_HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"


class Gaps(unittest.TestCase):
    def test_a_body_shorter_than_its_content_length_is_unreachable(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 10\r\n\r\n", b"hello"], per_read=0.01)
        self.assertIsInstance(got, TransportError)
        self.assertIn("5 of the 10 bytes", str(got))

    def test_a_body_matching_its_content_length_is_read(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 5\r\n\r\n", b"hello"], per_read=0.01)
        self.assertEqual(got.body, b"hello")

    def test_a_stall_late_in_the_budget_waits_only_for_the_time_left(self):
        # header lines arrive quickly until 2.5 s of the 3 s budget are spent, then the server goes silent
        lines = [b"HTTP/1.1 200 OK\r\n"] + [f"X-Line-{i}: x\r\n".encode() for i in range(25)] + [STALL]
        got, server, clock = through_urllib(lines, per_read=0.1, timeout=3)
        self.assertIsInstance(got, TransportError)
        self.assertLessEqual(clock[0], 3.0 + 1e-6)  # the stalled read waited the 0.5 s left, not 3 s more
        self.assertLess(min(server.timeouts), 1.0)

    def test_a_timeout_in_the_middle_of_the_body_is_a_transport_error(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 10\r\n\r\n", b"hello", STALL],
                                   per_read=0.01, timeout=30)
        self.assertIsInstance(got, TransportError)

    def test_an_os_error_in_the_middle_of_the_body_is_a_transport_error(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 10\r\n\r\n", b"hello", IO_ERROR],
                                   per_read=0.01, timeout=30)
        self.assertIsInstance(got, TransportError)
        self.assertIn("Input/output error", str(got))



class ContentLength(unittest.TestCase):
    def test_a_short_body_on_a_non_2xx_answer_is_still_returned(self):
        got, _, _ = through_urllib([b"HTTP/1.1 404 Not Found\r\nContent-Length: 10\r\n\r\n", b"hello"], per_read=0.01)
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertEqual((got.status, got.body), (404, b"hello"))

    def test_a_content_length_beyond_max_bytes_is_a_truncated_page_not_an_error(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 5000\r\n\r\n", b"x" * 150], per_read=0.01,
                                   max_bytes=100)
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertTrue(got.truncated)
        self.assertEqual(len(got.body), 100)

    def test_a_body_one_byte_short_is_cut_off(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 10\r\n\r\n", b"123456789"], per_read=0.01)
        self.assertIsInstance(got, TransportError)
        self.assertIn("9 of the 10 bytes", str(got))

    def test_a_chunked_body_ignores_a_content_length_it_does_not_match(self):
        reply = [OK_HEAD + b"Transfer-Encoding: chunked\r\nContent-Length: 50\r\n\r\n", b"5\r\nhello\r\n0\r\n\r\n"]
        got, _, _ = through_urllib(reply, per_read=0.01)
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertEqual(got.body, b"hello")

    def test_conflicting_content_length_headers_are_a_transport_error(self):
        # http.client reads by the first (5); a last-wins reading (3) would wrongly call the body whole
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 5\r\nContent-Length: 3\r\n\r\n", b"hello"],
                                   per_read=0.01)
        self.assertIsInstance(got, TransportError)
        self.assertIn("conflicting", str(got))

    def test_agreeing_duplicate_content_length_headers_are_accepted(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: 5\r\nContent-Length: 5\r\n\r\n", b"hello"],
                                   per_read=0.01)
        self.assertEqual(got.body, b"hello")

    def test_a_content_length_that_is_not_plain_digits_is_a_transport_error(self):
        for value in (b"+5", b"-1", b"abc", b"5,", b"5,,5", b"5 5"):
            with self.subTest(value=value):
                got, _, _ = through_urllib([OK_HEAD + b"Content-Length: " + value + b"\r\n\r\n", b"hello"],
                                           per_read=0.01)
                self.assertIsInstance(got, TransportError)
                self.assertIn("malformed", str(got))



class ContentLengthBoundaries(unittest.TestCase):
    """Literal values around every condition of the check: one under, exact, one over."""

    def short_of(self, body_len, max_bytes=100, declared=b"200"):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: " + declared + b"\r\n\r\n", b"x" * body_len],
                                   per_read=0.01, max_bytes=max_bytes)
        return got

    def test_99_bytes_under_a_cap_of_100_is_cut_off(self):
        self.assertIsInstance(self.short_of(99), TransportError)

    def test_exactly_100_bytes_at_a_cap_of_100_is_cut_off_not_ours(self):
        # we read up to 101 bytes; 100 arriving means the server stopped, not that we cut it
        self.assertIsInstance(self.short_of(100), TransportError)

    def test_101_bytes_over_a_cap_of_100_is_our_own_cut(self):
        got = self.short_of(101)
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertTrue(got.truncated)

    def status(self, line):
        got, _, _ = through_urllib([line + b"Content-Length: 10\r\n\r\n", b"hello"], per_read=0.01)
        return got

    def test_status_299_is_checked(self):
        self.assertIsInstance(self.status(b"HTTP/1.1 299 Odd\r\n"), TransportError)

    def test_status_300_is_returned_as_it_is(self):
        got = self.status(b"HTTP/1.1 300 Multiple Choices\r\n")
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertEqual(got.status, 300)

    def test_a_transfer_encoding_that_is_not_exactly_chunked_is_still_checked(self):
        # http.client reads this body by Content-Length, so a short one is cut off
        got, _, _ = through_urllib([OK_HEAD + b"Transfer-Encoding: gzip, chunked\r\nContent-Length: 10\r\n\r\n",
                                    b"hello"], per_read=0.01)
        self.assertIsInstance(got, TransportError)

    def test_an_empty_content_length_is_malformed(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length: \r\n\r\n", b"hello"], per_read=0.01)
        self.assertIsInstance(got, TransportError)

    def test_whitespace_around_a_content_length_is_not_malformed(self):
        got, _, _ = through_urllib([OK_HEAD + b"Content-Length:   5  \r\n\r\n", b"hello"], per_read=0.01)
        self.assertEqual(got.body, b"hello")



def ok_with(*header_lines, body=b"hello", status=b"HTTP/1.1 200 OK\r\n"):
    head = status + b"Content-Type: text/plain\r\n" + b"".join(h + b"\r\n" for h in header_lines) + b"\r\n"
    got, _, _ = through_urllib([head, body] if body else [head], per_read=0.01)
    return got


class ContentLengthValues(unittest.TestCase):
    def test_a_5000_digit_content_length_is_a_transport_error_not_a_crash(self):
        got = ok_with(b"Content-Length: " + b"9" * 5000)
        self.assertIsInstance(got, TransportError)
        self.assertIn("5 of the 99999999999999999999... bytes", str(got))

    def test_leading_zeros_do_not_change_the_number(self):
        got = ok_with(b"Content-Length: " + b"0" * 4999 + b"5")
        self.assertEqual(got.body, b"hello")

    def test_05_and_5_are_the_same_number(self):
        got = ok_with(b"Content-Length: 05", b"Content-Length: 5")
        self.assertEqual(got.body, b"hello")

    def test_a_list_of_identical_values_is_accepted(self):
        self.assertEqual(ok_with(b"Content-Length: 5, 5").body, b"hello")
        self.assertEqual(ok_with(b"Content-Length: 5,5,05").body, b"hello")

    def test_a_list_of_different_values_is_conflicting(self):
        got = ok_with(b"Content-Length: 5, 6")
        self.assertIsInstance(got, TransportError)
        self.assertIn("conflicting", str(got))


class NoContentStatuses(unittest.TestCase):
    def test_204_with_a_content_length_is_not_rejected(self):
        got = ok_with(b"Content-Length: 10", body=b"", status=b"HTTP/1.1 204 No Content\r\n")
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertEqual(got.status, 204)

    def test_205_with_a_content_length_is_not_rejected(self):
        got = ok_with(b"Content-Length: 10", body=b"", status=b"HTTP/1.1 205 Reset Content\r\n")
        self.assertNotIsInstance(got, TransportError, str(got))
        self.assertEqual(got.status, 205)

    def test_203_and_206_are_still_checked(self):
        for line in (b"HTTP/1.1 203 Non-Authoritative Information\r\n", b"HTTP/1.1 206 Partial Content\r\n"):
            with self.subTest(status=line):
                self.assertIsInstance(ok_with(b"Content-Length: 10", status=line), TransportError)


class FallbackHeaders(unittest.TestCase):
    """A response that is not from http.client (plain dict headers, no chunked flag)."""

    def get(self, headers, body=b"hello"):
        t = UrllibTransport(opener=Opener(Body(body, headers=headers)), monotonic=lambda: 0.0)
        try:
            return t.get("https://docs.example.com/x", {}, timeout=30, max_bytes=1000)
        except TransportError as err:
            return err

    def test_exactly_chunked_skips_the_check(self):
        got = self.get({"transfer-encoding": "chunked", "content-length": "10"})
        self.assertEqual(got.body, b"hello")

    def test_not_exactly_chunked_is_checked(self):
        self.assertIsInstance(self.get({"transfer-encoding": "gzip, chunked", "content-length": "10"}), TransportError)

    def test_a_short_body_is_cut_off(self):
        self.assertIsInstance(self.get({"content-length": "6"}), TransportError)

    def test_digit_string_comparison_at_literal_boundaries(self):
        self.assertEqual(self.get({"content-length": "4"}).body, b"hello")  # longer than declared: not short
        self.assertEqual(self.get({"content-length": "5"}).body, b"hello")  # exact
        self.assertIsInstance(self.get({"content-length": "6"}), TransportError)  # one over
        self.assertIsInstance(self.get({"content-length": "10"}), TransportError)  # more digits

    def test_no_content_length_is_not_checked(self):
        self.assertEqual(self.get({"content-type": "text/plain"}).body, b"hello")

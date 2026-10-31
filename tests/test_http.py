"""The real transport's total deadline, with a fake connection: no network."""
from __future__ import annotations

import unittest

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

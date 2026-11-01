"""The Connect conformance runner's own checks (conformance/connect_run.py), each against an answer that breaks
it one way, and the Failure codec (conformance/failure.py). HTTP is stubbed: tests never use the network.
Each check is also mutated in tests/guards.py."""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "conformance"))

import connect_run  # noqa: E402
import failure  # noqa: E402

SERVICE = "machinery.plugin.v1alpha1.PluginService"
DESCRIBE = {"name": "describe", "procedure": f"{SERVICE}/Describe", "expect": {"code": "ok", "envelope": {"ok": True}}}
GROUP = {"procedure": f"{SERVICE}/Merge", "defaults": {"caps": {"timeSeconds": 1, "outputBytes": 4096}}}
DESCRIBED = {"name": "fact-refresh", "version": "0.1.0", "protocol": "machinery.plugin.v1alpha1",
             "contracts": ["machinery.plugin.v1alpha1"], "job": "fact-refresh", "verbs": []}
REFUSED = {"ok": False, "data": None, "notRead": [], "warnings": ["two partial results for one unit"]}


def error_body(code, envelope=REFUSED, message=None, **extra):
    body = {"code": code, "message": "; ".join(envelope["warnings"]) if message is None else message}
    if envelope is not None:
        body["details"] = [{"type": failure.TYPE, "value": failure.encode(envelope)}]
    body.update(extra)
    return json.dumps(body).encode("utf-8")


def answer(case, status, body, ctype="application/json"):
    """What the runner says of one fixture when the service answers this."""
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    with mock.patch.object(connect_run, "post", return_value=(status, ctype, data, None)):
        return connect_run.run_fixture("http://127.0.0.1:1", connect_run.Validator(None, os.devnull), GROUP, case)


def refused(code="failed_precondition", **envelope):
    return {"name": "refused", "request": {"input": {}}, "expect": {"code": code, "envelope": dict({"ok": False}, **envelope)}}


class Success(unittest.TestCase):
    def test_a_good_answer_passes(self):
        self.assertIsNone(answer(DESCRIBE, 200, {"ok": True, "data": DESCRIBED, "notRead": [], "warnings": []}))

    def test_a_success_must_be_http_200(self):
        self.assertIn("HTTP 201", answer(DESCRIBE, 201, {"ok": True, "data": DESCRIBED, "notRead": [], "warnings": []}))

    def test_a_success_must_be_json(self):
        why = answer(DESCRIBE, 200, b'{"ok": true}', ctype="text/plain")
        self.assertIn("Content-Type", why)

    def test_a_success_answers_ok_true(self):
        why = answer(DESCRIBE, 200, {"ok": False, "data": DESCRIBED, "notRead": [], "warnings": []})
        self.assertIn("ok true", why)

    def test_describe_answers_in_the_package_it_was_called_in(self):
        why = answer(DESCRIBE, 200, {"ok": True, "data": dict(DESCRIBED, protocol="0"), "notRead": [], "warnings": []})
        self.assertIn("describe: answers in contract", why)

    def test_an_integral_number_is_written_as_an_integer(self):
        for written in (b"3.0", b"3e0", b"3.000"):
            with self.subTest(written=written):
                body = b'{"ok": true, "data": {"protocol": "x", "n": ' + written + b'}, "notRead": [], "warnings": []}'
                self.assertIn("write it as an integer", answer(DESCRIBE, 200, body))
        body = json.dumps({"ok": True, "data": dict(DESCRIBED, pacing=2.5), "notRead": [], "warnings": []}).encode()
        self.assertIsNone(answer(DESCRIBE, 200, body))

    def test_an_error_when_a_success_is_expected_fails(self):
        self.assertIn("HTTP 400", answer(DESCRIBE, 400, error_body("invalid_argument")))


class Errors(unittest.TestCase):
    def test_a_plugin_error_with_its_failure_passes(self):
        self.assertIsNone(answer(refused(), 400, error_body("failed_precondition")))

    def test_the_code_must_be_the_expected_one(self):
        self.assertIn("expected failed_precondition", answer(refused(), 400, error_body("invalid_argument")))

    def test_a_code_must_come_with_its_http_status(self):
        self.assertIn("expected HTTP 503", answer(refused("unavailable"), 400, error_body("unavailable")))

    def test_a_404_without_a_connect_body_is_unimplemented(self):
        case = {"name": "x", "request": {}, "expect": {"code": "unimplemented"}}
        self.assertIsNone(answer(case, 404, b"404 page not found\n", ctype="text/plain"))

    def test_a_429_without_a_connect_body_is_not_resource_exhausted(self):
        why = answer(refused("resource_exhausted"), 429, b"slow down", ctype="text/plain")
        self.assertIn("code unavailable", why)

    def test_a_plugin_code_needs_a_failure_detail(self):
        for code, status in (("failed_precondition", 400), ("resource_exhausted", 429), ("unavailable", 503)):
            with self.subTest(code=code):
                why = answer(refused(code), status, error_body(code, envelope=None, message="no detail"))
                self.assertIn("without a machinery.plugin.v1alpha1.Failure detail", why)

    def test_invalid_argument_may_come_without_a_detail(self):
        case = {"name": "x", "rawBody": "not json", "expect": {"code": "invalid_argument"}}
        self.assertIsNone(answer(case, 400, error_body("invalid_argument", envelope=None, message="bad JSON")))

    def test_two_failure_details_fail(self):
        body = json.loads(error_body("failed_precondition"))
        body["details"] *= 2
        self.assertIn("more than one", answer(refused(), 400, body))

    def test_the_message_is_the_warnings_joined(self):
        why = answer(refused(), 400, error_body("failed_precondition", message="refused"))
        self.assertIn("not the warnings joined", why)

    def test_a_failure_has_ok_false_data_null_and_a_warning(self):
        for envelope in (dict(REFUSED, ok=True), dict(REFUSED, warnings=[])):
            with self.subTest(envelope=envelope):
                why = answer(refused(), 400, error_body("failed_precondition", envelope=envelope, message=""))
                self.assertIn("ok false, data null and at least one warning", why)

    def test_a_failure_that_does_not_decode_fails(self):
        body = json.loads(error_body("failed_precondition"))
        body["details"][0]["value"] = "!!"
        self.assertIn("does not decode", answer(refused(), 400, body))

    def test_the_expected_envelope_is_matched_against_the_failure(self):
        why = answer(refused(notRead=[{"what": "x", "why": "y"}]), 400, error_body("failed_precondition"))
        self.assertIn("failure.notRead", why)

    def test_a_200_when_an_error_is_expected_fails(self):
        why = answer(refused(), 200, {"ok": True, "data": {}, "notRead": [], "warnings": []})
        self.assertIn("code unknown (HTTP 200), expected failed_precondition", why)


class Post(unittest.TestCase):
    class Answer(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_a_body_over_output_bytes_fails_success_or_error(self):
        with mock.patch("urllib.request.urlopen", return_value=self.Answer(b"x" * 1025)):
            self.assertIn("over outputBytes", connect_run.post("http://h.example/x", b"{}", 1, 1024)[3])
        err = urllib.error.HTTPError("http://h.example/x", 400, "bad", {"Content-Type": "application/json"},
                                     io.BytesIO(b"x" * 1025))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIn("over outputBytes", connect_run.post("http://h.example/x", b"{}", 1, 1024)[3])

    def test_a_body_of_exactly_output_bytes_passes(self):
        with mock.patch("urllib.request.urlopen", return_value=self.Answer(b"x" * 1024)):
            self.assertIsNone(connect_run.post("http://h.example/x", b"{}", 1, 1024)[3])

    def test_the_deadline_is_sent_and_a_late_answer_fails(self):
        seen = {}

        def urlopen(req, timeout):
            seen.update(req.headers, timeout=timeout)
            return self.Answer(b"{}")
        with mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                mock.patch("time.monotonic", side_effect=[0.0, 3.5]):
            why = connect_run.post("http://h.example/x", b"{}", 1, 1024)[3]
        self.assertEqual(seen["Connect-timeout-ms"], "1000")
        self.assertEqual(seen["Connect-protocol-version"], "1")
        self.assertIn("after timeSeconds", why)


class Codec(unittest.TestCase):
    def test_a_failure_round_trips(self):
        env = {"ok": False, "data": None, "notRead": [{"what": "source", "why": "not plain"}], "warnings": ["a", "ü"]}
        self.assertEqual(failure.decode(failure.encode(env)), env)

    def test_malformed_values_are_refused_never_read_as_empty(self):
        for value in ("!!", "CP8", "IgI", "IAE", 7):  # not base64, a cut varint, a cut string, warnings as a varint
            with self.subTest(value=value), self.assertRaises(failure.Malformed):
                failure.decode(value)


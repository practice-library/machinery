"""The Connect conformance runner's own checks (conformance/connect_run.py), each against an answer that breaks
it one way, and the Failure codec (conformance/failure.py). HTTP is stubbed: tests never use the network.
Each check is also mutated in tests/guards.py."""
from __future__ import annotations

import http.client
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "conformance"))

import carry  # noqa: E402
import connect_run  # noqa: E402
import failure  # noqa: E402
import v0_bridge  # noqa: E402

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


class Picky:
    """A validator that rejects every instance checked against the one schema reference ending in `refuses`."""

    def __init__(self, refuses=None):
        self.refuses, self.seen = refuses, []

    def check(self, ref, instance):
        self.seen.append(ref)
        return f"refused by {ref}" if self.refuses and ref.endswith(self.refuses) else None


def answer(case, status, body, ctype="application/json", validator=None):
    """What the runner says of one fixture when the service answers this."""
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    with mock.patch.object(connect_run, "post", return_value=(status, ctype, data, None)):
        return connect_run.run_fixture("http://127.0.0.1:1", validator or Picky(), GROUP, case)


GOOD = {"ok": True, "data": DESCRIBED, "notRead": [], "warnings": []}


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

    def test_the_expected_envelope_is_matched_against_the_response(self):
        case = dict(DESCRIBE, expect={"code": "ok", "envelope": {"ok": True, "data": {"name": "other"}}})
        self.assertIn("response.data.name", answer(case, 200, GOOD))

    def test_each_schema_of_a_success_is_checked(self):
        for refuses, said in (("envelope.schema.json#/$defs/response", "response:"), ("describe.schema.json", "data:"),
                              ("request.schema.json#/$defs/describeRequest", "the fixture's own request is not valid")):
            with self.subTest(refuses=refuses):
                self.assertIn(said, answer(DESCRIBE, 200, GOOD, validator=Picky(refuses)))

    def test_a_body_that_is_not_an_envelope_object_fails_without_a_crash(self):
        for body in (b"[1]", b'{"ok": true}', b'{"ok": true, "data": NaN, "notRead": [], "warnings": []}'):
            with self.subTest(body=body):
                self.assertIsNotNone(answer(DESCRIBE, 200, body))

    def test_a_success_fixture_given_as_a_raw_body_is_checked_without_a_crash(self):
        case = dict(DESCRIBE, rawBody='{"caps": {"timeSeconds": 1, "outputBytes": 4096}}')
        self.assertIsNone(answer(case, 200, GOOD))
        self.assertIn("ok true", answer(case, 200, dict(GOOD, ok=False)))

    def test_the_fixtures_own_input_is_checked_unless_it_expects_a_refused_request(self):
        why = answer(refused(), 400, error_body("failed_precondition"), validator=Picky("mergeInput"))
        self.assertTrue(why.startswith("the fixture's own request is not valid"), why)
        case = dict(refused(), expect={"code": "invalid_argument"})
        self.assertIsNone(answer(case, 400, error_body("invalid_argument", envelope=None, message="no"), validator=Picky("mergeInput")))

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

    def test_a_fixture_that_expects_a_failure_detail_gets_one(self):
        case = {"name": "x", "request": {}, "expect": {"code": "invalid_argument", "envelope": {"ok": False, "warnings": ["x"]}}}
        why = answer(case, 400, error_body("invalid_argument", envelope=None, message="no detail"))
        self.assertIn("expects a Failure detail", why)

    def test_the_failure_is_checked_against_its_schema(self):
        why = answer(refused(), 400, error_body("failed_precondition"), validator=Picky("envelope.schema.json#/$defs/failure"))
        self.assertIn("Failure: refused", why)

    def test_hostile_error_bodies_fail_without_a_crash(self):
        cases = ((400, {"code": "failed_precondition", "details": 5}, "not a list"),
                 (418, {"code": "teapot"}, "not a Connect code"),
                 (400, b'{"code": "failed_precondition", "n": 1.0}', "write it as an integer"))
        for status, body, said in cases:
            with self.subTest(body=body):
                self.assertIn(said, answer(refused(), status, body))

    def test_details_null_is_not_read_as_left_out(self):
        why = answer(refused("invalid_argument"), 400, error_body("invalid_argument", envelope=None, message="no", details=None))
        self.assertEqual(why, "the error's details is not a list")

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
        # not base64, a cut varint, a cut string, warnings as a varint, wire type 5, field number 0, a string
        # that is not UTF-8, an unknown NotRead field, an unknown Struct entry field (inside data), an unknown
        # Struct field and an unknown ListValue field (each inside data)
        for value in ("!!", "CP8", "IgI", "IAE", "JQ", "AAA", "IgH/", "GgIYAQ", "EgYqBAoCGAE", "EgQqAhAB", "EgQyAhAB", 7):
            with self.subTest(value=value), self.assertRaises(failure.Malformed):
                failure.decode(value)



class Carry(unittest.TestCase):
    def test_the_fixtures_are_what_carry_writes_and_account_for_every_v0_case(self):
        files = carry.render()
        v0 = set()
        for name in os.listdir(carry.V0):
            with open(os.path.join(carry.V0, name), encoding="utf-8") as f:
                v0 |= {c["name"] for c in json.load(f)["cases"]}
        accounted = set()
        for name, text in files.items():
            with open(os.path.join(carry.OUT, name), encoding="utf-8") as f:
                self.assertEqual(f.read(), text, f"{name}: run conformance/carry.py")
            group = json.loads(text)
            accounted |= {c["carries"] for c in group["cases"] if "carries" in c}
            accounted |= {c["name"] for c in group.get("notCarried", [])}
            self.assertTrue(all("carries" in c or c.get("added") for c in group["cases"]), name)
        self.assertEqual(accounted, v0)

    def test_each_case_outside_changed_is_carried_as_it_is(self):
        # derived here, not through carry.expect_of: the request less contract and verb, the exit code as its
        # Connect code, ok, data and notRead as the expected envelope, and about and why, all with v0 paths
        carried = {}
        for text in carry.render().values():
            carried.update({c["carries"]: c for c in json.loads(text)["cases"] if "carries" in c})
        checked = 0
        for name in sorted(os.listdir(carry.V0)):
            with open(os.path.join(carry.V0, name), encoding="utf-8") as f:
                cases = json.load(f)["cases"]
            for v0 in cases:
                if v0["name"] in carry.CHANGED:
                    continue
                c, want = carried[v0["name"]], v0.get("expect", {})
                with self.subTest(case=v0["name"]):
                    request = {k: v for k, v in v0.get("request", {}).items() if k not in ("contract", "verb")}
                    self.assertEqual(c["request"], carry.v1(request))
                    code = {0: "ok", 1: "failed_precondition", 2: "invalid_argument", 3: "unimplemented", 75: "unavailable"}
                    self.assertEqual(c["expect"]["code"], code[want.get("exit", 0)])
                    env = c["expect"]["envelope"]
                    self.assertEqual(env["ok"], want.get("ok", want.get("exit", 0) == 0))
                    for key in ("data", "notRead"):
                        self.assertEqual(env.get(key), carry.v1(want[key]) if key in want else None, key)
                    for key in ("about", "why"):
                        self.assertEqual(c.get(key), v0.get(key), key)
                checked += 1
        self.assertGreater(checked, 50)


class Bridge(unittest.TestCase):
    """conformance/v0_bridge.py's own transport, which no fixture reaches: 415, Content-Length and the deadline.
    The handler is driven over a socket pair, so nothing connects."""

    def setUp(self):
        self.saved = v0_bridge.Handler.plugin
        v0_bridge.Handler.plugin = [sys.executable, "-c", "import time; time.sleep(5)"]

    def tearDown(self):
        v0_bridge.Handler.plugin = self.saved

    def send(self, headers, body=b'{"caps": {"timeSeconds": 60, "outputBytes": 4096}}'):
        ours, theirs = socket.socketpair()
        ours.settimeout(10)  # a bridge that never answers fails the test instead of hanging it
        served = threading.Thread(target=v0_bridge.Handler, args=(theirs, ("127.0.0.1", 0), None), daemon=True)
        served.start()
        headers = dict({"Content-Length": str(len(body))}, **headers)
        head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        ours.sendall(f"POST /{SERVICE}/Describe HTTP/1.0\r\n{head}\r\n".encode("utf-8") + body)
        resp = http.client.HTTPResponse(ours)
        resp.begin()
        out = resp.status, resp.read()
        served.join(10)
        ours.close()
        theirs.close()
        return out

    def test_a_body_that_is_not_json_by_content_type_is_415(self):
        self.assertEqual(self.send({"Content-Type": "text/plain"})[0], 415)

    def test_a_content_length_that_is_not_a_number_is_invalid_argument(self):
        for value in ("abc", "-1", "1e3", "\u0665", "12345678901"):
            with self.subTest(value=value):
                status, body = self.send({"Content-Type": "application/json", "Content-Length": value}, b"")
                self.assertEqual((status, json.loads(body)["code"]), (400, "invalid_argument"))

    def test_the_plugin_is_stopped_at_connect_timeout_ms(self):
        started = time.monotonic()
        status, body = self.send({"Content-Type": "application/json", "Connect-Timeout-Ms": "200"})
        self.assertEqual((status, json.loads(body)["code"]), (504, "deadline_exceeded"))
        self.assertLess(time.monotonic() - started, 3)

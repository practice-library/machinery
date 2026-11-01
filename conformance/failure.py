"""The Failure detail of contract v1alpha1 in its binary protobuf form, without a protobuf library.

On the Connect JSON wire an error detail is {"type": "machinery.plugin.v1alpha1.Failure", "value": <unpadded
base64 of the binary message>}. decode() turns that value into the envelope's JSON form
({ok, data, notRead, warnings}, every field present), so it can be checked against envelope.schema.json;
encode() is its inverse, for the test bridge. Field numbers are those of plugin.proto:
  Failure  1 ok bool, 2 data google.protobuf.Value, 3 not_read repeated NotRead, 4 warnings repeated string
  NotRead  1 what string, 2 why string
  Value    1 null_value, 2 number_value double, 3 string_value, 4 bool_value, 5 struct_value, 6 list_value
  Struct   1 fields map<string, Value> (entries: 1 key, 2 value);  ListValue  1 values repeated Value
"""
from __future__ import annotations

import base64
import struct

TYPE = "machinery.plugin.v1alpha1.Failure"


class Malformed(ValueError):
    pass


def _varint(buf: bytes, i: int):
    shift = value = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise Malformed("a varint runs past the end")
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes):
    """(field number, wire type, value) for each field: an int for varint and 64-bit, bytes for length-delimited."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            if i + 8 > len(buf):
                raise Malformed("a 64-bit field runs past the end")
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise Malformed("a length-delimited field runs past the end")
            value, i = buf[i:i + n], i + n
        else:
            raise Malformed(f"wire type {wire} is not used by these messages")
        if number == 0:
            raise Malformed("field number 0")
        yield number, wire, value


def _text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Malformed("a string is not UTF-8")


def _value(buf: bytes):
    out = None
    for number, wire, v in _fields(buf):
        if number == 1 and wire == 0:
            out = None
        elif number == 2 and wire == 1:
            out = struct.unpack("<d", v)[0]
            out = int(out) if out.is_integer() and abs(out) <= 2 ** 53 else out
        elif number == 3 and wire == 2:
            out = _text(v)
        elif number == 4 and wire == 0:
            out = bool(v)
        elif number == 5 and wire == 2:
            out = {}
            for n, w, entry in _fields(v):
                if n == 1 and w == 2:
                    key, val = "", None
                    for en, ew, ev in _fields(entry):
                        if en == 1 and ew == 2:
                            key = _text(ev)
                        elif en == 2 and ew == 2:
                            val = _value(ev)
                    out[key] = val
        elif number == 6 and wire == 2:
            out = [_value(item) for n, w, item in _fields(v) if n == 1 and w == 2]
        else:
            raise Malformed(f"Value field {number} with wire type {wire}")
    return out


def decode(value_b64: str) -> dict:
    """The envelope JSON of a Failure detail's base64 value. Raises Malformed."""
    if not isinstance(value_b64, str):
        raise Malformed("the detail's value is not a string")
    try:
        buf = base64.b64decode(value_b64 + "=" * (-len(value_b64) % 4), validate=True)
    except (ValueError, TypeError):
        raise Malformed("the detail's value is not base64")
    out = {"ok": False, "data": None, "notRead": [], "warnings": []}
    for number, wire, v in _fields(buf):
        if number == 1 and wire == 0:
            out["ok"] = bool(v)
        elif number == 2 and wire == 2:
            out["data"] = _value(v)
        elif number == 3 and wire == 2:
            item = {"what": "", "why": ""}
            for n, w, x in _fields(v):
                if n in (1, 2) and w == 2:
                    item["what" if n == 1 else "why"] = _text(x)
            out["notRead"].append(item)
        elif number == 4 and wire == 2:
            out["warnings"].append(_text(v))
        else:
            raise Malformed(f"Failure field {number} with wire type {wire}")
    return out


def _key(number: int, wire: int) -> bytes:
    return _uvarint(number << 3 | wire)


def _uvarint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _len(number: int, payload: bytes) -> bytes:
    return _key(number, 2) + _uvarint(len(payload)) + payload


def encode(envelope: dict) -> str:
    """The unpadded base64 value of a Failure detail for an envelope with ok false and data null."""
    buf = bytearray()
    if envelope.get("ok"):
        buf += _key(1, 0) + b"\x01"
    if envelope.get("data") is not None:
        raise ValueError("a Failure carries data null")
    buf += _len(2, _key(1, 0) + b"\x00")  # data: Value{null_value: NULL_VALUE}
    for item in envelope.get("notRead", []):
        buf += _len(3, _len(1, item["what"].encode("utf-8")) + _len(2, item["why"].encode("utf-8")))
    for w in envelope.get("warnings", []):
        buf += _len(4, w.encode("utf-8"))
    return base64.b64encode(bytes(buf)).decode("ascii").rstrip("=")

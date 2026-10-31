"""Side-effect-free MCBEWS/2 JSON validation and serialization."""

from __future__ import annotations

import json
import math
import re
from importlib.resources import files
from typing import Any

MANIFEST: dict[str, Any] = json.loads(files(__package__).joinpath("manifest.json").read_text())
LIMITS: dict[str, int] = MANIFEST["limits"]


class V2ProtocolError(ValueError):
    """A classified protocol failure, independent of transport or business handlers."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def fail(code: str = "INVALID_MESSAGE") -> None:
    raise V2ProtocolError(code)


def dumps(value: object) -> str:
    """Serialize already validated JSON values without ASCII expansion."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _tree(value: Any, depth: int = 0) -> Any:
    if depth > LIMITS["depth"]:
        fail("LIMIT_EXCEEDED")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            fail()
        return value
    if isinstance(value, (int, float)):
        if abs(value) > 9007199254740991 or not math.isfinite(value) or value != int(value):
            fail()
        return int(value)
    if isinstance(value, list):
        return [_tree(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            fail()
        return {_tree(key): _tree(item, depth + 1) for key, item in value.items()}
    fail()


def _matches(pattern: str, value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value, flags=re.ASCII) is not None


def validate(message: object) -> dict[str, Any]:
    """Return a detached normalized message or raise a classified protocol error."""
    obj = _tree(message)
    if not isinstance(obj, dict):
        fail()
    if "v" not in obj:
        fail()
    if obj["v"] != MANIFEST["protocol"]:
        fail("UNSUPPORTED_VERSION")
    kind = obj.get("kind")
    if not isinstance(kind, str) or kind not in ("request", "response", "notification"):
        fail()
    common = MANIFEST["fields"]["common"]
    fields = MANIFEST["fields"][kind]
    required = set(common["required"] + fields["required"])
    allowed = required | set(common["optional"]) | set(fields.get("exclusive", []))
    if not required <= obj.keys() or obj.keys() - allowed:
        fail()
    if kind == "response":
        if ("result" in obj) == ("error" in obj):
            fail()
    else:
        if not _matches(MANIFEST["method_pattern"], obj["method"]):
            fail()
    if "id" in obj and not _matches(MANIFEST["id_pattern"], obj["id"]):
        fail()
    if "error" in obj:
        err = obj["error"]
        if (
            not isinstance(err, dict)
            or not {"code", "message"} <= err.keys()
            or err.keys() - {"code", "message", "data"}
        ):
            fail()
        if not isinstance(err["code"], str) or err["code"] not in MANIFEST["errors"]:
            fail()
        if not isinstance(err["message"], str) or len(err["message"].encode()) > 256:
            fail()
    if "meta" in obj:
        meta = obj["meta"]
        if not isinstance(meta, dict):
            fail()
        for key, value in meta.items():
            if key.startswith("sdk.") and key not in MANIFEST["metadata"]["reserved"]:
                fail()
            if key == "sdk.trace_id" and not _matches(MANIFEST["token_pattern"], value):
                fail()
            if key == "sdk.identity" and not isinstance(value, dict):
                fail()
            if key == "sdk.capabilities":
                if (
                    not isinstance(value, list)
                    or len(value) > 16
                    or not all(_matches(MANIFEST["token_pattern"], item) for item in value)
                ):
                    fail()
                if len(set(value)) != len(value):
                    fail()
        if len(dumps(meta).encode()) > LIMITS["metadata_bytes"]:
            fail("LIMIT_EXCEEDED")
    if len(dumps(obj).encode()) > LIMITS["message_bytes"]:
        fail("LIMIT_EXCEEDED")
    return obj  # type: ignore[no-any-return]


def encode(message: object) -> str:
    """Encode a validated logical message without sending anything."""
    return dumps(validate(message))


def decode(wire: str) -> dict[str, Any]:
    """Decode bounded scalar UTF-8 JSON and apply the same validation as encode."""
    try:
        size = len(wire.encode())
    except UnicodeError:
        fail()
        raise AssertionError("unreachable") from None
    if size > LIMITS["message_bytes"]:
        fail("LIMIT_EXCEEDED")
    try:
        value = json.loads(wire, parse_constant=lambda _: fail("MALFORMED_JSON"))
    except (ValueError, RecursionError):
        fail("MALFORMED_JSON")
    return validate(value)

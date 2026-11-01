#!/usr/bin/env python3
"""Probe the installed V2-only wheel from a directory outside the checkout.

Run with the contract virtualenv's interpreter and ``PYTHONPATH`` cleared::

    cd /tmp
    /tmp/contract-venv/bin/python "$GITHUB_WORKSPACE/tools/probe_installed_contract.py"

The probe proves the built wheel exposes only the ``McbeServer`` entry point,
ships only the MCBEWS/2 authority, and rejects pre-V2 business payloads.
"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.util import find_spec
from typing import Any

import mcbe_ws_sdk as sdk
from mcbe_ws_sdk.profiles.mcbews_v2.codec import MANIFEST, V2ProtocolError, decode, encode
from mcbe_ws_sdk.profiles.mcbews_v2.transport import (
    ReassemblyPool,
    fragment,
)

# Public names that the V2-only cutover removed. Both the attribute and
# ``__all__`` checks matter: a stale re-export is still a compatibility entry.
REMOVED_PUBLIC_NAMES: tuple[str, ...] = (
    "McbeServerFacade",
    "AddonBridgeService",
    "AddonBridgeClient",
    "ConnectionAddonBridgeClient",
    "AddonBridgeSettings",
    "AddonBridgeProfile",
    "AddonControlHook",
    "AddonMessageResult",
    "LegacyUiChatHook",
    "LegacyUiChatHookAdapter",
    "LegacyUiChatCallbackAdapter",
    "McbewsV1Profile",
    "McbewsV1Protocol",
    "McbewsV1Delivery",
    "MCBEWS_V1",
    "MCBEWS_V1_MANIFEST",
    "MCBEWS_V1_WIRE_VECTORS",
    "ApprovalDecision",
    "SessionError",
    "SessionRequest",
    "SessionResponse",
    "TextResponseChunk",
    "TextResponseMessage",
    "TokenUsage",
    "UiChatMessage",
    "ToolPlayerMessage",
    "decode_approval_decision",
    "decode_session_request_chat",
    "decode_session_response_message",
    "decode_text_response_frame",
    "encode_approval_decision",
    "encode_session_request_chat",
    "encode_session_response_command",
    "encode_text_response_commands",
)

REMOVED_SUBMODULES: tuple[str, ...] = (
    "mcbe_ws_sdk.profiles.mcbews_v1",
    "mcbe_ws_sdk.extensions.mcbews_v1",
    "mcbe_ws_sdk.addon",
)

STREAM_KINDS: set[str] = {
    "stream_open",
    "stream_item",
    "stream_end",
    "stream_error",
    "stream_cancel",
}


def _package_json(name: str) -> Any:
    package = files("mcbe_ws_sdk.profiles.mcbews_v2")
    return json.loads(package.joinpath(name).read_text(encoding="utf-8"))


def probe_assets() -> None:
    """Public surface and packaged authority checks."""
    assert hasattr(sdk, "McbeServer"), "installed wheel is missing McbeServer"
    assert isinstance(sdk.McbeServer, type), "McbeServer is not a class"
    assert hasattr(sdk, "V2Bridge"), "installed wheel is missing V2Bridge"
    exported = set(getattr(sdk, "__all__", ()))
    assert "McbeServer" in exported, "McbeServer is not a declared public export"

    for name in REMOVED_PUBLIC_NAMES:
        assert not hasattr(sdk, name), f"retired public name still exists: {name}"
        assert name not in exported, f"retired public name still exported: {name}"

    for module in REMOVED_SUBMODULES:
        try:
            spec = find_spec(module)
        except ModuleNotFoundError:
            spec = None
        assert spec is None, f"retired subpackage is still importable: {module}"

    manifest = _package_json("manifest.json")
    vectors = _package_json("vectors.json")
    assert manifest == MANIFEST, "installed codec MANIFEST differs from packaged manifest"
    assert manifest["protocol"] == "MCBEWS/2", manifest.get("protocol")
    assert manifest["carrier"]["event"] == "mcbews:packet_v2", manifest["carrier"]
    negotiation = manifest["negotiation"]
    assert negotiation["event"] == "mcbews:negotiate_v2", negotiation
    if "versions" in manifest:
        assert manifest["versions"] == ["MCBEWS/2"], manifest["versions"]
    assert negotiation.get("versions") == ["MCBEWS/2"], negotiation.get("versions")
    assert "v1_methods" not in negotiation and "legacy" not in negotiation
    assert "stream/1" in negotiation["features"], negotiation["features"]
    assert STREAM_KINDS.issubset(manifest["fields"]), manifest["fields"].keys()
    limits = manifest["limits"]
    assert limits["command_bytes"] == 461, limits
    assert all(isinstance(value, int) and value > 0 for value in limits.values()), limits
    streams = manifest["streams"]
    assert all(isinstance(value, int) and value > 0 for value in streams.values()), streams

    assert isinstance(vectors.get("valid"), list) and vectors["valid"]
    assert isinstance(vectors.get("invalid"), list) and vectors["invalid"]
    assert isinstance(vectors.get("raw_invalid"), list) and vectors["raw_invalid"]
    for vector in vectors["valid"]:
        assert decode(encode(vector["message"])) == vector["message"], (
            f"valid vector does not round-trip: {vector['name']}"
        )


def probe_transport_round_trip() -> None:
    """Round-trip a v2 request through the public codec and carrier helpers."""
    request = {
        "v": "MCBEWS/2",
        "kind": "request",
        "id": "p.wheel.1",
        "method": "wheel.echo",
        "params": {"n": 1, "text": "玩家😀"},
    }
    assert decode(encode(request)) == request

    for direction, transport_id in (("down", "p.wheel.1"), ("up", "a.wheel.1")):
        frames = fragment(request, transport_id, direction)
        assert frames, "fragment returned no frames"
        pool = ReassemblyPool()
        decoded = None
        for frame in frames:
            decoded = pool.receive("wheel", "server", frame, direction)
        assert decoded == request, f"{direction} carrier round-trip failed"

    response = {"v": "MCBEWS/2", "kind": "response", "id": "p.wheel.1", "result": {"ok": True}}
    assert decode(encode(response)) == response


def probe_legacy_rejection() -> None:
    """Pre-V2 business payloads must not decode as accepted messages."""
    cases = (
        # v1-shaped request without the MCBEWS/2 version field.
        '{"kind":"request","id":"p.wheel.1","method":"wheel.echo","params":{}}',
        # Explicit retired protocol version.
        '{"v":"MCBEWS/1","kind":"request","id":"p.wheel.1","method":"wheel.echo","params":{}}',
        # Numeric v1 version.
        '{"v":1,"kind":"request","id":"p.wheel.1","method":"wheel.echo","params":{}}',
    )
    for wire in cases:
        try:
            decode(wire)
        except V2ProtocolError as exc:
            assert exc.code in {"UNSUPPORTED_VERSION", "INVALID_MESSAGE"}, exc.code
        else:
            raise AssertionError(f"retired payload was accepted: {wire}")

    # The retired carrier prefix is also not a valid v2 fragment.
    try:
        ReassemblyPool().receive("wheel", "server", "MCBEWS|BRIDGE|p.wheel.1|1/1|{}", "up")
    except V2ProtocolError as exc:
        assert exc.code == "INVALID_FRAGMENT", exc.code
    else:
        raise AssertionError("retired carrier frame was accepted")


async def probe_runtime() -> None:
    """Backward-compatible async entry point for the existing release test."""
    probe_transport_round_trip()
    probe_legacy_rejection()


def main() -> None:
    version = getattr(sdk, "__version__", "")
    assert isinstance(version, str) and version, "installed wheel has no version"
    probe_assets()
    probe_transport_round_trip()
    probe_legacy_rejection()
    print(f"installed v2-only assets, codecs, carrier and rejection contracts: ok ({sdk.__file__})")


if __name__ == "__main__":
    main()

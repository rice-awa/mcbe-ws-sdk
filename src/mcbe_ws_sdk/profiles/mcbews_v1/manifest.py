"""Canonical MCBEWS/1 protocol assets.

The JSON resources in this module are intentionally small and executable.  The
Python codec, the reference Addon and downstream conformance checks all project
their constants and vectors from the same shape instead of treating a hand
copied list of strings as the protocol authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources
from types import MappingProxyType
from typing import Any, cast


def _load_json(name: str) -> dict[str, Any]:
    resource = resources.files(__package__).joinpath(name)
    with resource.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"MCBEWS/1 asset {name} must contain a JSON object")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def load_manifest() -> Mapping[str, Any]:
    """Load the immutable MCBEWS/1 manifest from the installed package."""

    return cast(Mapping[str, Any], _freeze(_load_json("manifest.json")))


def load_wire_vectors() -> Mapping[str, Any]:
    """Load the immutable executable MCBEWS/1 conformance vectors."""

    return cast(Mapping[str, Any], _freeze(_load_json("vectors.json")))


MCBEWS_V1_MANIFEST = load_manifest()
MCBEWS_V1_WIRE_VECTORS = load_wire_vectors()

__all__ = [
    "MCBEWS_V1_MANIFEST",
    "MCBEWS_V1_WIRE_VECTORS",
    "load_manifest",
    "load_wire_vectors",
]

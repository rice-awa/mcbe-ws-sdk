#!/usr/bin/env python3
"""Deterministically project independent v2 JSON assets into TypeScript."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_protocol_assets import _ts_literal

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'src/mcbe_ws_sdk/profiles/mcbews_v2'
OUTPUT = ROOT / 'addon/scripts/bridge/v2/assets.ts'


def render() -> str:
    lines = ['/** Generated from v2 manifest/vectors; do not edit. */']
    types = {
        'manifest': '{ protocol: string; fields: Record<string, { required: string[]; optional?: string[]; exclusive?: string[] }>; id_pattern: string; method_pattern: string; token_pattern: string; metadata: { reserved: string[] }; errors: string[]; carrier: { event: string; prefix: string }; negotiation: { event: string; hello_prefix: string; response_prefix: string; versions: string[]; features: string[]; v1_methods: string[]; max_attempts: number; hello: string; response: string; confirm: string; ready: string; busy: string; refresh: string; lease_ack: string; close: string; epoch: string; acceptance: string; legacy: string; max_peers: number; max_challenges: number; challenge_ms: number; lease_ms: number; refresh_ms: number; max_control_sends: number }; limits: Record<string, number>; streams: Record<string, number> }',
        'vectors': '{ valid: Vector[]; invalid: Vector[]; raw_invalid: { name: string; wire: string; expected: string }[] }',
    }
    lines.append('export type Vector = { name: string; message: unknown; expected: string };')
    for name in types:
        value = json.loads((ASSETS / f'{name}.json').read_text())
        literal = _ts_literal(json.dumps(value, ensure_ascii=True, separators=(',', ':')))
        lines.append(f'export const {name.upper()} = JSON.parse({literal}) as {types[name]};')
    return '\n'.join(lines) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != expected:
            raise SystemExit('stale v2 assets; run tools/generate_v2_assets.py')
        print('generate_v2_assets: ok')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected)
        print('generate_v2_assets: wrote assets.ts')


if __name__ == '__main__':
    main()

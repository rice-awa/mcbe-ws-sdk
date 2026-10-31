#!/usr/bin/env python3
"""Compare identical logical vectors as JSON objects and positional JSON arrays."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def measure() -> list[dict[str, int | str]]:
    vectors = json.loads((ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v2/vectors.json").read_text())
    rows = []
    for vector in vectors["valid"]:
        msg = vector["message"]
        # Keep the R09 eight-position candidate experiment scoped to atomic messages.
        if msg["kind"].startswith("stream_"):
            continue
        compact = [
            msg.get(k) for k in ("v", "kind", "id", "method", "params", "result", "error", "meta")
        ]

        def cost(obj: object) -> tuple[int, int]:
            payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            header = "MCBEWS2|p." + "x" * 24 + ".9999999999|1024/1024|"
            prefix = "scriptevent mcbews:packet_v2 " + header
            count, size, total = 1, len(prefix.encode()), 0
            for ch in payload:
                n = len(ch.encode())
                if size + n > 461:
                    total += size
                    count += 1
                    size = len(prefix.encode())
                size += n
            return count, total + size

        jc, jb = cost(msg)
        cc, cb = cost(compact)
        rows.append(
            dict(
                name=vector["name"],
                json_commands=jc,
                json_final_command_bytes=jb,
                compact_commands=cc,
                compact_final_command_bytes=cb,
            )
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(measure(), ensure_ascii=False, indent=2))

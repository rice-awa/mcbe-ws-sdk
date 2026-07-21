#!/usr/bin/env python3
"""Auto-format Python and (optionally) Addon TypeScript sources.

Usage::

    python tools/format.py            # format everything available
    python tools/format.py --check    # fail if anything would change
    python tools/format.py --python   # Python only (ruff)
    python tools/format.py --addon    # Addon only (prettier)

Exit code 0 on success; non-zero if a formatter is missing or a check fails.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYTHON_PATHS: tuple[str, ...] = ("src", "tests", "examples", "tools")


def _run(cmd: list[str], *, cwd: Path = ROOT) -> int:
    print("+", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    return proc.returncode


def _which_python_tools() -> str | None:
    """Prefer the active interpreter's ruff module; fall back to PATH ``ruff``."""
    # ``python -m ruff`` works after ``pip install -e ".[dev]"``.
    return sys.executable


def format_python(*, check: bool) -> int:
    python = _which_python_tools()
    assert python is not None
    ruff_base = [python, "-m", "ruff"]

    # Prefer module form; if ruff is not installed as a module, use bare binary.
    probe = subprocess.run(
        [*ruff_base, "--version"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        if shutil.which("ruff") is None:
            print('error: ruff not found; run: pip install -e ".[dev]"', file=sys.stderr)
            return 1
        ruff_base = ["ruff"]

    fmt_cmd = [*ruff_base, "format"]
    if check:
        fmt_cmd.append("--check")
    fmt_cmd.extend(PYTHON_PATHS)
    code = _run(fmt_cmd)
    if code != 0:
        return code

    # Apply safe auto-fixes (import order, etc.). In --check mode only report.
    fix_cmd = [*ruff_base, "check", "--no-cache"]
    if check:
        # Non-zero if any rule would fire (including ones --fix could heal).
        fix_cmd.append("--diff")
    else:
        fix_cmd.append("--fix")
    fix_cmd.extend(PYTHON_PATHS)
    return _run(fix_cmd)


def format_addon(*, check: bool) -> int:
    """Format Addon TS via the package's own prettier scripts (npm run format)."""
    addon = ROOT / "addon"
    if not addon.is_dir():
        print("error: addon/ directory not found", file=sys.stderr)
        return 1

    npm = shutil.which("npm")
    if npm is None:
        print("error: npm not found; install Node.js to format the addon", file=sys.stderr)
        return 1

    # Prefer local node_modules/.bin/prettier via package.json scripts so the
    # pinned prettier version (devDependency) is used — same as `npm run format`.
    if not (addon / "node_modules" / "prettier").is_dir():
        print(
            "error: addon prettier not installed; run: (cd addon && npm ci)",
            file=sys.stderr,
        )
        return 1

    script = "format:check" if check else "format"
    return _run([npm, "run", script], cwd=addon)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write files; exit non-zero if reformatting is needed",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--python",
        action="store_true",
        help="format Python only (src/tests/examples/tools)",
    )
    scope.add_argument(
        "--addon",
        action="store_true",
        help="format Addon TypeScript/JS only",
    )
    args = parser.parse_args(argv)

    run_python = args.python or not args.addon
    run_addon = args.addon or not args.python

    # Default "everything" mode: Python is required; Addon is best-effort so a
    # machine without Node still formats the Python tree cleanly.
    codes: list[int] = []
    if run_python:
        codes.append(format_python(check=args.check))
    if run_addon:
        if args.addon:
            codes.append(format_addon(check=args.check))
        else:
            # Best-effort in full mode.
            if shutil.which("npm") is None:
                print("skip addon: npm not found", flush=True)
            elif not (ROOT / "addon").is_dir():
                print("skip addon: addon/ missing", flush=True)
            elif not (ROOT / "addon" / "node_modules" / "prettier").is_dir():
                print("skip addon: prettier not installed (cd addon && npm ci)", flush=True)
            else:
                codes.append(format_addon(check=args.check))

    return 0 if all(code == 0 for code in codes) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Make sure the README keeps its live GitHub badges and container link."""

from __future__ import annotations

from pathlib import Path

from check_versions import validate


README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
REQUIRED = {
    "test status badge": "actions/workflows/tests.yml/badge.svg",
    "container package link": "/pkgs/container/ps2-save-tool-web",
    "release badge": "img.shields.io/github/v/release/Chrisser73/ps2-save-tool",
    "license badge": "img.shields.io/github/license/Chrisser73/ps2-save-tool",
}

desktop, web = validate()
REQUIRED["desktop version badge"] = f"desktop-v{desktop}"
REQUIRED["web version badge"] = f"web-v{web}"

missing = [label for label, value in REQUIRED.items() if value not in README]
if missing:
    raise SystemExit("README badge check failed; missing: " + ", ".join(missing))

print("README badges and package link are present.")

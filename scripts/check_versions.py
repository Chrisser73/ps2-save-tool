#!/usr/bin/env python3
"""Validate the independently versioned desktop and web editions."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def read_desktop_version() -> str:
    source = (ROOT / "ps2_save_tool.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        raise ValueError("Could not find APP_VERSION in ps2_save_tool.py")
    return match.group(1)


def read_web_versions() -> dict[str, str]:
    canonical = (ROOT / "web" / "VERSION").read_text(encoding="utf-8").strip()
    package = json.loads((ROOT / "web" / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    return {"web/VERSION": canonical, "web/frontend/package.json": package}


def validate() -> tuple[str, str]:
    desktop = read_desktop_version()
    web_versions = read_web_versions()
    web = web_versions["web/VERSION"]

    for label, version in {"desktop": desktop, **web_versions}.items():
        if not SEMVER.fullmatch(version):
            raise ValueError(f"{label} is not a valid semantic version: {version!r}")
    mismatches = [f"{label}={version}" for label, version in web_versions.items() if version != web]
    if mismatches:
        raise ValueError("Web versions must match: " + ", ".join(mismatches))
    return desktop, web


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("desktop", "web"), help="Print the release tag for one component.")
    args = parser.parse_args()
    desktop, web = validate()
    if args.component:
        print(f"{args.component}-v{desktop if args.component == 'desktop' else web}")
        return
    print(f"Desktop version: {desktop}")
    print(f"Web version:     {web}")


if __name__ == "__main__":
    main()

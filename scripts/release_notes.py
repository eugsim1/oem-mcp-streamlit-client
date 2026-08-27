#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def newest_section(changelog: str) -> tuple[str, str]:
    matches = list(HEADING_RE.finditer(changelog))
    if not matches:
        raise ValueError("CHANGELOG.md has no '## <version>' release section")
    first = matches[0]
    end = matches[1].start() if len(matches) > 1 else len(changelog)
    heading = first.group(1).strip()
    notes = changelog[first.end() : end].strip()
    if not notes:
        raise ValueError("the newest CHANGELOG.md release section is empty")
    version = heading.split(" - ", 1)[0].strip()
    return version, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract the newest changelog release section.")
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--title-file", required=True)
    parser.add_argument("--notes-file", required=True)
    parser.add_argument("--short-sha", default="")
    args = parser.parse_args()
    version, notes = newest_section(Path(args.changelog).read_text(encoding="utf-8"))
    suffix = f" · {args.short_sha}" if args.short_sha else ""
    Path(args.title_file).write_text(f"v{version}{suffix}\n", encoding="utf-8")
    Path(args.notes_file).write_text(f"{notes}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

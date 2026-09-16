#!/usr/bin/env python3
"""Allow only expected author and committer identities for this repository."""

from __future__ import annotations

import subprocess
import sys

ALLOWED_AUTHORS = {
    ("NET86", "43442823+NET86@users.noreply.github.com"),
    ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com"),
}
ALLOWED_COMMITTERS = ALLOWED_AUTHORS | {
    ("GitHub", "noreply@github.com"),
}


def current_identity() -> tuple[tuple[str, str], tuple[str, str]]:
    result = subprocess.run(
        ["git", "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    parts = result.stdout.strip().split("\x00")
    if len(parts) != 4:
        raise ValueError("unexpected git identity format")
    return (parts[0], parts[1]), (parts[2], parts[3])


def validate_identity(author: tuple[str, str], committer: tuple[str, str]) -> None:
    if author not in ALLOWED_AUTHORS:
        raise ValueError("unexpected commit author identity")
    if committer not in ALLOWED_COMMITTERS:
        raise ValueError("unexpected commit committer identity")


def main() -> int:
    try:
        validate_identity(*current_identity())
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

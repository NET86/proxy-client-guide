#!/usr/bin/env python3
"""Allow only expected author and committer identities for this repository."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ALLOWED_AUTHORS = {
    ("NET86", "43442823+NET86@users.noreply.github.com"),
    ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com"),
}
ALLOWED_COMMITTERS = ALLOWED_AUTHORS | {
    ("GitHub", "noreply@github.com"),
}


def git(*args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], input=input, check=True, capture_output=True, text=True,
    ).stdout.strip()


def event_sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise ValueError("missing or invalid GitHub event commit SHA")
    return value.lower()


def validation_range() -> tuple[str | None, str, bool]:
    """Return the trusted base, proposed head and whether this is a PR."""
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    if event_name:
        if event_name not in {"pull_request", "push"}:
            raise ValueError("unsupported GitHub validation event")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if event_name == "pull_request":
            base = event_sha(event["pull_request"]["base"]["sha"])
            head = event_sha(event["pull_request"]["head"]["sha"])
        else:
            base, head = event_sha(event["before"]), event_sha(event["after"])
        if head == "0" * 40 or (event_name == "pull_request" and base == "0" * 40):
            raise ValueError("GitHub event lacks a valid commit range")
        head = git("rev-parse", "--verify", f"{head}^{{commit}}")
        if base == "0" * 40:
            base = None  # First push: validate every introduced commit.
        else:
            base = git("rev-parse", "--verify", f"{base}^{{commit}}")
        if event_name == "push" and git("rev-parse", "HEAD") != head:
            raise ValueError("checkout does not match the pushed revision")
        if event_name == "pull_request":
            # The workflow checks out the synthetic merge; its proposed head
            # must actually belong to that checkout as well as to the event.
            git("merge-base", "--is-ancestor", head, "HEAD")
        return base, head, event_name == "pull_request"
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise ValueError("GitHub validation requires event metadata")
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    return (parents[1] if len(parents) > 1 else None), parents[0], False


def current_identity(revision: str = "HEAD") -> tuple[tuple[str, str], tuple[str, str]]:
    result = subprocess.run(
        ["git", "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", revision],
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-output", type=Path, help="Export checked base/head for the whitespace gate")
    args = parser.parse_args()
    try:
        base, head, is_pr = validation_range()
        revisions = git("rev-list", f"{base}..{head}" if base else head).splitlines()
        # Include the synthetic merge itself, but never let its allowed
        # identity substitute for checking the PR's original commits.
        revisions.append(git("rev-parse", "HEAD"))
        for revision in dict.fromkeys(revisions):
            validate_identity(*current_identity(revision))
        if args.github_output:
            whitespace_base = git("merge-base", base, head) if is_pr else base
            if whitespace_base is None:
                whitespace_base = git("hash-object", "-w", "-t", "tree", "--stdin", input="")
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(f"base={whitespace_base}\nhead={head}\n")
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

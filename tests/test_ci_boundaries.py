"""Exercise CI gates against real disposable Git histories, without network access."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import test_github_rediscovery as discovery_fixtures

d = discovery_fixtures.d
NOW = discovery_fixtures.NOW

ROOT = Path(__file__).resolve().parents[1]


class CIBoundaryTests(unittest.TestCase):
    def git(self, directory, *args, env=None):
        result = subprocess.run(
            ["git", *args], cwd=directory, env=env or self.fixture_env,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def init_fixture(self, directory):
        self.fixture_env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "GITHUB_"))
        }
        self.fixture_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git(directory, "init", "--initial-branch=main")
        self.git(directory, "config", "user.name", "NET86")
        self.git(directory, "config", "user.email", "43442823+NET86@users.noreply.github.com")
        self.git(directory, "config", "commit.gpgsign", "false")
        (directory / "fixture.txt").write_text("base\n", encoding="utf-8")
        self.git(directory, "add", "fixture.txt")
        self.git(directory, "commit", "-m", "base")
        return self.git(directory, "rev-parse", "HEAD")

    def check_identity(self, directory, event_name, event):
        event_file = directory / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")
        output = directory / "github-output"
        env = dict(self.fixture_env, GITHUB_ACTIONS="true", GITHUB_EVENT_NAME=event_name,
                   GITHUB_EVENT_PATH=str(event_file))
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_commit_identity.py"), "--github-output", str(output)],
            cwd=directory, env=env, capture_output=True, text=True, timeout=20,
        )
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
        return result, values

    def check_whitespace(self, directory, base, head):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        step = workflow.split("- name: Check whitespace", 1)[1]
        command = re.search(r"\brun: ([^\n]+)", step).group(1)
        self.assertIn("CI_BASE_SHA: ${{ steps.identity.outputs.base }}", step)
        self.assertIn("CI_HEAD_SHA: ${{ steps.identity.outputs.head }}", step)
        return subprocess.run(
            command, shell=True, cwd=directory,
            env=dict(self.fixture_env, CI_BASE_SHA=base, CI_HEAD_SHA=head),
            capture_output=True, text=True, timeout=20,
        )

    def test_identity_gate_rejects_unauthorized_author_in_pr_parent(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = self.init_fixture(directory)
            self.git(directory, "switch", "-c", "proposal")
            author_env = dict(self.fixture_env, GIT_AUTHOR_NAME="Other User",
                              GIT_AUTHOR_EMAIL="other@example.invalid")
            self.git(directory, "commit", "--allow-empty", "-m", "unapproved author", env=author_env)
            self.git(directory, "commit", "--allow-empty", "-m", "allowed feature tip")
            head = self.git(directory, "rev-parse", "HEAD")
            self.git(directory, "switch", "main")
            self.git(directory, "merge", "--no-ff", "proposal", "-m", "synthetic pull request merge")
            result, outputs = self.check_identity(
                directory, "pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}},
            )
            self.assertNotEqual(result.returncode, 0,
                                "identity gate accepted an unauthorized PR parent behind an allowed merge HEAD")
            self.assertIn("unexpected commit author identity", result.stderr)
            self.assertEqual(outputs, {})

    def test_whitespace_gate_rejects_committed_trailing_spaces(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = self.init_fixture(directory)
            (directory / "fixture.txt").write_text("committed trailing spaces  \n", encoding="utf-8")
            self.git(directory, "add", "fixture.txt")
            self.git(directory, "commit", "-m", "whitespace defect")
            self.assertEqual(self.git(directory, "status", "--porcelain"), "")
            control = subprocess.run(["git", "diff", "--check", base, "HEAD"], cwd=directory,
                                     env=self.fixture_env, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(control.returncode, 0, "fixture must contain committed whitespace errors")
            head = self.git(directory, "rev-parse", "HEAD")
            result = self.check_whitespace(directory, base, head)
            self.assertNotEqual(result.returncode, 0,
                                "workflow whitespace gate accepted a committed trailing-space defect")
            self.assertIn("trailing whitespace", result.stdout)

    def test_multi_commit_push_rejects_unauthorized_committer_before_allowed_tip(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = self.init_fixture(directory)
            committer_env = dict(self.fixture_env, GIT_COMMITTER_NAME="Other User",
                                 GIT_COMMITTER_EMAIL="other@example.invalid")
            self.git(directory, "commit", "--allow-empty", "-m", "unapproved committer", env=committer_env)
            self.git(directory, "commit", "--allow-empty", "-m", "allowed tip")
            head = self.git(directory, "rev-parse", "HEAD")
            for before in (base, "0" * 40):
                with self.subTest(before=before):
                    result, outputs = self.check_identity(directory, "push", {"before": before, "after": head})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unexpected commit committer identity", result.stderr)
                    self.assertEqual(outputs, {})

    def test_allowed_push_and_initial_push_export_usable_whitespace_range(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = self.init_fixture(directory)
            self.git(directory, "commit", "--allow-empty", "-m", "allowed tip")
            head = self.git(directory, "rev-parse", "HEAD")
            for before in (base, "0" * 40):
                with self.subTest(before=before):
                    result, outputs = self.check_identity(directory, "push", {"before": before, "after": head})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(outputs["head"], head)
                    if before == base:
                        self.assertEqual(outputs["base"], base)
                    whitespace = self.check_whitespace(directory, outputs["base"], outputs["head"])
                    self.assertEqual(whitespace.returncode, 0, whitespace.stderr)

    def test_pr_uses_merge_base_and_does_not_audit_unrelated_base_history(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            common = self.init_fixture(directory)
            self.git(directory, "switch", "-c", "proposal")
            (directory / "fixture.txt").write_text("proposal\n", encoding="utf-8")
            self.git(directory, "add", "fixture.txt")
            self.git(directory, "commit", "-m", "allowed feature")
            head = self.git(directory, "rev-parse", "HEAD")
            self.git(directory, "switch", "main")
            (directory / "unrelated.txt").write_text("unrelated base whitespace  \n", encoding="utf-8")
            self.git(directory, "add", "unrelated.txt")
            author_env = dict(self.fixture_env, GIT_AUTHOR_NAME="Other User", GIT_AUTHOR_EMAIL="other@example.invalid")
            self.git(directory, "commit", "-m", "trusted existing base history", env=author_env)
            base = self.git(directory, "rev-parse", "HEAD")
            self.git(directory, "merge", "--no-ff", "proposal", "-m", "synthetic merge")
            result, outputs = self.check_identity(
                directory, "pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(outputs, {"base": common, "head": head})
            whitespace = self.check_whitespace(directory, outputs["base"], outputs["head"])
            self.assertEqual(whitespace.returncode, 0, whitespace.stdout + whitespace.stderr)

    def test_invalid_or_missing_event_revisions_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            head = self.init_fixture(directory)
            cases = (
                ("push", {"before": head}),
                ("push", {"before": "bad", "after": head}),
                ("push", {"before": "f" * 40, "after": head}),
                ("pull_request", {"pull_request": {"base": {"sha": "0" * 40}, "head": {"sha": head}}}),
                ("schedule", {}),
            )
            for event_name, event in cases:
                with self.subTest(event_name=event_name, event=event):
                    result, outputs = self.check_identity(directory, event_name, event)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(outputs, {})

    def test_ci_fetches_full_history_and_exports_the_checked_range(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn('python3 scripts/check_commit_identity.py --github-output "$GITHUB_OUTPUT"', workflow)

    def test_discovery_treats_renamed_aliases_as_one_repository(self):
        client = copy.deepcopy({item["id"]: item for item in d.load_clients()}["karing"])
        metadata = discovery_fixtures.GithubRediscoveryTests.repo(
            990002, 131734194, "KaringX/karing-next", "Karing proxy utility",
        )
        requested = []

        def request(url, token=None):
            requested.append(url)
            self.assertIn(url, {
                "https://api.github.com/repos/KaringX/karing-old",
                "https://api.github.com/repos/KaringX/karing-next",
            })
            return copy.deepcopy(metadata)

        record = d.audit_github_discovery(
            client, {}, NOW, None, None, request,
            lambda _url: "https://github.com/KaringX/karing-old https://github.com/KaringX/karing-next",
        )
        self.assertEqual(len(requested), 2)
        self.assertEqual(record["state"], "verified", record)
        self.assertEqual(record["repo_id"], 990002)


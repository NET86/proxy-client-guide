"""Review-only reproductions; fixtures never write production data or remote refs."""
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


class RemainingGapsReviewTests(unittest.TestCase):
    def git(self, directory, *args, env=None):
        result = subprocess.run(
            ["git", *args], cwd=directory, env=env or self.fixture_env,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def init_fixture(self, directory):
        self.fixture_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.fixture_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git(directory, "init", "--initial-branch=main")
        self.git(directory, "config", "user.name", "NET86")
        self.git(directory, "config", "user.email", "43442823+NET86@users.noreply.github.com")
        self.git(directory, "config", "commit.gpgsign", "false")
        (directory / "fixture.txt").write_text("base\n", encoding="utf-8")
        self.git(directory, "add", "fixture.txt")
        self.git(directory, "commit", "-m", "base")
        return self.git(directory, "rev-parse", "HEAD")

    def test_identity_gate_rejects_unauthorized_author_in_pr_parent(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = self.init_fixture(directory)
            self.git(directory, "switch", "-c", "proposal")
            author_env = dict(self.fixture_env, GIT_AUTHOR_NAME="Other User",
                              GIT_AUTHOR_EMAIL="other@example.invalid")
            self.git(directory, "commit", "--allow-empty", "-m", "unapproved author", env=author_env)
            head = self.git(directory, "rev-parse", "HEAD")
            self.git(directory, "switch", "main")
            self.git(directory, "merge", "--no-ff", "proposal", "-m", "synthetic pull request merge")
            event = directory / "event.json"
            event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}))
            env = dict(self.fixture_env, GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_commit_identity.py")],
                cwd=directory, env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertNotEqual(result.returncode, 0,
                                "identity gate accepted an unauthorized PR parent behind an allowed merge HEAD")

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
            workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
            command = re.search(r"- name: Check whitespace\s+run: ([^\n]+)", workflow).group(1)
            result = subprocess.run(command, shell=True, cwd=directory, env=self.fixture_env,
                                    capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0,
                                "workflow whitespace gate accepted a committed trailing-space defect")

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


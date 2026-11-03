"""Regressions for pinned core paths and public coauthor attribution."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest

import test_ci_boundaries as ci_fixtures
import test_directory as directory_fixtures

d = directory_fixtures.d
NOW = directory_fixtures.NOW


class PinnedCoreAndCoauthorTests(unittest.TestCase):
    def test_pinned_app_store_core_rejects_reassigned_or_missing_content_path(self):
        client = next(item for item in d.load_clients() if item["id"] == "hako")
        pin = client["app_store_core_repo"]
        sha = "a" * 40
        metadata = {
            "id": pin["id"], "full_name": pin["repository"],
            "owner": {"id": 334401670}, "archived": False, "disabled": False,
            "pushed_at": d.iso(NOW),
        }
        by_id = f"https://api.github.com/repositories/{pin['id']}"
        by_path = f"https://api.github.com/repos/{pin['repository']}"

        for mode in ("matching", "reassigned", "missing"):
            with self.subTest(mode=mode):
                path_metadata = copy.deepcopy(metadata)
                if mode == "reassigned":
                    path_metadata["id"] = 987654
                if mode == "missing":
                    path_metadata = None
                calls, reads = [], []

                def api(url, token=None):
                    calls.append(url)
                    if url == by_id:
                        # Model a stale ID response while the path is reused.
                        return copy.deepcopy(metadata)
                    if url == by_path:
                        return copy.deepcopy(path_metadata)
                    if url == f"{by_path}/commits/main":
                        return {"sha": sha}
                    self.fail(f"unexpected API URL: {url}")

                def fetch(url):
                    reads.append(url)
                    self.assertTrue(url.startswith(
                        f"https://raw.githubusercontent.com/{pin['repository']}/{sha}/",
                    ))
                    if url.endswith("/README.md"):
                        return "# Clash\nClash is a proxy kernel based on mihomo v1.19.32.\n"
                    if url.endswith("/UPSTREAM_VERSION"):
                        return "v1.19.32\n"
                    self.fail(f"unexpected evidence URL: {url}")

                record, issues, ok = d.audit_app_store_pinned_core_evidence(
                    client, {}, NOW, api, fetch, "fixture-token",
                )
                if mode == "matching":
                    self.assertTrue(ok, issues)
                    self.assertEqual(record["state"], "ok")
                    self.assertEqual(calls.count(by_path), 4, "each body read needs before/after path checks")
                else:
                    self.assertFalse(ok, f"accepted {mode} content path as pinned core evidence: {record}")
                    self.assertIn(by_path, calls)
                    self.assertEqual(reads, [], "untrusted content must not be read")
                    if mode == "reassigned":
                        self.assertEqual(record["state"], "identity_mismatch")
                        self.assertTrue(issues)
                    else:
                        self.assertEqual(record["observation_state"], "error")

    def test_core_path_identity_or_lifecycle_change_during_read_is_rejected(self):
        client = next(item for item in d.load_clients() if item["id"] == "hako")
        metadata = directory_fixtures.AuditTests.clash_core_metadata()
        pin = client["app_store_core_repo"]
        by_id = f"https://api.github.com/repositories/{pin['id']}"
        by_path = f"https://api.github.com/repos/{pin['repository']}"
        prior_stamp = d.iso(NOW - dt.timedelta(days=1))
        previous = d.positive_record(None, prior_stamp, d.core_evidence_scope(client), state="ok")
        for change in ({"id": 987654}, {"archived": True}, {"disabled": True}):
            with self.subTest(change=change):
                read_started = False
                reads = []

                def api(url, token=None):
                    if url == by_id:
                        return copy.deepcopy(metadata)
                    if url == by_path:
                        return {**copy.deepcopy(metadata), **(change if read_started else {})}
                    if url == f"{by_path}/commits/main":
                        return {"sha": "b" * 40}
                    self.fail(f"unexpected API URL: {url}")

                def fetch(url):
                    nonlocal read_started
                    read_started = True
                    reads.append(url)
                    return "# Clash\nClash is a proxy kernel based on mihomo v1.19.32.\n"

                record, issues, ok = d.audit_app_store_pinned_core_evidence(
                    client, {"core_evidence": previous}, NOW, api, fetch, None,
                )
                self.assertFalse(ok)
                self.assertEqual(record["state"], "identity_mismatch")
                self.assertEqual(record["last_success_at"], prior_stamp)
                self.assertEqual(len(reads), 1, "do not fetch further evidence after identity failure")
                self.assertTrue(issues)

    def test_transient_core_path_failure_keeps_old_evidence_without_new_success(self):
        client = next(item for item in d.load_clients() if item["id"] == "hako")
        metadata = directory_fixtures.AuditTests.clash_core_metadata()
        pin = client["app_store_core_repo"]
        prior_stamp = d.iso(NOW - dt.timedelta(days=1))
        previous = d.positive_record(
            None, prior_stamp, d.core_evidence_scope(client), state="ok",
            evidence_snapshots=[{"url": "https://example.invalid/previous", "sha256": "previous-digest"}],
        )
        for missing in (True, False):
            with self.subTest(missing=missing):
                def api(url, token=None):
                    if url == f"https://api.github.com/repositories/{pin['id']}":
                        return copy.deepcopy(metadata)
                    self.assertEqual(url, f"https://api.github.com/repos/{pin['repository']}")
                    if missing:
                        return None
                    raise OSError("temporary path lookup failure")

                record, _, ok = d.audit_app_store_pinned_core_evidence(
                    client, {"core_evidence": previous}, NOW, api,
                    lambda _url: self.fail("must not read after a failed path check"), None,
                )
                self.assertFalse(ok)
                self.assertEqual(record["state"], "ok")
                self.assertEqual(record["observation_state"], "error")
                self.assertEqual(record["last_success_at"], prior_stamp)
                self.assertEqual(record["evidence_snapshots"], previous["evidence_snapshots"])

    def test_identity_gate_rejects_unauthorized_coauthor_with_allowed_git_identities(self):
        fixture = ci_fixtures.CIBoundaryTests()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = fixture.init_fixture(directory)
            fixture.git(directory, "commit", "--allow-empty", "-m", "allowed coauthor control", "-m",
                        "Co-authored-by: NET86 <43442823+NET86@users.noreply.github.com>")
            head = fixture.git(directory, "rev-parse", "HEAD")
            control, _ = fixture.check_identity(directory, "push", {"before": base, "after": head})
            self.assertEqual(control.returncode, 0, control.stderr)
            fixture.git(directory, "commit", "--allow-empty", "-m", "unauthorized coauthor fixture", "-m",
                        "Co-authored-by: Other User <other@example.invalid>")
            head = fixture.git(directory, "rev-parse", "HEAD")
            result, _ = fixture.check_identity(directory, "push", {"before": base, "after": head})
            self.assertNotEqual(result.returncode, 0,
                                "allowed author/committer hid an unauthorized public coauthor trailer")

    def test_pr_parent_coauthor_is_checked_behind_allowed_tip_and_merge(self):
        fixture = ci_fixtures.CIBoundaryTests()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            base = fixture.init_fixture(directory)
            fixture.git(directory, "switch", "-c", "proposal")
            fixture.git(directory, "commit", "--allow-empty", "-m", "unauthorized coauthor fixture", "-m",
                        "Co-authored-by: Other User <other@example.invalid>")
            fixture.git(directory, "commit", "--allow-empty", "-m", "allowed tip")
            head = fixture.git(directory, "rev-parse", "HEAD")
            fixture.git(directory, "switch", "main")
            fixture.git(directory, "merge", "--no-ff", "proposal", "-m", "allowed merge")
            result, _ = fixture.check_identity(
                directory, "pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected commit coauthor identity", result.stderr)

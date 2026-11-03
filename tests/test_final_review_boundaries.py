"""Review-only probes; hostile data and Git histories remain disposable fixtures."""
import copy
from pathlib import Path
import tempfile
import unittest

import test_ci_boundaries as ci_fixtures
import test_directory as directory_fixtures

d = directory_fixtures.d
NOW = directory_fixtures.NOW


class FinalReviewBoundaryTests(unittest.TestCase):
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
                else:
                    self.assertFalse(ok, f"accepted {mode} content path as pinned core evidence: {record}")
                    self.assertIn(by_path, calls)
                    self.assertEqual(reads, [], "untrusted content must not be read")

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

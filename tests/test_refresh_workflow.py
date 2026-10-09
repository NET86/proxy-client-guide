"""Keep the periodic refresh efficient without weakening non-fast-forward safety."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RefreshWorkflowTests(unittest.TestCase):
    def test_queued_trigger_refreshes_latest_main_and_fences_by_checked_out_sha(self):
        workflow = (ROOT / ".github/workflows/refresh-directory.yml").read_text(encoding="utf-8")
        self.assertIn("ref: main", workflow)
        self.assertIn('checked_out="$(git rev-parse HEAD)"', workflow)
        self.assertIn('if [[ "$remote_head" != "$checked_out" ]]; then', workflow)
        self.assertIn('echo "base_sha=$checked_out" >> "$GITHUB_OUTPUT"', workflow)
        self.assertEqual(
            workflow.count('"$remote_head" != "${{ steps.freshness.outputs.base_sha }}"'),
            2,
        )
        self.assertNotIn("GITHUB_SHA", workflow)
        self.assertIn("git push origin HEAD:main", workflow)
        self.assertNotIn("--force", workflow)

    def test_full_ci_discovers_focused_response_tests_without_running_them_twice(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("python3 -m unittest discover -s tests -v"), 1)
        self.assertNotIn("test_directory.ResponseLimitTests", workflow)
        self.assertNotIn("test_response_isolation.ResponseIsolationTests", workflow)


if __name__ == "__main__":
    unittest.main()

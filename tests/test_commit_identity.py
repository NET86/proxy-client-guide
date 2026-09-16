import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_commit_identity", ROOT / "scripts/check_commit_identity.py")
assert SPEC and SPEC.loader
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)


class CommitIdentityTests(unittest.TestCase):
    def test_net86_identity_is_allowed(self):
        c.validate_identity(
            ("NET86", "43442823+NET86@users.noreply.github.com"),
            ("NET86", "43442823+NET86@users.noreply.github.com"),
        )

    def test_github_merge_identity_is_allowed(self):
        c.validate_identity(
            ("NET86", "43442823+NET86@users.noreply.github.com"),
            ("GitHub", "noreply@github.com"),
        )

    def test_github_actions_identity_is_allowed(self):
        identity = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")
        c.validate_identity(identity, identity)

    def test_unexpected_author_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected commit author identity"):
            c.validate_identity(
                ("Other User", "other@example.invalid"),
                ("GitHub", "noreply@github.com"),
            )

    def test_unexpected_committer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected commit committer identity"):
            c.validate_identity(
                ("NET86", "43442823+NET86@users.noreply.github.com"),
                ("Other User", "other@example.invalid"),
            )


if __name__ == "__main__":
    unittest.main()

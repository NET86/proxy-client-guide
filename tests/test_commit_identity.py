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

    def test_allowed_coauthors_accept_case_variants_repetition_and_folding(self):
        for trailers in (
            "Co-authored-by: NET86 <43442823+NET86@users.noreply.github.com>",
            "Co-Authored-By: NET86 <43442823+NET86@users.noreply.github.com>\n"
            "co-authored-by: github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>",
            "Co-authored-by: NET86\n <43442823+NET86@users.noreply.github.com>",
        ):
            with self.subTest(trailers=trailers):
                c.validate_coauthors("Subject\n\n" + trailers + "\n")

    def test_unauthorized_coauthor_cannot_hide_behind_other_allowed_trailers(self):
        for trailers in (
            "Co-authored-by: Other User <other@example.invalid>",
            "Co-authored-by: NET86 <43442823+NET86@users.noreply.github.com>\n"
            "Co-Authored-By: Other User <other@example.invalid>",
            "Co-authored-by: Other User\n <other@example.invalid>",
            "Co-authored-by: NET86 <other@example.invalid>",
        ):
            with self.subTest(trailers=trailers):
                with self.assertRaisesRegex(ValueError, "unexpected commit coauthor identity"):
                    c.validate_coauthors("Subject\n\n" + trailers + "\n")

    def test_malformed_coauthor_trailers_are_rejected(self):
        for value in ("", "NET86", "NET86 <>", "NET86 <first@example.invalid> <second@example.invalid>"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "malformed commit coauthor identity"):
                    c.validate_coauthors("Subject\n\nCo-authored-by: " + value + "\n")

    def test_commit_message_divider_does_not_hide_unauthorized_coauthor(self):
        with self.assertRaisesRegex(ValueError, "unexpected commit coauthor identity"):
            c.validate_coauthors("Subject\n\n---\n\nCo-authored-by: Other User <other@example.invalid>\n")

    def test_messages_without_coauthor_trailers_remain_allowed(self):
        for message in ("Subject\n", "Subject\n\nExplain how Co-authored-by metadata works.\n",
                        "Subject\n\nRefs: #26\n"):
            with self.subTest(message=message):
                c.validate_coauthors(message)


if __name__ == "__main__":
    unittest.main()

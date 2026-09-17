"""Exercise the workflow's ordinary push against a real, local Git remote."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("git"), "git is required for the publication race fixture")
class PublishRaceTests(unittest.TestCase):
    def test_stale_bot_cannot_overwrite_human_and_next_fresh_run_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote, human, bot, fresh = (root / name for name in ("remote.git", "human", "bot", "fresh"))
            env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CONFIG_COUNT="0", GIT_TERMINAL_PROMPT="0")

            def git(cwd, *args, success=True):
                command = ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *args]
                result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=20,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if success:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0, "stale push unexpectedly succeeded")
                return result.stdout.strip()

            git(root, "init", "--bare", "--initial-branch=main", str(remote))
            git(root, "clone", str(remote), str(human))
            (human / "clients.json").write_text("original catalog\n", encoding="utf-8")
            (human / "README.md").write_text("initial generated output\n", encoding="utf-8")
            git(human, "add", "clients.json", "README.md")
            git(human, "commit", "-m", "initial")
            git(human, "push", "origin", "HEAD:main")
            trigger_sha = git(human, "rev-parse", "HEAD")

            git(root, "clone", str(remote), str(bot))
            git(bot, "checkout", "--detach", trigger_sha)
            (human / "clients.json").write_text("human catalog correction\n", encoding="utf-8")
            (human / "README.md").write_text("new human-generated output\n", encoding="utf-8")
            git(human, "add", "clients.json", "README.md")
            git(human, "commit", "-m", "human update wins")
            git(human, "push", "origin", "HEAD:main")
            human_sha = git(human, "rev-parse", "HEAD")

            (bot / "README.md").write_text("stale bot output\n", encoding="utf-8")
            git(bot, "add", "README.md")
            git(bot, "commit", "-m", "stale refresh")
            git(bot, "push", "origin", "HEAD:main", success=False)
            self.assertEqual(git(root, "--git-dir", str(remote), "rev-parse", "main"), human_sha)
            self.assertEqual(git(root, "--git-dir", str(remote), "show", "main:clients.json"), "human catalog correction")
            self.assertEqual(git(root, "--git-dir", str(remote), "show", "main:README.md"), "new human-generated output")

            # The next run checks out the new main. No retry, rebase or force is needed.
            git(root, "clone", str(remote), str(fresh))
            git(fresh, "checkout", "--detach", human_sha)
            (fresh / "README.md").write_text("fresh generated output\n", encoding="utf-8")
            git(fresh, "add", "README.md")
            git(fresh, "commit", "-m", "next scheduled refresh")
            git(fresh, "push", "origin", "HEAD:main")
            self.assertEqual(git(root, "--git-dir", str(remote), "show", "main:clients.json"), "human catalog correction")
            self.assertEqual(git(root, "--git-dir", str(remote), "show", "main:README.md"), "fresh generated output")
            self.assertEqual(git(fresh, "diff", "--name-only", "HEAD^", "HEAD"), "README.md")


if __name__ == "__main__":
    unittest.main()

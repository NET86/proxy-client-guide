import copy
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_readme as d

NOW = dt.datetime(2026, 9, 28, 6, 0, tzinfo=dt.timezone.utc)


class GithubRediscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clients = d.load_clients(ROOT / "data" / "clients.json")
        cls.by_id = {client["id"]: client for client in cls.clients}

    @staticmethod
    def release(version="v2", rid=9000):
        return {
            "id": rid,
            "tag_name": version,
            "published_at": "2026-09-28T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "id": rid * 10 + 1,
                    "name": "client.bin",
                    "size": 100,
                    "state": "uploaded",
                }
            ],
        }

    @staticmethod
    def repo(repo_id, owner_id, full_name, description):
        return {
            "id": repo_id,
            "name": full_name.split("/", 1)[1],
            "full_name": full_name,
            "description": description,
            "homepage": "",
            "topics": [],
            "owner": {"id": owner_id},
            "archived": False,
            "disabled": False,
            "pushed_at": "2026-09-28T00:00:00Z",
        }

    def test_catalog_enables_discovery_for_selected_projects(self):
        expected = {
            "clashmi": ("https://clashmi.app/page/contact", 131734194, r"\bclashmi\b"),
            "karing": ("https://karing.app/faq", 131734194, r"\bkaring\b"),
            "hiddify": (
                "https://hiddify.com/app/How-to-contribute-to-this-project/",
                126981719,
                r"\bhiddify\b",
            ),
            "sing-box": (
                "https://sing-box.sagernet.org/support/",
                83217677,
                r"\bsing-box\b",
            ),
        }
        for client_id, (url, owner_id, pattern) in expected.items():
            with self.subTest(client=client_id):
                config = self.by_id[client_id]["github_discovery"]
                self.assertEqual(config["trusted_text_urls"], [url])
                self.assertEqual(config["trusted_owner_ids"], [owner_id])
                self.assertEqual(config["repo_patterns"], [pattern])

    def test_same_path_recreated_repo_is_recovered_only_after_official_confirmation(self):
        client = self.by_id["karing"]
        new_repo_id = 990001

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return self.repo(new_repo_id, 131734194, client["github_repo"], "Karing proxy utility")
            if url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                return None
            if url == f"https://api.github.com/repos/{client['github_repo']}/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url == "https://karing.app/faq":
                return f"Official GitHub: https://github.com/{client['github_repo']}/releases"
            if "raw.githubusercontent.com/KaringX/karing/" in url:
                return "Built-in modified sing-box core"
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["source"]["repo_id"], new_repo_id)
        self.assertTrue(out["source"]["rediscovered_repo"])
        self.assertEqual(out["github_discovery"]["state"], "verified")

    def test_new_path_same_trusted_owner_is_recovered_and_links_follow_it(self):
        client = self.by_id["karing"]
        new_full_name = "KaringX/karing-next"
        new_repo_id = 990002

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return None
            if url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                return None
            if url == f"https://api.github.com/repos/{new_full_name}":
                return self.repo(new_repo_id, 131734194, new_full_name, "Karing next-generation proxy utility")
            if url == f"https://api.github.com/repos/{new_full_name}/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url == "https://karing.app/faq":
                return f"Official download: https://github.com/{new_full_name}/releases"
            if f"raw.githubusercontent.com/{new_full_name}/" in url:
                return "Built-in modified sing-box core"
            if "raw.githubusercontent.com/KaringX/karing/" in url:
                self.fail("core evidence must follow the rediscovered canonical repository")
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["full_name"], new_full_name)
        self.assertTrue(out["source"]["rediscovered_repo"])
        self.assertEqual(
            d.links_for(client, out, NOW),
            (
                f"https://github.com/{new_full_name}",
                f"https://github.com/{new_full_name}/releases",
            ),
        )

    def test_path_squatter_cannot_override_still_existing_pinned_repository(self):
        client = self.by_id["karing"]
        canonical_name = "KaringX/karing-renamed"

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return self.repo(880001, 999999, client["github_repo"], "Karing lookalike")
            if url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                return self.repo(client["official_repo_id"], 131734194, canonical_name, "Karing proxy utility")
            if url == f"https://api.github.com/repos/{canonical_name}":
                return self.repo(client["official_repo_id"], 131734194, canonical_name, "Karing proxy utility")
            if url == f"https://api.github.com/repos/{canonical_name}/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if f"raw.githubusercontent.com/{canonical_name}/" in url:
                return "Built-in modified sing-box core"
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["repo_id"], client["official_repo_id"])
        self.assertEqual(out["source"]["full_name"], canonical_name)
        self.assertFalse(out["source"]["rediscovered_repo"])
        self.assertNotIn("github_discovery", out)

    def test_new_owner_with_only_one_official_domain_is_not_auto_accepted(self):
        client = copy.deepcopy(self.by_id["karing"])
        new_full_name = "NewKaringOrg/karing"

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url in {
                f"https://api.github.com/repos/{client['github_repo']}",
                f"https://api.github.com/repositories/{client['official_repo_id']}",
            }:
                return None
            if url == f"https://api.github.com/repos/{new_full_name}":
                return self.repo(990003, 777001, new_full_name, "Karing proxy utility")
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url == "https://karing.app/faq":
                return f"Official GitHub: https://github.com/{new_full_name}"
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertFalse(ok)
        self.assertTrue(issues)
        self.assertEqual(out["source"]["state"], "missing")
        self.assertEqual(out["github_discovery"]["state"], "candidate")
        self.assertEqual(
            out["github_discovery"]["candidates"][0]["reason"],
            "single_source_new_owner",
        )

    def test_two_pages_on_same_domain_do_not_fake_two_independent_sources(self):
        client = copy.deepcopy(self.by_id["karing"])
        client["github_discovery"] = {
            "trusted_text_urls": [
                "https://karing.app/page-one",
                "https://karing.app/page-two",
            ],
            "trusted_owner_ids": [],
            "repo_patterns": [r"\bkaring\b"],
        }
        new_full_name = "NewKaringOrg/karing"

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url in {
                f"https://api.github.com/repos/{client['github_repo']}",
                f"https://api.github.com/repositories/{client['official_repo_id']}",
            }:
                return None
            if url == f"https://api.github.com/repos/{new_full_name}":
                return self.repo(990004, 777002, new_full_name, "Karing proxy utility")
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url.startswith("https://karing.app/"):
                return f"Source: https://github.com/{new_full_name}"
            self.fail(f"unexpected text URL: {url}")

        out, _, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertFalse(ok)
        discovery = out["github_discovery"]
        self.assertEqual(discovery["state"], "candidate")
        candidate = discovery["candidates"][0]
        self.assertEqual(candidate["source_identities"], ["karing.app"])
        self.assertEqual(candidate["reason"], "single_source_new_owner")

    def test_two_independent_official_domains_can_confirm_new_owner(self):
        client = copy.deepcopy(self.by_id["karing"])
        client["github_discovery"] = {
            "trusted_text_urls": [
                "https://official.example/source",
                "https://store.example/source",
            ],
            "trusted_owner_ids": [],
            "repo_patterns": [r"\bkaring\b"],
        }
        new_full_name = "NewKaringOrg/karing"

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url in {
                f"https://api.github.com/repos/{client['github_repo']}",
                f"https://api.github.com/repositories/{client['official_repo_id']}",
            }:
                return None
            if url == f"https://api.github.com/repos/{new_full_name}":
                return self.repo(990005, 777003, new_full_name, "Karing proxy utility")
            if url == f"https://api.github.com/repos/{new_full_name}/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url in {"https://official.example/source", "https://store.example/source"}:
                return f"Source: https://github.com/{new_full_name}"
            if f"raw.githubusercontent.com/{new_full_name}/" in url:
                return "Built-in modified sing-box core"
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["github_discovery"]["state"], "verified")
        self.assertEqual(
            out["github_discovery"]["source_identities"],
            ["official.example", "store.example"],
        )
        self.assertEqual(out["source"]["full_name"], new_full_name)

    def test_multiple_matching_trusted_owner_candidates_stay_ambiguous(self):
        client = copy.deepcopy(self.by_id["karing"])
        first = "KaringX/karing-next"
        second = "KaringX/karing-tools"

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url in {
                f"https://api.github.com/repos/{client['github_repo']}",
                f"https://api.github.com/repositories/{client['official_repo_id']}",
            }:
                return None
            if url == f"https://api.github.com/repos/{first}":
                return self.repo(990006, 131734194, first, "Karing proxy utility")
            if url == f"https://api.github.com/repos/{second}":
                return self.repo(990007, 131734194, second, "Karing support utility")
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url == "https://karing.app/faq":
                return (
                    f"Primary: https://github.com/{first} "
                    f"Tools: https://github.com/{second}"
                )
            self.fail(f"unexpected text URL: {url}")

        out, _, ok = d.audit_github(client, {}, "token", NOW, api, fetch_text)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "missing")
        self.assertEqual(out["github_discovery"]["state"], "candidate")
        self.assertEqual(
            out["github_discovery"]["reason"],
            "ambiguous_verified_candidates",
        )
        self.assertEqual(len(out["github_discovery"]["candidates"]), 2)

    def alias_discovery(self, urls, metadata, old=None):
        client = copy.deepcopy(self.by_id["karing"])
        client["github_discovery"]["trusted_text_urls"] = urls
        client["github_discovery"]["trusted_owner_ids"] = []
        names = ("NewKaringOrg/karing-old", "NewKaringOrg/karing-next")

        def text(url):
            return f"https://github.com/{names[urls.index(url)]}"

        def api(url, token=None):
            name = url.removeprefix("https://api.github.com/repos/")
            return copy.deepcopy(metadata[names.index(name)])

        previous = {}
        if old:
            previous["github_discovery"] = d.positive_record(
                None, d.iso(NOW - dt.timedelta(hours=1)), d.github_discovery_scope(client),
                **old["github_discovery"],
            )
        return d.audit_github_discovery(client, previous, NOW, None, None, api, text)

    def test_aliases_merge_two_independent_domains_before_new_owner_trust_check(self):
        metadata = self.repo(990020, 777003, "NewKaringOrg/karing-next", "Karing proxy utility")
        record = self.alias_discovery(
            ["https://official.example/source", "https://store.example/source"],
            [metadata, metadata],
        )
        self.assertEqual(record["state"], "verified", record)
        self.assertEqual(record["repo_id"], 990020)
        self.assertEqual(record["source_identities"], ["official.example", "store.example"])
        self.assertEqual(record["sources"], ["https://official.example/source", "https://store.example/source"])

    def test_aliases_on_one_domain_do_not_create_independent_trust(self):
        metadata = self.repo(990020, 777003, "NewKaringOrg/karing-next", "Karing proxy utility")
        record = self.alias_discovery(
            ["https://official.example/one", "https://official.example/two"],
            [metadata, metadata],
        )
        self.assertEqual(record["state"], "candidate")
        self.assertEqual(len(record["candidates"]), 1)
        self.assertEqual(record["candidates"][0]["reason"], "single_source_new_owner")

    def test_sources_for_distinct_ids_are_never_merged_even_with_same_name(self):
        first = self.repo(990020, 777003, "NewKaringOrg/karing-next", "Karing proxy utility")
        second = {**copy.deepcopy(first), "id": 990021}
        record = self.alias_discovery(
            ["https://official.example/source", "https://store.example/source"],
            [first, second],
        )
        self.assertEqual(record["state"], "candidate")
        self.assertEqual(len(record["candidates"]), 2)
        self.assertTrue(all(candidate["reason"] == "single_source_new_owner" for candidate in record["candidates"]))

    def test_conflicting_alias_metadata_cannot_reuse_cached_verified_claim(self):
        first = self.repo(990020, 777003, "NewKaringOrg/karing-next", "Karing proxy utility")
        old = {"github_discovery": {"state": "verified", "repo_id": 990020, "full_name": first["full_name"]}}
        for field, value in (("archived", True), ("owner", {"id": 777004}),
                             ("description", "Unrelated utility")):
            with self.subTest(field=field):
                second = {**copy.deepcopy(first), field: value}
                record = self.alias_discovery(
                    ["https://official.example/source", "https://store.example/source"],
                    [first, second], old,
                )
                self.assertEqual(record["state"], "candidate")
                self.assertEqual(record["reason"], "repository_changed_during_alias_resolution")

    def test_original_repository_recovery_clears_stale_discovery(self):
        client = self.by_id["karing"]
        old = {
            "github_discovery": {
                "scope": d.github_discovery_scope(client),
                "state": "candidate",
                "reason": "insufficient_identity_evidence",
                "candidates": [],
                "observation_state": "fresh",
                "consecutive_failures": 0,
                "last_success_at": d.iso(NOW),
                "observed_at": d.iso(NOW),
            }
        }

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return self.repo(
                    client["official_repo_id"],
                    131734194,
                    client["github_repo"],
                    "Karing proxy utility",
                )
            if url == f"https://api.github.com/repos/{client['github_repo']}/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if "raw.githubusercontent.com/KaringX/karing/" in url:
                return "Built-in modified sing-box core"
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, old, "token", NOW, api, fetch_text)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertNotIn("github_discovery", out)
        self.assertFalse(out["source"]["rediscovered_repo"])

    def test_unconfigured_github_client_has_no_rediscovery_marker(self):
        client = self.by_id["flclash"]

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return self.repo(
                    client["official_repo_id"],
                    181477152,
                    client["github_repo"],
                    "FlClash Mihomo client",
                )
            if url == f"https://api.github.com/repos/{client['github_repo']}/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")

        out, issues, ok = d.audit_github(
            client,
            {},
            "token",
            NOW,
            api,
            lambda _url: "github.com/metacubex/mihomo/",
        )
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertNotIn("rediscovered_repo", out["source"])
        self.assertNotIn("github_discovery", out)

    def test_rediscovered_repo_does_not_inherit_old_repo_release_identity(self):
        client = self.by_id["karing"]
        new_full_name = "KaringX/karing-next"
        new_repo_id = 990008
        prior = NOW - dt.timedelta(days=1)
        old = {
            "source": d.positive_record(
                None,
                d.iso(prior),
                d.source_scope(client),
                state="ok",
                repo_id=client["official_repo_id"],
                owner_id=131734194,
                full_name=client["github_repo"],
                last_activity_at=d.iso(prior),
            ),
            "release": d.positive_record(
                None,
                d.iso(prior),
                d.release_scope(client),
                state="ok",
                version="v2",
                published_at="2026-09-27T00:00:00Z",
                release_id=111,
                asset_count=1,
            ),
        }

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url in {
                f"https://api.github.com/repos/{client['github_repo']}",
                f"https://api.github.com/repositories/{client['official_repo_id']}",
            }:
                return None
            if url == f"https://api.github.com/repos/{new_full_name}":
                return self.repo(new_repo_id, 131734194, new_full_name, "Karing proxy utility")
            if url == f"https://api.github.com/repos/{new_full_name}/releases/latest":
                return self.release(version="v2", rid=9008)
            self.fail(f"unexpected API URL: {url}")

        def fetch_text(url):
            if url == "https://karing.app/faq":
                return f"Official download: https://github.com/{new_full_name}/releases"
            if f"raw.githubusercontent.com/{new_full_name}/" in url:
                return "Built-in modified sing-box core"
            self.fail(f"unexpected text URL: {url}")

        out, issues, ok = d.audit_github(client, old, "token", NOW, api, fetch_text)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["release"]["state"], "ok")
        self.assertEqual(out["release"]["repo_id"], new_repo_id)
        self.assertEqual(out["release"]["release_id"], 9008)

    def test_stale_last_known_discovery_is_not_used_for_recovery(self):
        client = self.by_id["karing"]
        old_success = NOW - dt.timedelta(days=d.FRESH_DAYS + 1)
        old = {
            "github_discovery": {
                "scope": d.github_discovery_scope(client),
                "state": "verified",
                "repo_id": 990006,
                "owner_id": 131734194,
                "full_name": "KaringX/karing-next",
                "sources": ["https://karing.app/faq"],
                "source_identities": ["karing.app"],
                "observation_state": "fresh",
                "consecutive_failures": 0,
                "last_success_at": d.iso(old_success),
                "observed_at": d.iso(old_success),
            }
        }

        def api(url, token=None):
            if "/commits/" in url:
                return {"sha": "a" * 40}
            if url in {
                f"https://api.github.com/repos/{client['github_repo']}",
                f"https://api.github.com/repositories/{client['official_repo_id']}",
            }:
                return None
            self.fail(f"stale candidate must not be queried: {url}")

        def fetch_text(_url):
            raise OSError("official anchor unavailable")

        out, _, ok = d.audit_github(client, old, "token", NOW, api, fetch_text)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "missing")
        self.assertEqual(out["github_discovery"]["observation_state"], "error")


if __name__ == "__main__":
    unittest.main()

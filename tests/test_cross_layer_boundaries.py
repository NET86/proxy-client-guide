"""Cross-layer checks for observation safety and rendering."""

import copy
import datetime as dt
import json
import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_readme as d

NOW = dt.datetime(2026, 9, 15, 12, tzinfo=dt.timezone.utc)
CLIENT = {
    "id": "sample",
    "name": "Sample",
    "category": "mihomo",
    "core": "Mihomo",
    "platforms": {"windows": True, "macos": False, "ios": False, "tvos": False, "android": False, "linux": False},
    "source_type": "github",
    "github_repo": "example/client",
    "official_repo_id": 101,
    "download_url": "https://github.com/example/client/releases",
}


def api(url, token=None):
    if "/releases/latest" in url:
        return {
            "id": 303,
            "tag_name": "v1",
            "published_at": "2026-09-15T10:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [{"id": 404, "name": "client.bin", "size": 100, "state": "uploaded"}],
        }
    return {
        "id": 101,
        "full_name": "example/client",
        "owner": {"id": 202},
        "archived": False,
        "disabled": False,
        "pushed_at": "2026-09-15T10:00:00Z",
    }


class CrossLayerBoundaryTests(unittest.TestCase):
    def test_total_outage_retains_last_good_state_but_fails_health(self):
        clients = [dict(CLIENT, id=f"client{i}", name=f"Client {i}") for i in range(20)]
        good = d.audit(clients, d.empty_observations(), api, lambda u: "", NOW)
        self.assertEqual(good["health"]["coverage"], 1.0)

        def down(*args):
            raise OSError("correlated upstream outage")

        bad = d.audit(clients, good, down, down, NOW + dt.timedelta(hours=1))
        self.assertEqual(bad["health"]["coverage"], 0.0)
        self.assertEqual(bad["health"]["succeeded_last_run"], 0)
        with self.assertRaisesRegex(ValueError, "coverage"):
            d.health_check(clients, bad, NOW + dt.timedelta(hours=1))
        for client in clients:
            old = good["clients"][client["id"]]["source"]
            source = bad["clients"][client["id"]]["source"]
            self.assertEqual(source["last_success_at"], old["last_success_at"])
            self.assertEqual(source["state"], "ok")

    def test_scope_change_cannot_reuse_old_version_or_download(self):
        good = d.audit([CLIENT], d.empty_observations(), api, lambda u: "", NOW)
        changed = dict(CLIENT, official_repo_id=999)
        good["clients"][CLIENT["id"]]["release"]["version"] = "OLD_SCOPE_VERSION_MUST_NOT_RENDER"
        rendered = d.render_readme([changed], good, NOW)
        self.assertNotIn("OLD_SCOPE_VERSION_MUST_NOT_RENDER", rendered)
        self.assertEqual(d.links_for(changed, good["clients"][CLIENT["id"]], NOW)[1], "")

    def test_bad_state_remains_visible_after_transport_or_region_failure(self):
        scope = d.source_scope(CLIENT)
        stamp = d.iso(NOW)
        bad = d.negative_record(None, stamp, scope, "identity_mismatch")
        variants = [
            d.failure_record(bad, stamp, "timeout", scope),
            d.unverified_record(bad, stamp, scope, "region_missing"),
        ]
        for component in variants:
            self.assertEqual(component["state"], "identity_mismatch")
            warning = d.component_warning("项目来源", component)
            self.assertNotIn("identity_mismatch", warning)
            self.assertRegex(warning, r"(既有异常未恢复|未确认恢复)")
            self.assertIn("下载入口保持隐藏", warning)
            self.assertNotIn("保留已配置入口", warning)

    def test_path_takeover_stays_blocked_through_404_and_timeout_until_identity_recovers(self):
        good = d.audit([CLIENT], d.empty_observations(), api, lambda u: "", NOW)
        good["clients"]["sample"]["source"]["last_success_at"] = "2026-09-01T00:00:00Z"
        bad = d.audit([CLIENT], good, lambda u, t=None: dict(api(u), id=999), lambda u: "", NOW)
        for request in (lambda *a: None, lambda *a: (_ for _ in ()).throw(OSError("timeout"))):
            bad = d.audit([CLIENT], bad, request, lambda u: "", NOW)
            source = bad["clients"]["sample"]["source"]
            self.assertEqual(source["state"], "identity_mismatch")
            self.assertEqual(source["observed_repo_id"], 999)
            self.assertEqual(d.links_for(CLIENT, bad["clients"]["sample"], NOW), ("", ""))
            with self.assertRaises(ValueError):
                d.health_check([CLIENT], bad, NOW)
        recovered = d.audit([CLIENT], bad, api, lambda u: "", NOW)
        self.assertEqual(d.links_for(CLIENT, recovered["clients"]["sample"], NOW)[1], CLIENT["download_url"])
        self.assertNotIn("observed_state", recovered["clients"]["sample"]["source"])
        d.health_check([CLIENT], recovered, NOW)

    def test_release_recreation_cannot_hide_behind_rollback_or_later_absence(self):
        good = d.audit([CLIENT], d.empty_observations(), api, lambda u: "", NOW)
        for published in ("2026-09-15T09:00:00Z", "2026-09-15T10:00:00Z", "2026-09-15T11:00:00Z"):
            with self.subTest(published=published):
                def recreated(url, token=None):
                    payload = api(url)
                    return dict(payload, id=999, published_at=published) if "/releases/" in url else payload
                bad = d.audit([CLIENT], good, recreated, lambda u: "", NOW)
                self.assertEqual(bad["clients"]["sample"]["release"]["state"], "identity_mismatch")
                for release_value in (None, dict(api("/releases/latest"), tag_name="v0", published_at="2026-09-14T10:00:00Z")):
                    bad = d.audit([CLIENT], bad, lambda u, t=None: release_value if "/releases/" in u else api(u), lambda u: "", NOW)
                    self.assertEqual(bad["clients"]["sample"]["release"]["state"], "identity_mismatch")
                    self.assertEqual(d.links_for(CLIENT, bad["clients"]["sample"], NOW)[1], "")
                recovered = d.audit([CLIENT], bad, api, lambda u: "", NOW)
                d.health_check([CLIENT], recovered, NOW)
                self.assertEqual(d.links_for(CLIENT, recovered["clients"]["sample"], NOW)[1], CLIENT["download_url"])

    def test_historical_conflicts_survive_missing_release_and_recover_only_on_matching_pins(self):
        client = dict(CLIENT, category="legacy", download_url=CLIENT["download_url"] + "/tag/v1",
                      historical_release={"tag": "v1", "release_id": 303, "assets": [{"id": 404, "name": "client.bin", "size": 100}]})
        release = api("/releases/latest")
        def healthy(url, token=None):
            return copy.deepcopy(release) if "/releases/" in url else api(url)
        good = d.audit([client], d.empty_observations(), healthy, lambda u: "", NOW)
        for state in ("identity_mismatch", "asset_mismatch"):
            with self.subTest(state=state):
                changed = copy.deepcopy(release)
                if state == "identity_mismatch":
                    changed["id"] = 999
                else:
                    changed["assets"][0]["id"] = 999
                bad = d.audit([client], good, lambda u, t=None: changed if "/releases/" in u else api(u), lambda u: "", NOW)
                bad = d.audit([client], bad, lambda u, t=None: None if "/releases/" in u else api(u), lambda u: "", NOW)
                self.assertEqual(bad["clients"]["sample"]["historical_release"]["state"], state)
                self.assertEqual(d.links_for(client, bad["clients"]["sample"], NOW)[1], "")
                with self.assertRaises(ValueError):
                    d.health_check([client], bad, NOW)
                recovered = d.audit([client], bad, healthy, lambda u: "", NOW)
                d.health_check([client], recovered, NOW)
                self.assertEqual(d.links_for(client, recovered["clients"]["sample"], NOW)[1], client["download_url"])

    def test_archived_download_identity_conflict_is_visible_even_above_95_percent_coverage(self):
        good = d.audit([CLIENT], d.empty_observations(), api, lambda u: "", NOW)
        def archived_conflict(url, token=None):
            value = api(url)
            return dict(value, id=999) if "/releases/" in url else dict(value, archived=True)
        bad = d.audit([CLIENT], good, archived_conflict, lambda u: "", NOW)
        clients = [dict(CLIENT, id=f"c{i}", name=f"C{i}") for i in range(24)]
        observations = {"clients": {c["id"]: copy.deepcopy(good["clients"]["sample"]) for c in clients}}
        observations["clients"]["c0"] = bad["clients"]["sample"]
        health = d.derive_health(clients, observations, NOW)
        self.assertGreater(health["coverage"], 0.95)
        self.assertTrue(any("identity_mismatch" in value for value in health["anomalies"]))
        with self.assertRaises(ValueError):
            d.health_check(clients, observations, NOW)
        rendered = d.render_readme([CLIENT], bad, NOW)
        self.assertIn("下载入口已隐藏", rendered)
        self.assertNotIn("- 核验：无异常。", rendered)

    def test_unarchive_rechecks_optional_release_and_core_without_changing_catalog(self):
        client = dict(CLIENT, core_evidence=[{"url": "https://raw.githubusercontent.com/example/client/main/README.md", "patterns": ["Mihomo"]}])
        before = copy.deepcopy(client)
        good = d.audit([client], d.empty_observations(), api, lambda u: "Mihomo", NOW)
        def missing(url, token=None, archived=True):
            return None if "/releases/" in url else dict(api(url), archived=archived)
        archived = d.audit([client], good, missing, lambda u: "unrelated", NOW)
        d.health_check([client], archived, NOW)
        self.assertEqual(d.effective_category(client, archived["clients"]["sample"]), "legacy")
        active = d.audit([client], archived, lambda u, t=None: missing(u, archived=False), lambda u: "unrelated", NOW)
        self.assertEqual(d.effective_category(client, active["clients"]["sample"]), "mihomo")
        self.assertEqual(d.activity_status(client, active["clients"]["sample"], NOW)[0], "❓")
        self.assertTrue(any("core_evidence" in v for v in active["health"]["anomalies"]))
        self.assertTrue(any("release" in v for v in active["health"]["anomalies"]))
        recovered = d.audit([client], active, api, lambda u: "Mihomo", NOW)
        d.health_check([client], recovered, NOW)
        self.assertEqual(d.activity_status(client, recovered["clients"]["sample"], NOW)[0], "🟢")
        self.assertEqual(client, before)

    def test_canonical_transfer_follows_release_raw_and_wiki_but_not_other_repo(self):
        urls = ["https://raw.githubusercontent.com/example/client/main/README.md",
                "https://raw.githubusercontent.com/wiki/example/client/Guide.md",
                "https://raw.githubusercontent.com/example/kernel/main/README.md"]
        client = dict(CLIENT, core_evidence=[{"url": url, "patterns": ["Mihomo"]} for url in urls])
        seen = []
        def transferred(url, token=None):
            seen.append(url)
            return api(url) if "/releases/" in url else dict(api(url), full_name="new-owner/renamed", owner={"id": 999})
        out = d.audit([client], d.empty_observations(), transferred, lambda u: (seen.append(u) or "Mihomo"), NOW)
        d.health_check([client], out, NOW)
        self.assertIn("https://api.github.com/repos/new-owner/renamed/releases/latest", seen)
        self.assertIn("https://raw.githubusercontent.com/new-owner/renamed/main/README.md", seen)
        self.assertIn("https://raw.githubusercontent.com/wiki/new-owner/renamed/Guide.md", seen)
        self.assertIn(urls[2], seen)
        record = out["clients"]["sample"]
        self.assertEqual(d.links_for(client, record, NOW), ("https://github.com/new-owner/renamed", "https://github.com/new-owner/renamed/releases"))
        self.assertTrue(all(d.component_is_scoped(client, record, name) for name in d.expected_observation_components(client)))
        self.assertEqual(client["core_evidence"][1]["url"], urls[1])

    def test_download_page_change_cannot_inherit_release_or_historical_authorization(self):
        client = dict(CLIENT, download_page_url="https://example.org/download")
        good = d.audit([client], d.empty_observations(), api, lambda u: "", NOW)
        for target in ("https://example.org/new-download", None):
            with self.subTest(target=target):
                changed = dict(client)
                if target is None:
                    changed.pop("download_page_url")
                else:
                    changed["download_page_url"] = target
                self.assertNotEqual(d.release_scope(client), d.release_scope(changed))
                self.assertEqual(d.links_for(changed, good["clients"]["sample"], NOW)[1], "")
                refreshed = d.audit([changed], good, api, lambda u: "", NOW)
                d.health_check([changed], refreshed, NOW)
                self.assertEqual(d.links_for(changed, refreshed["clients"]["sample"], NOW)[1], target or client["download_url"])
                history = {"tag": "v1", "release_id": 303, "assets": []}
                self.assertNotEqual(d.historical_release_scope(dict(client, historical_release=history)),
                                    d.historical_release_scope(dict(changed, historical_release=history)))
        self.assertNotEqual(d.release_scope(client), d.release_scope(dict(client, download_url=client["download_url"] + "/tag/v1")))
        self.assertNotEqual(d.release_scope(CLIENT), d.release_scope(client))

    def test_catalog_rejects_non_https_or_relative_download_page(self):
        payload = json.loads((ROOT / "data/clients.json").read_text(encoding="utf-8"))
        stash = next(c for c in payload["clients"] if c["id"] == "stash")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "clients.json"
            for url in ("javascript:alert(1)", "http://stash.ws/download", "/download", "https:///missing-host"):
                with self.subTest(url=url):
                    stash["download_page_url"] = url
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "download_page_url"):
                        d.load_clients(path)


if __name__ == "__main__":
    unittest.main()

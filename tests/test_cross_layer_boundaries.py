"""Cross-layer checks for observation safety and rendering."""

import datetime as dt
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
    "official_owner_id": 202,
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


if __name__ == "__main__":
    unittest.main()

import copy
import datetime as dt
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_readme as d

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)
STAMP = d.iso(NOW)


class AdversarialRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clients = d.load_clients(ROOT / "data" / "clients.json")
        cls.by_id = {client["id"]: client for client in cls.clients}

    @staticmethod
    def repo_payload(client, **updates):
        value = {
            "id": client["official_repo_id"],
            "full_name": client["github_repo"],
            "owner": {"id": client["official_owner_id"]},
            "archived": False,
            "disabled": False,
            "pushed_at": "2026-09-15T10:00:00Z",
        }
        value.update(updates)
        return value

    @staticmethod
    def release_payload(version="v10", published="2026-09-15T10:00:00Z", rid=100, assets=None):
        if assets is None:
            assets = [{"id": 1001, "name": "client.bin", "size": 100, "state": "uploaded"}]
        return {
            "id": rid,
            "tag_name": version,
            "published_at": published,
            "draft": False,
            "prerelease": False,
            "assets": assets,
        }

    @staticmethod
    def evidence(_url):
        return "Mihomo github.com/metacubex/mihomo/"

    @staticmethod
    def app_payload(client, *, seller=None, release_date="2026-09-15T10:00:00Z", version="3"):
        return {
            "resultCount": 1,
            "results": [{
                "trackId": int(client["app_store_id"]),
                "sellerName": seller if seller is not None else client["app_store_seller"],
                "version": version,
                "currentVersionReleaseDate": release_date,
            }],
        }

    def healthy_github_record(self, client):
        return {
            "source": d.positive_record(
                None, STAMP, d.source_scope(client), state="ok",
                repo_id=client["official_repo_id"], owner_id=client["official_owner_id"],
                full_name=client["github_repo"], last_activity_at="2026-09-15T10:00:00Z",
            ),
            "release": d.positive_record(
                None, STAMP, d.release_scope(client), state="ok", version="v10",
                published_at="2026-09-15T10:00:00Z", release_id=100, asset_count=1,
            ),
        }

    def test_source_scope_changes_when_identity_pin_changes(self):
        client = copy.deepcopy(self.by_id["flclash"])
        original = d.source_scope(client)
        client["official_repo_id"] += 1
        self.assertNotEqual(original, d.source_scope(client))

    def test_release_scope_changes_when_download_target_changes(self):
        client = copy.deepcopy(self.by_id["flclash"])
        original = d.release_scope(client)
        client["download_url"] += "/tag/v1"
        self.assertNotEqual(original, d.release_scope(client))

    def test_old_identity_lkg_cannot_authorize_new_catalog_identity_after_timeout(self):
        client_a = copy.deepcopy(self.by_id["flclash"])
        old = {"source": d.positive_record(None, STAMP, d.source_scope(client_a), state="ok", last_activity_at=STAMP)}
        client_b = copy.deepcopy(client_a)
        client_b["official_repo_id"] += 999
        out, _, ok = d.audit_github(
            client_b, old, None, NOW,
            lambda *args: (_ for _ in ()).throw(OSError("timeout")),
            lambda _url: "",
        )
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "unknown")
        self.assertIsNone(out["source"].get("last_success_at"))
        self.assertFalse(d.source_trusted(client_b, out, NOW))
        self.assertEqual(d.links_for(client_b, out, NOW)[1], "")

    def test_v1_observations_migrate_fail_closed_without_granting_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observations.json"
            path.write_text(json.dumps({"version": 1, "clients": {"flclash": {"source": {"state": "ok"}}}}), encoding="utf-8")
            migrated = d.load_observations(path)
        self.assertEqual(migrated["version"], d.OBSERVATION_VERSION)
        self.assertEqual(migrated["clients"], {})
        self.assertTrue(migrated["health"]["anomalies"])

    def test_scope_reset_cannot_pass_health_before_real_audit(self):
        observations = d.empty_observations()
        health = d.derive_health([self.by_id["flclash"]], observations, NOW)
        self.assertEqual(health["coverage"], 0.0)
        self.assertTrue(any("scope mismatch" in item for item in health["anomalies"]))

    def test_first_region_missing_is_unknown_unverified_and_has_no_download(self):
        client = self.by_id["shadowrocket"]
        out, _, ok = d.audit_app_store_source(client, {}, NOW, lambda *args: {"resultCount": 0, "results": []})
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "unknown")
        self.assertEqual(out["source"]["observation_state"], "unverified")
        self.assertEqual(out["source"]["unverified_reason"], "region_missing")
        self.assertEqual(d.links_for(client, out, NOW)[1], "")

    def test_app_store_identity_mismatch_does_not_refresh_positive_success_time(self):
        client = self.by_id["shadowrocket"]
        good, _, _ = d.audit_app_store_source(client, {}, NOW, lambda *args: self.app_payload(client))
        prior_success = good["source"]["last_success_at"]
        later = NOW + dt.timedelta(hours=1)
        bad, _, _ = d.audit_app_store_source(
            client, good, later,
            lambda *args: self.app_payload(client, seller="Attacker Seller"),
        )
        self.assertEqual(bad["source"]["state"], "identity_mismatch")
        self.assertEqual(bad["source"]["last_success_at"], prior_success)
        self.assertEqual(bad["source"]["observation_state"], "verified_negative")

    def test_identity_mismatch_survives_region_missing_and_timeout_until_positive_match(self):
        client = self.by_id["shadowrocket"]
        good, _, _ = d.audit_app_store_source(client, {}, NOW, lambda *args: self.app_payload(client))
        mismatch, _, _ = d.audit_app_store_source(
            client, good, NOW + dt.timedelta(hours=1),
            lambda *args: self.app_payload(client, seller="Wrong Seller"),
        )
        region, _, _ = d.audit_app_store_source(
            client, mismatch, NOW + dt.timedelta(hours=2),
            lambda *args: {"resultCount": 0, "results": []},
        )
        self.assertEqual(region["source"]["state"], "identity_mismatch")
        self.assertEqual(region["source"]["observation_state"], "unverified")
        timeout, _, _ = d.audit_app_store_source(
            client, region, NOW + dt.timedelta(hours=3),
            lambda *args: (_ for _ in ()).throw(OSError("timeout")),
        )
        self.assertEqual(timeout["source"]["state"], "identity_mismatch")
        self.assertEqual(d.links_for(client, timeout, NOW + dt.timedelta(hours=3))[1], "")
        recovered, _, ok = d.audit_app_store_source(
            client, timeout, NOW + dt.timedelta(hours=4),
            lambda *args: self.app_payload(client, release_date="2026-09-15T11:00:00Z"),
        )
        self.assertTrue(ok)
        self.assertEqual(recovered["source"]["state"], "ok")
        self.assertNotIn("observed_seller", recovered["source"])
        self.assertEqual(d.links_for(client, recovered, NOW + dt.timedelta(hours=4))[1], client["download_url"])

    def test_exhaustive_app_store_sequences_never_recover_mismatch_without_good(self):
        client = self.by_id["shadowrocket"]
        events = ("good", "mismatch", "region", "timeout")
        for sequence in itertools.product(events, repeat=5):
            record = {}
            mismatch_blocked = False
            for index, event in enumerate(sequence):
                when = NOW + dt.timedelta(minutes=index + 1)
                if event == "good":
                    request = lambda *args, c=client: self.app_payload(c)
                elif event == "mismatch":
                    request = lambda *args, c=client: self.app_payload(c, seller="Wrong Seller")
                elif event == "region":
                    request = lambda *args: {"resultCount": 0, "results": []}
                else:
                    request = lambda *args: (_ for _ in ()).throw(OSError("timeout"))
                record, _, _ = d.audit_app_store_source(client, record, when, request)
                if event == "mismatch":
                    mismatch_blocked = True
                elif event == "good":
                    mismatch_blocked = False
                if event == "region":
                    self.assertEqual(d.links_for(client, record, when)[1], "", sequence)
                if mismatch_blocked:
                    self.assertEqual(record["source"]["state"], "identity_mismatch", sequence)
                    self.assertEqual(d.links_for(client, record, when)[1], "", sequence)

    def test_app_store_same_day_rollback_detected_with_full_timestamp(self):
        client = self.by_id["shadowrocket"]
        good, _, _ = d.audit_app_store_source(
            client, {}, NOW,
            lambda *args: self.app_payload(client, release_date="2026-09-15T10:30:00Z"),
        )
        rolled, issues, ok = d.audit_app_store_source(
            client, good, NOW + dt.timedelta(hours=1),
            lambda *args: self.app_payload(client, release_date="2026-09-15T09:30:00Z", version="2"),
        )
        self.assertFalse(ok)
        self.assertEqual(rolled["release"]["state"], "rollback")
        self.assertTrue(any("timestamp moved backwards" in item for item in issues))

    def test_github_same_day_release_rollback_detected(self):
        client = self.by_id["flclash"]
        old = self.healthy_github_record(client)
        old["release"]["published_at"] = "2026-09-15T10:30:00Z"
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release_payload("v9", "2026-09-15T09:30:00Z", 99)
            return self.repo_payload(client)
        out, issues, ok = d.audit_github(client, old, None, NOW, api, lambda _url: "github.com/metacubex/mihomo/")
        self.assertFalse(ok)
        self.assertEqual(out["release"]["state"], "rollback")
        self.assertEqual(out["release"]["version"], "v10")
        self.assertTrue(any("timestamp moved backwards" in item for item in issues))

    def test_future_remote_release_timestamp_is_observation_error(self):
        client = self.by_id["flclash"]
        old = self.healthy_github_record(client)
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release_payload(published="2026-09-16T12:00:00Z")
            return self.repo_payload(client)
        out, _, ok = d.audit_github(client, old, None, NOW, api, lambda _url: "github.com/metacubex/mihomo/")
        self.assertFalse(ok)
        self.assertEqual(out["release"]["state"], "ok")
        self.assertEqual(out["release"]["observation_state"], "error")
        self.assertIn("future", out["release"]["error"])

    def test_future_remote_repo_activity_is_observation_error(self):
        client = self.by_id["flclash"]
        old = self.healthy_github_record(client)
        out, _, ok = d.audit_github(
            client, old, None, NOW,
            lambda *args: self.repo_payload(client, pushed_at="2026-09-16T12:00:00Z"),
            lambda _url: "github.com/metacubex/mihomo/",
        )
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["source"]["observation_state"], "error")

    def test_future_local_last_success_is_not_fresh(self):
        client = self.by_id["flclash"]
        record = self.healthy_github_record(client)
        record["source"]["last_success_at"] = "2026-09-16T00:00:00Z"
        self.assertFalse(d.component_fresh(client, record, "source", NOW))
        self.assertFalse(d.source_trusted(client, record, NOW))

    def test_source_fresh_release_stale_means_gray_and_download_withheld(self):
        client = self.by_id["flclash"]
        record = self.healthy_github_record(client)
        record["release"]["last_success_at"] = "2026-09-01T00:00:00Z"
        status, _ = d.activity_status(client, record, NOW)
        self.assertEqual(status, "❓")
        self.assertEqual(d.links_for(client, record, NOW)[1], "")

    def test_core_mismatch_degrades_status_but_is_not_binary_download_gate(self):
        client = self.by_id["flclash"]
        record = self.healthy_github_record(client)
        record["core_evidence"] = d.negative_record(
            d.positive_record(None, STAMP, d.core_evidence_scope(client), state="ok"),
            STAMP, d.core_evidence_scope(client), "mismatch",
        )
        status, _ = d.activity_status(client, record, NOW)
        self.assertEqual(status, "❓")
        self.assertEqual(d.links_for(client, record, NOW)[1], client["download_url"])

    def test_manual_source_never_authorizes_main_download(self):
        client = copy.deepcopy(self.by_id["clashn-legacy"])
        client["download_url"] = "https://github.com/2dust/clashN/releases"
        self.assertEqual(d.links_for(client, {}, NOW)[1], "")

    def test_catalog_rejects_manual_main_download(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(item for item in payload["clients"] if item["id"] == "clashn-legacy")
        target["download_url"] = "https://github.com/2dust/clashN/releases"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manual sources cannot authorize"):
                d.load_clients(path)

    def test_manual_audit_has_no_fake_heartbeat_record(self):
        client = self.by_id["clashn-legacy"]
        out = d.audit([client], {"version": d.OBSERVATION_VERSION, "clients": {}}, now=NOW)
        self.assertNotIn(client["id"], out["clients"])
        self.assertEqual(out["health"]["attempted"], 0)

    def test_archived_to_active_transition_sets_sticky_lifecycle_review(self):
        client = copy.deepcopy(self.by_id["clash-verge-legacy"])
        old_source = d.positive_record(
            None, STAMP, d.source_scope(client), state="archived",
            repo_id=client["official_repo_id"], owner_id=client["official_owner_id"],
            full_name=client["github_repo"], last_activity_at="2023-11-03T08:00:47Z",
        )
        history = client["historical_release"]
        assets = [dict(asset, state="uploaded") for asset in history["assets"]]
        def api(url, token=None):
            if "/releases/tags/" in url:
                return self.release_payload("v1.3.8", "2023-10-30T17:38:38Z", history["release_id"], assets)
            return self.repo_payload(client, archived=False)
        first, _, _ = d.audit_github(client, {"source": old_source}, None, NOW, api, lambda _url: "")
        self.assertTrue(first["source"]["lifecycle_review_required"])
        second, _, _ = d.audit_github(client, first, None, NOW + dt.timedelta(hours=1), api, lambda _url: "")
        self.assertTrue(second["source"]["lifecycle_review_required"])

    def test_first_unarchived_observation_for_discontinued_source_does_not_alarm_by_itself(self):
        client = copy.deepcopy(self.by_id["clash-verge-legacy"])
        history = client["historical_release"]
        assets = [dict(asset, state="uploaded") for asset in history["assets"]]
        def api(url, token=None):
            if "/releases/tags/" in url:
                return self.release_payload("v1.3.8", "2023-10-30T17:38:38Z", history["release_id"], assets)
            return self.repo_payload(client, archived=False)
        out, _, _ = d.audit_github(client, {}, None, NOW, api, lambda _url: "")
        self.assertNotIn("lifecycle_review_required", out["source"])

    def test_assets_missing_preserves_lkg_asset_count_and_records_observed_count(self):
        client = self.by_id["flclash"]
        old = self.healthy_github_record(client)
        old["release"]["asset_count"] = 7
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release_payload(assets=[])
            return self.repo_payload(client)
        out, _, ok = d.audit_github(client, old, None, NOW, api, lambda _url: "github.com/metacubex/mihomo/")
        self.assertFalse(ok)
        self.assertEqual(out["release"]["asset_count"], 7)
        self.assertEqual(out["release"]["observed_asset_count"], 0)

    def test_positive_recovery_clears_observed_diagnostics(self):
        client = self.by_id["flclash"]
        old = self.healthy_github_record(client)
        bad = d.negative_record(
            old["release"], STAMP, d.release_scope(client), "identity_mismatch",
            observed_release_id=999, observed_asset_count=0,
        )
        recovered = d.positive_record(
            bad, d.iso(NOW + dt.timedelta(hours=1)), d.release_scope(client),
            state="ok", version="v10", published_at="2026-09-15T10:00:00Z",
            release_id=100, asset_count=1,
        )
        self.assertNotIn("observed_release_id", recovered)
        self.assertNotIn("observed_asset_count", recovered)
        self.assertEqual(recovered["state"], "ok")

    def test_same_day_healthy_scoped_audit_is_byte_stable(self):
        client = copy.deepcopy(self.by_id["flclash"])
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release_payload()
            return self.repo_payload(client)
        first = d.audit([client], d.empty_observations(), api, lambda _url: "github.com/metacubex/mihomo/", NOW)
        second = d.audit([client], first, api, lambda _url: "github.com/metacubex/mihomo/", NOW + dt.timedelta(hours=1))
        self.assertEqual(first, second)

    def test_cached_green_health_cannot_hide_scope_mismatch(self):
        client = self.by_id["flclash"]
        observations = {
            "version": d.OBSERVATION_VERSION,
            "clients": {"flclash": self.healthy_github_record(client)},
            "health": {"coverage": 1.0, "anomalies": []},
        }
        observations["clients"]["flclash"]["source"]["scope"] = "sha256:wrong"
        health = d.derive_health([client], observations, NOW)
        self.assertTrue(any("scope mismatch" in item for item in health["anomalies"]))
        self.assertEqual(health["coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()

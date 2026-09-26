import copy
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_readme as d

NOW = dt.datetime(2026, 9, 15, 8, 0, tzinfo=dt.timezone.utc)
STAMP = d.iso(NOW)


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clients = d.load_clients(ROOT / "data" / "clients.json")
        cls.by_id = {client["id"]: client for client in cls.clients}

    def test_stable_ids_unique(self):
        self.assertEqual(len(self.by_id), len(self.clients))

    def test_scope_is_explicit(self):
        self.assertEqual(self.by_id["flclash"]["category"], "mihomo")
        self.assertEqual(self.by_id["karing"]["category"], "sing_box")
        self.assertEqual(self.by_id["hiddify"]["category"], "sing_box")
        self.assertEqual(self.by_id["stash"]["category"], "proprietary")
        self.assertEqual(self.by_id["v2rayn"]["category"], "multi_core")
        self.assertEqual(self.by_id["shadowrocket"]["category"], "proprietary")
        self.assertEqual(self.by_id["surge"]["category"], "proprietary")
        self.assertEqual(self.by_id["sing-box"]["category"], "sing_box")
        self.assertEqual(self.by_id["clash-verge-legacy"]["category"], "legacy")

    def test_catalog_has_no_commercial_category(self):
        self.assertNotIn("commercial", {client["category"] for client in self.clients})

    def test_mihomo_candidates_added(self):
        for cid in ("sparkle", "metacubexd", "bettbox", "asteriskmeta"):
            self.assertIn(cid, self.by_id)
            self.assertEqual(self.by_id[cid]["category"], "mihomo")

    def test_representative_clients_are_normalized(self):
        self.assertEqual(self.by_id["hako"]["source_type"], "app_store")
        self.assertNotIn("github_repo", self.by_id["hako"])
        self.assertNotIn("official_repo_id", self.by_id["hako"])
        self.assertNotIn("core_evidence", self.by_id["hako"])
        self.assertEqual(self.by_id["hako"]["app_store_core_patterns"], ["Hako builds on the open-source mihomo project"])
        self.assertEqual(self.by_id["hako"]["platforms"]["tvos"], True)
        self.assertEqual(self.by_id["stash"]["core"], "未公开")
        self.assertEqual(self.by_id["surge"]["name"], "Surge")
        self.assertEqual(self.by_id["surge"]["core"], "未公开")
        self.assertEqual(self.by_id["shadowrocket"]["core"], "未公开")
        self.assertEqual(self.by_id["quantumult-x"]["core"], "未公开")
        self.assertEqual(self.by_id["loon"]["core"], "未公开")
        self.assertEqual(self.by_id["egern"]["core"], "未公开")
        self.assertEqual(self.by_id["sing-box"]["core"], "sing-box")
        self.assertEqual(self.by_id["clash-nyanpasu"]["core"], "多内核（Mihomo / Clash Premium / Clash Rust / Meow）")
        self.assertEqual(self.by_id["clash-party"]["core"], "多内核（Mihomo / Smart Core）")
        self.assertEqual(self.by_id["v2rayn"]["core"], "多内核（Xray / v2fly / Mihomo / sing-box 等）")
        self.assertEqual(self.by_id["nekobox-android"]["source_note"], "Google Play 版本自 2024 年 5 月起不再由原项目维护。")
        self.assertTrue(self.by_id["sing-box"]["platforms"]["tvos"])
        self.assertTrue(self.by_id["stash"]["platforms"]["windows"])
        self.assertEqual(self.by_id["stash"]["download_page_url"], "https://stash.ws/download")

    def test_bettbox_core_evidence_accepts_current_wording(self):
        pattern = self.by_id["bettbox"]["core_evidence"][0]["patterns"][1]
        self.assertRegex("Bettbox 基于强大的 Mihomo(Clash Meta) 内核深度打造", pattern)

    def test_core_labels_follow_display_convention(self):
        simple = {"Mihomo", "Clash", "sing-box", "Hako（基于 Mihomo）", "未公开"}
        for client in self.clients:
            core = client.get("core")
            if not core:
                continue
            self.assertTrue(
                core in simple or (core.startswith("多内核（") and core.endswith("）")),
                f"{client['name']}: {core}",
            )
            if " / " in core:
                self.assertTrue(core.startswith("多内核（"), f"{client['name']}: {core}")

    def test_catalog_avoids_freeform_notes_and_low_representativeness_entries(self):
        self.assertNotIn("nyx", self.by_id)
        self.assertNotIn("anyportal", self.by_id)
        self.assertTrue(all("notes" not in client for client in self.clients))

    def test_active_categories_follow_core(self):
        for client in self.clients:
            if client["category"] == "legacy":
                continue
            core = client.get("core")
            if core in {"Mihomo", "Clash"} or str(core).startswith("Hako（"):
                expected = "mihomo"
            elif core == "sing-box":
                expected = "sing_box"
            elif str(core).startswith("多内核（"):
                expected = "multi_core"
            elif core == "未公开":
                expected = "proprietary"
            else:
                self.fail(f"Unclassified core for {client['name']}: {core}")
            self.assertEqual(client["category"], expected, client["name"])

    def test_active_directory_entries_pass_entry_gate(self):
        for client in self.clients:
            if client["category"] == "legacy":

                continue

            self.assertNotEqual(client["source_type"], "manual")
            self.assertTrue(client.get("download_url"), client["name"])
            self.assertTrue(any(client["platforms"].values()), client["name"])

    def test_loader_rejects_active_entry_without_official_download(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "flclash")
        target["download_url"] = ""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires an official download/store URL"):
                d.load_clients(path)

    def test_loader_rejects_entry_without_supported_platform(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "flclash")
        target["platforms"] = {key: False for key in d.PLATFORMS}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least one supported platform"):
                d.load_clients(path)

    def test_active_derivative_failover_removed(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        raw = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn('"backups"', raw)
        for name in ("flclashx", "slothclash", "clashfest"):
            self.assertNotIn(name, raw)

    def test_no_direct_binary_urls(self):
        for client in self.clients:
            self.assertNotIn("/releases/download/", client.get("download_url", "").lower())
            for archive in client.get("third_party_archives", []):
                self.assertNotIn("/releases/download/", archive["url"].lower())

    def test_unverified_legacy_archives_not_main_downloads(self):
        for cid in ("cfw-legacy", "clashx-legacy", "clashx-pro-legacy", "cfa-legacy"):
            self.assertEqual(self.by_id[cid]["download_url"], "")
            self.assertTrue(self.by_id[cid].get("third_party_archives"))

    def test_clash_verge_uses_original_historical_release(self):
        client = self.by_id["clash-verge-legacy"]
        self.assertEqual(client["github_repo"], "zzzgydi/clash-verge")
        self.assertEqual(client["historical_release"]["release_id"], 127230375)
        self.assertIn("zzzgydi/clash-verge/releases/tag/v1.3.8", client["download_url"])

    def test_all_github_sources_have_repo_id_pins(self):
        for client in self.clients:
            if client["source_type"] == "github":
                self.assertIsInstance(client["official_repo_id"], int)


    def test_active_github_core_claims_have_evidence(self):
        for client in self.clients:
            if client["source_type"] == "github" and client["category"] != "legacy" and "core" in client:
                self.assertTrue(client.get("core_evidence"), client["name"])

    def test_loader_rejects_active_github_core_without_evidence(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "bettbox")
        target.pop("core_evidence", None)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "core claim requires"):
                d.load_clients(path)

    def test_loader_rejects_active_app_store_core_without_evidence(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "hako")
        target.pop("core_evidence", None)
        target.pop("app_store_core_patterns", None)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "App Store core claim requires evidence"):
                d.load_clients(path)

    def test_loader_rejects_both_app_store_core_evidence_modes(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "hako")
        target["core_evidence"] = [{"url": "https://raw.githubusercontent.com/example/repo/main/README.md", "patterns": ["mihomo"]}]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot both be configured"):
                d.load_clients(path)

    def test_app_store_identity_has_seller_pin(self):
        for client in self.clients:
            if client["source_type"] == "app_store" or client.get("release_source") == "app_store":
                self.assertTrue(client["app_store_id"].isdigit())
                self.assertTrue(client["app_store_seller"])

    def test_loader_rejects_github_download_outside_pinned_repo(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "flclash")
        target["download_url"] = "https://github.com/attacker/FlClash/releases"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pinned official repository"):
                d.load_clients(path)

    def test_loader_rejects_github_releases_prefix_spoof(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "flclash")
        target["download_url"] = "https://github.com/chen08209/FlClash/releases-evil"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pinned official repository"):
                d.load_clients(path)

    def test_loader_rejects_app_store_download_for_different_app_id(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "hako")
        target["download_url"] = "https://apps.apple.com/app/id123456789"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pinned app_store_id"):
                d.load_clients(path)

    def test_loader_rejects_app_store_id_substring_spoof(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "hako")
        target["download_url"] = "https://apps.apple.com/app/id67942571890"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pinned app_store_id"):
                d.load_clients(path)

    def test_loader_rejects_noncanonical_github_repo_path(self):
        payload = json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))
        target = next(client for client in payload["clients"] if client["id"] == "flclash")
        target["github_repo"] = "chen08209/FlClash/releases) malicious"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clients.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical owner/repository"):
                d.load_clients(path)


class RequestRetryTests(unittest.TestCase):
    def test_json_transient_network_failure_retries_once(self):
        response = io.BytesIO(b'{"ok": true}')
        with patch.object(d.urllib.request, "urlopen", side_effect=[urllib.error.URLError("tls eof"), response]) as opener:
            with patch.object(d.time, "sleep") as sleeper:
                self.assertEqual(d.request_json("https://example.invalid/test", attempts=2), {"ok": True})
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once()

    def test_text_transient_network_failure_retries_once(self):
        class TextResponse(io.BytesIO):
            class Headers:
                @staticmethod
                def get_content_charset():
                    return "utf-8"
            headers = Headers()

        response = TextResponse("Mihomo".encode("utf-8"))
        with patch.object(d.urllib.request, "urlopen", side_effect=[urllib.error.URLError("tls eof"), response]) as opener:
            with patch.object(d.time, "sleep") as sleeper:
                self.assertEqual(d.request_text("https://example.invalid/test", attempts=2), "Mihomo")
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once()

    def test_404_is_not_retried(self):
        error = urllib.error.HTTPError("https://example.invalid/missing", 404, "missing", {}, None)
        with patch.object(d.urllib.request, "urlopen", side_effect=error) as opener:
            self.assertIsNone(d.request_json("https://example.invalid/missing", attempts=2))
        self.assertEqual(opener.call_count, 1)


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clients = d.load_clients(ROOT / "data" / "clients.json")
        cls.by_id = {client["id"]: client for client in cls.clients}

    def repo(self, client, **updates):
        value = {
            "id": client.get("official_repo_id", 1),
            "full_name": client["github_repo"],
            "owner": {"id": 2},
            "archived": False,
            "disabled": False,
            "pushed_at": "2026-09-14T00:00:00Z",
        }
        value.update(updates)
        return value

    @staticmethod
    def release(version="v9", published="2026-09-14T00:00:00Z", rid=99, assets=None):
        if assets is None:
            assets = [{"id": rid * 10 + 1, "name": "client.bin", "size": 100, "state": "uploaded"}]
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
        return "Mihomo Clash Premium Clash Rust Meow Smart 内核 embedded mihomo core github.com/metacubex/mihomo/ 内置Mihomo内核 based on Sing-box powered by the Hako kernel based on mihomo github.com/Dreamacro/clash A Graphical user interface of Clash.Meta modified sing-box core Xray sing-box sing-box / universal proxy toolchain"

    @staticmethod
    def scoped_source(client, **updates):
        value = {
            "scope": d.source_scope(client),
            "state": "ok",
            "observation_state": "fresh",
            "last_success_at": STAMP,
            "observed_at": STAMP,
            "last_activity_at": "2026-09-14T00:00:00Z",
            "consecutive_failures": 0,
        }
        value.update(updates)
        return value

    @staticmethod
    def scoped_release(client, **updates):
        value = {
            "scope": d.release_scope(client),
            "state": "ok",
            "observation_state": "fresh",
            "last_success_at": STAMP,
            "observed_at": STAMP,
            "version": "v10",
            "published_at": "2026-09-14T00:00:00Z",
            "release_id": 100,
            "asset_count": 1,
            "consecutive_failures": 0,
        }
        value.update(updates)
        return value

    def test_different_repo_id_at_configured_path_is_confirmed_bad(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release()
            return self.repo(client, id=-1)
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "identity_mismatch")
        self.assertTrue(issues)

    def test_same_repo_id_owner_transfer_is_followed(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release()
            return self.repo(client, owner={"id": -1}, full_name="new-owner/FlClash")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["source"]["owner_id"], -1)
        self.assertEqual(out["source"]["full_name"], "new-owner/FlClash")

    def test_repo_canonical_name_change_updates_rendered_links(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release()
            return self.repo(client, owner={"id": -1}, full_name="new-owner/FlClash")
        out, _, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertTrue(ok)
        repository, download = d.links_for(client, out, NOW)
        self.assertEqual(repository, "https://github.com/new-owner/FlClash")
        self.assertEqual(download, "https://github.com/new-owner/FlClash/releases")

    def test_404_is_deterministic_negative_evidence(self):
        client = self.by_id["flclash"]
        out, issues, ok = d.audit_github(client, {}, None, NOW, lambda *args: None, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "missing")
        self.assertTrue(issues)

    def test_path_404_recovers_by_pinned_repo_id(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return None
            if url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                return self.repo(client)
            if url.endswith("/releases/latest"):
                return self.release()
            self.fail(f"unexpected API URL: {url}")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["source"]["repo_id"], client["official_repo_id"])

    def test_path_404_follows_pinned_repo_id_canonical_name(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return None
            if url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                return self.repo(client, full_name="new-owner/FlClash")
            if url == "https://api.github.com/repos/new-owner/FlClash/releases/latest":
                return self.release()
            self.fail(f"unexpected API URL: {url}")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(d.links_for(client, out, NOW), (
            "https://github.com/new-owner/FlClash",
            "https://github.com/new-owner/FlClash/releases",
        ))

    def test_path_404_rejects_pinned_id_response_with_different_repo_id(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if url == f"https://api.github.com/repos/{client['github_repo']}":
                return None
            if url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                return self.repo(client, id=-1)
            self.fail(f"unexpected API URL: {url}")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "identity_mismatch")
        self.assertTrue(issues)


    def test_transient_failure_preserves_lkg(self):
        client = self.by_id["flclash"]
        old = {
            "source": self.scoped_source(
                client,
                last_success_at="2026-09-14T00:00:00Z",
                observed_at="2026-09-14T00:00:00Z",
            )
        }
        out, issues, ok = d.audit_github(
            client, old, None, NOW,
            lambda *args: (_ for _ in ()).throw(OSError("timeout")),
            self.evidence,
        )
        self.assertFalse(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["source"]["observation_state"], "error")
        self.assertEqual(out["source"]["last_activity_at"], old["source"]["last_activity_at"])


    def test_schema_change_is_observation_error_not_negative_fact(self):
        client = self.by_id["flclash"]
        old = {"source": self.scoped_source(client, last_success_at="2026-09-14T00:00:00Z", observed_at="2026-09-14T00:00:00Z")}
        out, _, ok = d.audit_github(client, old, None, NOW, lambda *args: {}, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["source"]["observation_state"], "error")

    def test_active_archived_repo_is_automatically_historical(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release()
            return self.repo(client, archived=True)
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "archived")

        self.assertEqual(d.effective_category(client, out), "legacy")
        self.assertEqual(d.activity_status(client, out, NOW)[0], "🔴")

    def test_active_repo_recovers_from_automatic_history_when_unarchived(self):
        client = self.by_id["flclash"]
        archived = {"source": self.scoped_source(client, state="archived")}
        self.assertEqual(d.effective_category(client, archived), "legacy")
        active = {"source": self.scoped_source(client, state="ok")}
        self.assertEqual(d.effective_category(client, active), client["category"])

    def test_release_rollback_preserves_previous_version(self):
        client = self.by_id["flclash"]
        old = {
            "source": self.scoped_source(client),
            "release": self.scoped_release(
                client,
                published_at="2026-09-14T12:00:00Z",
                last_success_at="2026-09-14T12:00:00Z",
                observed_at="2026-09-14T12:00:00Z",
            ),
        }
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v9", "2026-09-01T00:00:00Z")
            return self.repo(client)
        out, issues, ok = d.audit_github(client, old, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["release"]["version"], "v10")
        self.assertEqual(out["release"]["state"], "rollback")
        self.assertTrue(issues)

    def test_repeated_github_latest_rollback_is_accepted_after_confirmation(self):
        client = self.by_id["flclash"]
        old = {
            "source": self.scoped_source(client),
            "release": self.scoped_release(client),
        }

        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v9", "2026-09-01T00:00:00Z", 99)
            return self.repo(client)

        first, issues, ok = d.audit_github(client, old, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertTrue(issues)
        self.assertEqual(first["release"]["state"], "rollback")
        self.assertEqual(first["release"]["version"], "v10")
        self.assertEqual(first["release"]["observed_version"], "v9")

        second, issues, ok = d.audit_github(
            client,
            first,
            None,
            NOW + dt.timedelta(hours=1),
            api,
            self.evidence,
        )
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(second["release"]["state"], "ok")
        self.assertEqual(second["release"]["version"], "v9")
        self.assertEqual(second["release"]["release_id"], 99)
        self.assertNotIn("observed_version", second["release"])

    def test_changed_github_rollback_candidate_requires_new_confirmation(self):
        client = self.by_id["flclash"]
        old = {
            "source": self.scoped_source(client),
            "release": self.scoped_release(client),
        }

        def api_v9(url, token=None):
            if "/releases/latest" in url:
                return self.release("v9", "2026-09-01T00:00:00Z", 99)
            return self.repo(client)

        first, _, _ = d.audit_github(client, old, None, NOW, api_v9, self.evidence)

        def api_v8(url, token=None):
            if "/releases/latest" in url:
                return self.release("v8", "2026-08-01T00:00:00Z", 98)
            return self.repo(client)

        second, issues, ok = d.audit_github(
            client,
            first,
            None,
            NOW + dt.timedelta(hours=1),
            api_v8,
            self.evidence,
        )
        self.assertFalse(ok)
        self.assertTrue(issues)
        self.assertEqual(second["release"]["state"], "rollback")
        self.assertEqual(second["release"]["version"], "v10")
        self.assertEqual(second["release"]["observed_version"], "v8")
        self.assertEqual(second["release"]["observed_release_id"], 98)

    def test_latest_release_disappearance_is_anomaly(self):
        client = self.by_id["flclash"]
        old = {"release": {"state": "ok", "version": "v10", "published_at": "2026-09-14"}}
        def api(url, token=None):
            return None if "/releases/latest" in url else self.repo(client)
        out, issues, ok = d.audit_github(client, old, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["release"]["state"], "missing")
        self.assertTrue(issues)

    def test_first_observation_without_latest_release_is_anomaly(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            return None if "/releases/latest" in url else self.repo(client)
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["release"]["state"], "missing")
        self.assertTrue(any("no latest release" in issue for issue in issues))
        _, download = d.links_for(client, out, NOW)
        self.assertEqual(download, client["download_url"])

    def test_latest_release_without_usable_assets_is_anomaly(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v10", "2026-09-14T00:00:00Z", 100, [])
            return self.repo(client)
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["release"]["state"], "assets_missing")
        self.assertNotIn("asset_count", out["release"])
        self.assertEqual(out["release"]["observed_asset_count"], 0)
        self.assertTrue(any("no usable uploaded assets" in issue for issue in issues))
        self.assertEqual(d.links_for(client, out, NOW)[1], client["download_url"])

    def test_same_tag_release_recreation_is_anomaly(self):
        client = self.by_id["flclash"]
        old = {"release": self.scoped_release(client)}
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v10", "2026-09-14T00:00:00Z", 101)
            return self.repo(client)
        out, issues, ok = d.audit_github(client, old, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["release"]["state"], "identity_mismatch")
        self.assertEqual(out["release"]["release_id"], 100)
        self.assertEqual(out["release"]["observed_release_id"], 101)
        self.assertTrue(any("recreated under the same tag" in issue for issue in issues))
        self.assertEqual(d.links_for(client, out, NOW)[1], "")

    def test_clash_verge_archived_original_release_is_valid(self):
        client = self.by_id["clash-verge-legacy"]
        history = client["historical_release"]
        assets = [dict(asset, state="uploaded") for asset in history["assets"]]
        def api(url, token=None):
            if "/releases/tags/" in url:
                return self.release("v1.3.8", "2023-10-30T17:38:38Z", history["release_id"], assets)
            return self.repo(client, archived=True, pushed_at="2023-11-03T08:00:47Z")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "archived")
        self.assertEqual(out["historical_release"]["state"], "ok")

    def test_historical_release_recreation_is_blocked(self):
        client = self.by_id["clash-verge-legacy"]
        def api(url, token=None):
            if "/releases/tags/" in url:
                return self.release("v1.3.8", "2023-10-30T17:38:38Z", 999, [])
            return self.repo(client, archived=True, pushed_at="2023-11-03T08:00:47Z")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["historical_release"]["state"], "identity_mismatch")
        self.assertTrue(issues)

    def test_historical_asset_reupload_is_blocked(self):
        client = self.by_id["clash-verge-legacy"]
        history = client["historical_release"]
        assets = [dict(asset, state="uploaded") for asset in history["assets"]]
        assets[0]["id"] += 1
        def api(url, token=None):
            if "/releases/tags/" in url:
                return self.release("v1.3.8", "2023-10-30T17:38:38Z", history["release_id"], assets)
            return self.repo(client, archived=True, pushed_at="2023-11-03T08:00:47Z")
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, self.evidence)
        self.assertFalse(ok)
        self.assertEqual(out["historical_release"]["state"], "asset_mismatch")
        self.assertTrue(issues)

    def test_core_evidence_mismatch_is_anomaly(self):
        client = self.by_id["flclash"]
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release()
            return self.repo(client)
        out, issues, ok = d.audit_github(client, {}, None, NOW, api, lambda _url: "unrelated text")
        self.assertFalse(ok)
        self.assertEqual(out["core_evidence"]["state"], "mismatch")
        self.assertTrue(issues)

    def test_app_store_seller_transfer_is_identity_failure(self):
        client = self.by_id["shadowrocket"]
        payload = {"resultCount": 1, "results": [{"trackId": 932747118, "sellerName": "Different Seller", "version": "3", "currentVersionReleaseDate": "2026-09-15T00:00:00Z"}]}
        out, issues, ok = d.audit_app_store_source(client, {}, NOW, lambda *args: payload)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "identity_mismatch")
        self.assertTrue(issues)

    def test_app_store_core_evidence_success(self):
        client = self.by_id["hako"]
        payload = {"resultCount": 1, "results": [{"trackId": int(client["app_store_id"]), "sellerName": client["app_store_seller"], "version": "1.0.7", "currentVersionReleaseDate": "2026-09-14T00:00:00Z", "description": "Hako builds on the open-source mihomo project"}]}
        out, issues, ok = d.audit_app_store_source(client, {}, NOW, lambda *args: payload)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["release"]["state"], "ok")
        self.assertEqual(out["core_evidence"]["state"], "ok")
        self.assertEqual(d.links_for(client, out, NOW), (client["website_url"], client["download_url"]))

    def test_app_store_core_evidence_mismatch(self):
        client = self.by_id["hako"]
        payload = {"resultCount": 1, "results": [{"trackId": int(client["app_store_id"]), "sellerName": client["app_store_seller"], "version": "1.0.7", "currentVersionReleaseDate": "2026-09-14T00:00:00Z", "description": "Hako is an unrelated proxy engine"}]}
        out, issues, ok = d.audit_app_store_source(client, {}, NOW, lambda *args: payload)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["release"]["state"], "ok")
        self.assertEqual(out["core_evidence"]["state"], "mismatch")
        self.assertTrue(issues)

    def test_app_store_core_evidence_missing_description(self):
        client = self.by_id["hako"]
        payload = {"resultCount": 1, "results": [{"trackId": int(client["app_store_id"]), "sellerName": client["app_store_seller"], "version": "1.0.7", "currentVersionReleaseDate": "2026-09-14T00:00:00Z"}]}
        out, issues, ok = d.audit_app_store_source(client, {}, NOW, lambda *args: payload)
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "ok")
        self.assertEqual(out["release"]["state"], "ok")
        self.assertEqual(out["core_evidence"]["observation_state"], "error")
        self.assertTrue(issues == [])

    def test_app_store_core_evidence_is_expected_observation(self):
        self.assertIn("core_evidence", d.expected_observation_components(self.by_id["hako"]))

    def test_app_store_region_missing_is_not_global_delisting_claim(self):
        client = self.by_id["shadowrocket"]
        out, issues, ok = d.audit_app_store_source(client, {}, NOW, lambda *args: {"resultCount": 0, "results": []})
        self.assertFalse(ok)
        self.assertEqual(out["source"]["state"], "unknown")
        self.assertEqual(out["source"]["observation_state"], "unverified")
        self.assertEqual(out["source"]["unverified_reason"], "region_missing")
        self.assertTrue(issues)
        status, _ = d.activity_status(client, out, NOW)
        self.assertEqual(status, "❓")
        repository, download = d.links_for(client, out, NOW)
        self.assertEqual(repository, client["website_url"])
        self.assertEqual(download, client["download_url"])

    def test_coverage_below_95_percent_degrades_health(self):
        clients = []
        for index in range(20):
            clients.append({
                "id": f"x{index}", "name": f"X{index}", "category": "mihomo",
                "platforms": {key: False for key in d.PLATFORMS},
                "source_type": "github", "github_repo": f"o/r{index}",
                "official_repo_id": 100 + index,
                "download_url": f"https://github.com/o/r{index}/releases"
            })
        failed = {0, 1}
        def api(url, token=None):
            repo = url.split("/repos/")[1].split("/")[1]
            index = int(repo[1:])
            if index in failed and "/releases/" not in url:
                raise OSError("down")
            if "/releases/latest" in url:
                return self.release(rid=300 + index)
            return {"id": 100 + index, "full_name": f"o/r{index}", "owner": {"id": 200 + index}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        out = d.audit(clients, {"version": 1, "clients": {}}, api, lambda _url: "", NOW)
        self.assertEqual(out["health"]["succeeded"], 18)
        self.assertTrue(any("coverage" in issue for issue in out["health"]["anomalies"]))

    def test_concurrent_audit_persists_catalog_order(self):
        clients = [
            {
                "id": "remote-a", "name": "Remote A", "category": "mihomo",
                "platforms": {key: False for key in d.PLATFORMS},
                "source_type": "github", "github_repo": "o/a",
                "official_repo_id": 101,
                "download_url": "https://github.com/o/a/releases",
            },
            {
                "id": "manual-middle", "name": "Manual", "category": "legacy",
                "platforms": {key: False for key in d.PLATFORMS},
                "source_type": "manual", "download_url": "",

            },
            {
                "id": "remote-b", "name": "Remote B", "category": "mihomo",
                "platforms": {key: False for key in d.PLATFORMS},
                "source_type": "github", "github_repo": "o/b",
                "official_repo_id": 102,
                "download_url": "https://github.com/o/b/releases",
            },
        ]
        def api(url, token=None):
            if "/releases/latest" in url:
                rid = 301 if "/o/a/" in url else 302
                return self.release("v1", "2026-09-14T00:00:00Z", rid)
            if "/o/a" in url:
                return {"id": 101, "full_name": "o/a", "owner": {"id": 201}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
            return {"id": 102, "full_name": "o/b", "owner": {"id": 202}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        out = d.audit(clients, {"version": 1, "clients": {}}, api, lambda _url: "", NOW)
        self.assertEqual(list(out["clients"]), ["remote-a", "remote-b"])
        self.assertNotIn("manual-middle", out["clients"])
        self.assertEqual(out["health"]["succeeded"], 2)
        self.assertEqual(out["health"]["anomalies"], [])

    def test_one_failure_of_twenty_meets_95_percent_coverage(self):
        clients = []
        for index in range(20):
            clients.append({
                "id": f"x{index}", "name": f"X{index}", "category": "mihomo",
                "platforms": {key: False for key in d.PLATFORMS},
                "source_type": "github", "github_repo": f"o/r{index}",
                "official_repo_id": 100 + index,
                "download_url": f"https://github.com/o/r{index}/releases"
            })
        def api(url, token=None):
            repo = url.split("/repos/")[1].split("/")[1]
            index = int(repo[1:])
            if index == 0 and "/releases/" not in url:
                raise OSError("down")
            if "/releases/latest" in url:
                return self.release(rid=300 + index)
            return {"id": 100 + index, "full_name": f"o/r{index}", "owner": {"id": 200 + index}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        out = d.audit(clients, {"version": 1, "clients": {}}, api, lambda _url: "", NOW)
        self.assertEqual(out["health"]["coverage"], 0.95)
        self.assertFalse(any("below" in issue for issue in out["health"]["anomalies"]))
        self.assertTrue(any("unresolved source state unknown" in issue for issue in out["health"]["anomalies"]))

    def test_one_transient_failure_with_lkg_is_tolerated(self):
        clients = []
        for index in range(20):
            clients.append({
                "id": f"x{index}", "name": f"X{index}", "category": "mihomo",
                "platforms": {key: False for key in d.PLATFORMS},
                "source_type": "github", "github_repo": f"o/r{index}",
                "official_repo_id": 100 + index,
                "download_url": f"https://github.com/o/r{index}/releases",
            })
        previous = {
            "version": d.OBSERVATION_VERSION,
            "last_run_at": STAMP,
            "clients": {
                "x0": {
                    "source": self.scoped_source(clients[0]),
                    "release": self.scoped_release(
                        clients[0],
                        version="v1",
                        published_at="2026-09-14T00:00:00Z",
                        release_id=300,
                    ),
                }
            },
            "health": {"attempted": 20, "succeeded": 20, "coverage": 1.0, "anomalies": []},
        }
        def api(url, token=None):
            repo = url.split("/repos/")[1].split("/")[1]
            index = int(repo[1:])
            if index == 0 and "/releases/" not in url:
                raise OSError("transient")
            if "/releases/latest" in url:
                return self.release("v1", "2026-09-14T00:00:00Z", 300 + index)
            return {"id": 100 + index, "full_name": f"o/r{index}", "owner": {"id": 200 + index}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        out = d.audit(clients, previous, api, lambda _url: "", NOW)
        self.assertEqual(out["health"]["coverage"], 0.95)
        self.assertEqual(out["health"]["succeeded_last_run"], 19)
        self.assertEqual(out["health"]["anomalies"], [])
        self.assertEqual(out["clients"]["x0"]["source"]["state"], "ok")
        self.assertEqual(out["clients"]["x0"]["source"]["observation_state"], "error")

    def test_release_activity_is_derived_without_mutating_source_fact(self):
        client = {
            "id": "synthetic", "name": "Synthetic", "category": "mihomo",
            "platforms": {key: False for key in d.PLATFORMS},
            "source_type": "github", "github_repo": "o/r",
            "official_repo_id": 101,
            "download_url": "https://github.com/o/r/releases",
        }
        pushed = "2026-01-01T00:00:00Z"
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v1", "2026-09-14T12:00:00Z", 303)
            return {"id": 101, "full_name": "o/r", "owner": {"id": 202}, "archived": False, "disabled": False, "pushed_at": pushed}
        first = d.audit([client], {"version": 1, "clients": {}}, api, lambda _url: "", NOW)
        self.assertEqual(first["clients"]["synthetic"]["source"]["last_activity_at"], pushed)
        status, _ = d.activity_status(client, first["clients"]["synthetic"], NOW)
        self.assertEqual(status, "🟢")
        second = d.audit([client], first, api, lambda _url: "", NOW + dt.timedelta(hours=2))
        self.assertEqual(second, first)

    def test_discontinued_github_unarchived_first_observation_is_not_permanent_alarm(self):
        client = copy.deepcopy(self.by_id["clash-verge-legacy"])
        history = client["historical_release"]
        assets = [dict(asset, state="uploaded") for asset in history["assets"]]
        def api(url, token=None):
            if "/releases/tags/" in url:
                return self.release("v1.3.8", "2023-10-30T17:38:38Z", history["release_id"], assets)
            return self.repo(client, archived=False, pushed_at="2026-09-15T00:00:00Z")
        out = d.audit([client], {"version": d.OBSERVATION_VERSION, "clients": {}}, api, self.evidence, NOW)
        source = out["clients"][client["id"]]["source"]
        self.assertEqual(source["state"], "ok")
        self.assertNotIn("lifecycle_review_required", source)
        self.assertFalse(any("lifecycle review" in issue for issue in out["health"]["anomalies"]))

    def test_same_day_healthy_audit_is_byte_stable(self):
        client = {
            "id": "synthetic", "name": "Synthetic", "category": "mihomo",
            "platforms": {key: False for key in d.PLATFORMS},
            "source_type": "github", "github_repo": "o/r",
            "official_repo_id": 101,
            "download_url": "https://github.com/o/r/releases",
        }
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v1", "2026-09-14T00:00:00Z", 303)
            return {"id": 101, "full_name": "o/r", "owner": {"id": 202}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        first = d.audit([client], {"version": 1, "clients": {}}, api, lambda _url: "", NOW)
        second = d.audit([client], first, api, lambda _url: "", NOW + dt.timedelta(hours=2))
        self.assertEqual(second, first)

    def test_shanghai_next_day_refreshes_heartbeat_within_same_utc_day(self):
        client = {
            "id": "synthetic", "name": "Synthetic", "category": "mihomo",
            "platforms": {key: False for key in d.PLATFORMS},
            "source_type": "github", "github_repo": "o/r",
            "official_repo_id": 101,
            "download_url": "https://github.com/o/r/releases",
        }
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v1", "2026-09-14T00:00:00Z", 303)
            return {"id": 101, "full_name": "o/r", "owner": {"id": 202}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        before_midnight = dt.datetime(2026, 9, 17, 15, 30, tzinfo=dt.timezone.utc)
        after_midnight = dt.datetime(2026, 9, 17, 16, 30, tzinfo=dt.timezone.utc)
        first = d.audit([client], {"version": 1, "clients": {}}, api, lambda _url: "", before_midnight)
        second = d.audit([client], first, api, lambda _url: "", after_midnight)
        self.assertEqual(before_midnight.date(), after_midnight.date())
        self.assertNotEqual(second["last_run_at"], first["last_run_at"])
        self.assertEqual(d.display_date(d.parse_time(second["last_run_at"])), dt.date(2026, 9, 18))
        self.assertEqual(d.display_date(d.parse_time(second["clients"]["synthetic"]["source"]["last_success_at"])), dt.date(2026, 9, 18))

    def test_next_day_healthy_audit_refreshes_daily_heartbeat(self):
        client = {
            "id": "synthetic", "name": "Synthetic", "category": "mihomo",
            "platforms": {key: False for key in d.PLATFORMS},
            "source_type": "github", "github_repo": "o/r",
            "official_repo_id": 101,
            "download_url": "https://github.com/o/r/releases",
        }
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release("v1", "2026-09-14T00:00:00Z", 303)
            return {"id": 101, "full_name": "o/r", "owner": {"id": 202}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        first = d.audit([client], {"version": 1, "clients": {}}, api, lambda _url: "", NOW)
        next_day = NOW + dt.timedelta(days=1)
        second = d.audit([client], first, api, lambda _url: "", next_day)
        self.assertNotEqual(second["last_run_at"], first["last_run_at"])
        self.assertEqual(d.parse_time(second["last_run_at"]).date(), next_day.date())
        self.assertEqual(d.parse_time(second["clients"]["synthetic"]["source"]["last_success_at"]).date(), next_day.date())
        self.assertEqual(
            second["clients"]["synthetic"]["source"]["observed_at"],
            first["clients"]["synthetic"]["source"]["observed_at"],
        )

    def test_business_change_refreshes_observed_at(self):
        initial = d.positive_record(
            None,
            STAMP,
            "synthetic-scope",
            state="ok",
            version="v1",
        )
        changed_at = d.iso(NOW + dt.timedelta(hours=2))
        changed = d.positive_record(
            initial,
            changed_at,
            "synthetic-scope",
            state="ok",
            version="v2",
        )
        self.assertEqual(changed["observed_at"], changed_at)
        self.assertEqual(changed["last_success_at"], changed_at)

    def test_same_day_recovery_updates_immediately(self):
        client = {
            "id": "synthetic", "name": "Synthetic", "category": "mihomo",
            "platforms": {key: False for key in d.PLATFORMS},
            "source_type": "github", "github_repo": "o/r",
            "official_repo_id": 101,
            "download_url": "https://github.com/o/r/releases",
        }
        def healthy(url, token=None):
            if "/releases/latest" in url:
                return self.release("v1", "2026-09-14T00:00:00Z", 303)
            return {"id": 101, "full_name": "o/r", "owner": {"id": 202}, "archived": False, "disabled": False, "pushed_at": "2026-09-14T00:00:00Z"}
        first = d.audit([client], {"version": 1, "clients": {}}, healthy, lambda _url: "", NOW)
        failed = d.audit([client], first, lambda *args: (_ for _ in ()).throw(OSError("temporary")), lambda _url: "", NOW + dt.timedelta(hours=1))
        recovered_at = NOW + dt.timedelta(hours=2)
        recovered = d.audit([client], failed, healthy, lambda _url: "", recovered_at)
        self.assertEqual(recovered["clients"]["synthetic"]["source"]["observation_state"], "fresh")
        self.assertEqual(d.parse_time(recovered["clients"]["synthetic"]["source"]["last_success_at"]), recovered_at)
        self.assertEqual(d.parse_time(recovered["clients"]["synthetic"]["source"]["observed_at"]), recovered_at)
        self.assertEqual(recovered["health"]["anomalies"], [])

    def test_stale_transient_failure_is_health_anomaly(self):
        client = copy.deepcopy(self.by_id["flclash"])
        old = {
            "version": d.OBSERVATION_VERSION,
            "clients": {"flclash": {"source": self.scoped_source(
                client,
                last_success_at="2026-09-01T00:00:00Z",
                observed_at="2026-09-01T00:00:00Z",
                last_activity_at="2026-09-01T00:00:00Z",
            )}},
        }
        out = d.audit([client], old, lambda *args: (_ for _ in ()).throw(OSError("down")), self.evidence, NOW)
        self.assertTrue(any("source evidence stale" in issue for issue in out["health"]["anomalies"]))

    def test_stale_release_failure_is_independently_visible(self):
        client = copy.deepcopy(self.by_id["flclash"])
        old = {
            "version": d.OBSERVATION_VERSION,
            "clients": {
                "flclash": {
                    "source": self.scoped_source(client),
                    "release": self.scoped_release(
                        client,
                        version="v1",
                        published_at="2026-09-01T00:00:00Z",
                        last_success_at="2026-09-01T00:00:00Z",
                        observed_at="2026-09-01T00:00:00Z",
                    ),
                }
            },
        }
        def api(url, token=None):
            if "/releases/latest" in url:
                raise OSError("release endpoint down")
            return self.repo(client)
        out = d.audit([client], old, api, self.evidence, NOW)
        self.assertTrue(any("release evidence stale" in issue for issue in out["health"]["anomalies"]))

    def test_stale_core_evidence_failure_is_independently_visible(self):
        client = copy.deepcopy(self.by_id["flclash"])
        old = {
            "version": d.OBSERVATION_VERSION,
            "clients": {
                "flclash": {
                    "source": self.scoped_source(client),
                    "core_evidence": {
                        "scope": d.core_evidence_scope(client),
                        "state": "ok",
                        "observation_state": "fresh",
                        "observed_at": "2026-09-01T00:00:00Z",
                        "last_success_at": "2026-09-01T00:00:00Z",
                    },
                }
            },
        }
        def api(url, token=None):
            if "/releases/latest" in url:
                return self.release()
            return self.repo(client)
        out = d.audit([client], old, api, lambda _url: (_ for _ in ()).throw(OSError("evidence endpoint down")), NOW)
        self.assertTrue(any("core_evidence evidence stale" in issue for issue in out["health"]["anomalies"]))

    def test_persisted_identity_failure_remains_health_anomaly(self):
        client = copy.deepcopy(self.by_id["flclash"])
        old = {
            "version": d.OBSERVATION_VERSION,
            "clients": {
                "flclash": {
                    "source": self.scoped_source(
                        client,
                        state="identity_mismatch",
                        observation_state="verified_negative",
                    )
                }
            },
        }
        out = d.audit([client], old, lambda *args: (_ for _ in ()).throw(OSError("temporary outage")), self.evidence, NOW)
        self.assertTrue(any("unresolved source state identity_mismatch" in issue for issue in out["health"]["anomalies"]))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clients = d.load_clients(ROOT / "data" / "clients.json")
        cls.by_id = {client["id"]: client for client in cls.clients}

    def base_observations(self):
        records = {}
        for client in self.clients:
            if client["source_type"] == "manual":
                continue
            state = "archived" if client["id"] == "clash-verge-legacy" else "ok"
            record = {
                "source": {
                    "scope": d.source_scope(client),
                    "state": state,
                    "observation_state": "fresh",
                    "observed_at": STAMP,
                    "last_success_at": STAMP,
                    "last_activity_at": "2026-09-14T00:00:00Z",
                    "consecutive_failures": 0,
                }
            }
            if client.get("historical_release"):
                record["historical_release"] = {
                    "scope": d.historical_release_scope(client),
                    "state": "ok",
                    "observation_state": "fresh",
                    "observed_at": STAMP,
                    "last_success_at": STAMP,
                    "tag": client["historical_release"]["tag"],
                    "release_id": client["historical_release"]["release_id"],
                    "consecutive_failures": 0,
                }
            elif client.get("release_source", client["source_type"]) in {"github", "app_store"}:
                record["release"] = {
                    "scope": d.release_scope(client),
                    "state": "ok",
                    "observation_state": "fresh",
                    "observed_at": STAMP,
                    "last_success_at": STAMP,
                    "version": "v1",
                    "published_at": "2026-09-14T00:00:00Z",
                    "consecutive_failures": 0,
                }
            if client.get("core_evidence") or client.get("app_store_core_patterns"):
                record["core_evidence"] = {
                    "scope": d.core_evidence_scope(client),
                    "state": "ok",
                    "observation_state": "fresh",
                    "observed_at": STAMP,
                    "last_success_at": STAMP,
                    "consecutive_failures": 0,
                }
            records[client["id"]] = record
        return {
            "version": d.OBSERVATION_VERSION,
            "last_run_at": STAMP,
            "clients": records,
            "health": {"attempted": 0, "succeeded": 0, "coverage": 1.0, "anomalies": []},
        }

    def test_confirmed_bad_source_is_not_clickable(self):
        observations = self.base_observations()
        observations["clients"]["flclash"]["source"]["state"] = "identity_mismatch"
        text = d.render_readme(self.clients, observations, NOW)
        row = next(line for line in text.splitlines() if line.startswith("| FlClash |"))
        self.assertIn("| — | — |", row)

    def test_transient_failure_keeps_configured_links_even_when_stale(self):
        observations = self.base_observations()
        source = observations["clients"]["flclash"]["source"]
        source["observation_state"] = "error"
        source["consecutive_failures"] = 20
        source["last_success_at"] = "2026-09-01T00:00:00Z"
        text = d.render_readme(self.clients, observations, NOW)
        row = next(line for line in text.splitlines() if line.startswith("| FlClash |"))
        self.assertIn("| ❓ |", row)
        self.assertIn("[官方仓库](https://github.com/chen08209/FlClash)", row)
        self.assertIn("[下载页](https://github.com/chen08209/FlClash/releases)", row)

    def test_confirmed_missing_source_keeps_configured_links_but_marks_pending(self):
        observations = self.base_observations()
        source = observations["clients"]["flclash"]["source"]
        source["state"] = "missing"
        source["observation_state"] = "verified_negative"
        text = d.render_readme(self.clients, observations, NOW)
        row = next(line for line in text.splitlines() if line.startswith("| FlClash |"))
        self.assertIn("| ❓ |", row)
        self.assertIn("[官方仓库](https://github.com/chen08209/FlClash)", row)
        self.assertIn("[下载页](https://github.com/chen08209/FlClash/releases)", row)

    def test_archived_active_entry_renders_in_history_with_last_release(self):
        observations = self.base_observations()
        observations["clients"]["flclash"]["source"]["state"] = "archived"
        text = d.render_readme(self.clients, observations, NOW)
        history = text.split("## 历史项目\n", 1)[1].split("\n## 项目详情", 1)[0]
        self.assertIn("| FlClash | 🔴 |", history)
        detail = text.split("### FlClash\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn("- 分类：历史项目", detail)
        self.assertIn("- 最后版本：", detail)
        self.assertIn("已自动归入历史项目", detail)

    def test_clash_verge_original_release_remains_preferred(self):
        observations = self.base_observations()
        text = d.render_readme(self.clients, observations, NOW)
        row = next(line for line in text.splitlines() if line.startswith("| Clash Verge |"))
        self.assertIn("zzzgydi/clash-verge", row)
        self.assertNotIn("clashbk/Clash_Verge", row)

    def test_unverified_legacy_mirror_not_main_download(self):
        observations = self.base_observations()
        text = d.render_readme(self.clients, observations, NOW)
        row = next(line for line in text.splitlines() if line.startswith("| Clash for Windows |"))
        self.assertTrue(row.rstrip().endswith("| — | — |"))
        detail = text.split("### Clash for Windows\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn("- 第三方下载：", detail)
        self.assertIn("第三方下载仅作历史资料，未与原官方版本核对。", text)
        self.assertNotIn("第三方历史资料", text)

    def test_evidence_freshness_ages_without_network(self):
        observations = self.base_observations()
        source = observations["clients"]["flclash"]["source"]
        source["last_success_at"] = "2026-09-01T00:00:00Z"
        status, reason = d.activity_status(self.by_id["flclash"], observations["clients"]["flclash"], NOW)
        self.assertEqual(status, "❓")
        self.assertIn("7 天", reason)

    def test_activity_age_is_derived_not_cached(self):
        observations = self.base_observations()
        source = observations["clients"]["flclash"]["source"]
        source["last_success_at"] = STAMP
        source["last_activity_at"] = "2026-02-01T00:00:00Z"
        observations["clients"]["flclash"]["release"]["published_at"] = "2026-02-01T00:00:00Z"
        status, _ = d.activity_status(self.by_id["flclash"], observations["clients"]["flclash"], NOW)
        self.assertEqual(status, "🟡")

    def test_long_inactive_status_uses_clock_not_orange(self):
        observations = self.base_observations()
        record = observations["clients"]["flclash"]
        record["source"]["last_activity_at"] = "2025-01-01T00:00:00Z"
        record["release"]["published_at"] = "2025-01-01T00:00:00Z"
        status, _ = d.activity_status(self.by_id["flclash"], record, NOW)
        self.assertEqual(status, "🕒")

    def test_render_is_deterministic(self):
        observations = self.base_observations()
        self.assertEqual(d.render_readme(self.clients, observations, NOW), d.render_readme(self.clients, observations, NOW))

    def test_status_display_is_cross_platform_clear(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        self.assertNotIn("🟠", text)
        self.assertNotIn("⚪", text)
        self.assertIn("🟢 近半年有更新", text)
        self.assertIn("🟡 最近更新距今半年至一年", text)
        self.assertIn("🕒 最近更新距今一年以上", text)
        self.assertIn("❓ 待确认", text)
        flclash_row = next(line for line in text.splitlines() if line.startswith("| FlClash |"))
        self.assertIn("| 🟢 |", flclash_row)
        self.assertNotIn("🟢 近半年有更新", flclash_row)

    def test_detail_sections_use_consistent_fields_without_freeform_notes(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        hako = text.split("### Clash（Hako）\n", 1)[1].split("\n### ", 1)[0]
        for label in ("分类", "状态", "平台", "内核", "官方来源", "下载", "版本"):
            self.assertIn(f"- {label}：", hako)
        self.assertNotIn("- 说明：", hako)
        self.assertNotIn("- 核验：", hako)
        self.assertIn("- 平台：macOS / iOS / tvOS", hako)
        self.assertIn("- 内核：Hako（基于 Mihomo）", hako)
        self.assertNotIn("- 备注：", text)
        self.assertNotIn("- 最低系统：", text)
        self.assertNotIn("- 定位/兼容性：", text)
        self.assertNotIn("- 来源说明：", text)
        self.assertNotIn("Surge for iOS", text)
        self.assertIn("## Mihomo / Clash 内核客户端", text)
        self.assertIn("## sing-box 内核客户端", text)
        self.assertIn("## 多内核客户端", text)
        self.assertIn("## 闭源客户端", text)
        self.assertIn("| Stash |", text)
        self.assertNotIn("| AnyPortal |", text)
        self.assertNotIn("| Nyx |", text)
        self.assertNotIn("## Clash 配置兼容客户端", text)
        self.assertIn("| 官方来源 |", text)
        self.assertIn("来源类型不等同于开源许可证", text)
        stash_row = next(line for line in text.splitlines() if line.startswith("| Stash |"))
        self.assertIn("[官网](https://stash.ws/)", stash_row)
        self.assertIn("[下载页](https://stash.ws/download)", stash_row)
        flclash_row = next(line for line in text.splitlines() if line.startswith("| FlClash |"))
        self.assertIn("[官方仓库](https://github.com/chen08209/FlClash)", flclash_row)
        legacy = text.split("### ClashX Pro\n", 1)[1].split("\n### ", 1)[0]
        for label in ("分类", "状态", "平台", "说明", "第三方下载"):
            self.assertIn(f"- {label}：", legacy)
        self.assertNotIn("- 内核：", legacy)
        self.assertNotIn("- 最后版本：", legacy)
        self.assertNotIn("- 原官方来源：", legacy)
        self.assertNotIn("- 原官方下载：", legacy)
        self.assertNotIn("- 核验：", legacy)
        self.assertNotIn("🟡 最近更新距今半年至一年｜最近更新距今半年至一年", text)
        self.assertNotIn("🔴 历史项目｜历史项目", text)
        self.assertNotIn("在用项目按主要内核或实现方式分类", text)
        self.assertNotIn("多内核统一写为", text)

    def test_legacy_table_labels_are_consistent(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        verge = next(line for line in text.splitlines() if line.startswith("| Clash Verge |"))
        clashn = next(line for line in text.splitlines() if line.startswith("| ClashN |"))
        self.assertIn("[原官方仓库]", verge)
        self.assertIn("[原官方下载页]", verge)
        self.assertIn("[原项目]", clashn)
        self.assertTrue(clashn.rstrip().endswith("| — |"))

    def test_detail_sections_keep_bare_urls(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        section = text.split("### Clash Verge\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn("- 原官方来源：", section)
        self.assertIn("[https://github.com/zzzgydi/clash-verge](https://github.com/zzzgydi/clash-verge)", section)
        self.assertIn("[https://github.com/zzzgydi/clash-verge/releases/tag/v1.3.8](https://github.com/zzzgydi/clash-verge/releases/tag/v1.3.8)", section)

    def test_readme_avoids_maintenance_jargon(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        for jargon in ("`LKG`", "`scope`", "`pin`", "canonical `", " unverified ", " failover", "lookup", "报警"):
            self.assertNotIn(jargon, text)

    def test_multicore_table_stays_compact_and_details_expose_supported_cores(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        section = text.split("## 多内核客户端\n", 1)[1].split("\n## 闭源客户端", 1)[0]
        self.assertIn("| 客户端 | 状态 | macOS |", section)
        self.assertNotIn("| 客户端 | 状态 | 内核 |", section)
        self.assertNotIn("Mihomo / Clash Premium / Clash Rust / Meow", section)
        self.assertNotIn("Mihomo / Smart Core", section)
        self.assertNotIn("Xray / v2fly / Mihomo / sing-box 等", section)
        details = text.split("## 项目详情\n", 1)[1]
        self.assertIn("- 内核：多内核（Mihomo / Clash Premium / Clash Rust / Meow）", details)
        self.assertIn("- 内核：多内核（Mihomo / Smart Core）", details)
        self.assertIn("- 内核：多内核（Xray / v2fly / Mihomo / sing-box 等）", details)

    def test_readme_omits_noop_verification_but_exposes_evidence_age(self):
        text = d.render_readme(self.clients, self.base_observations(), NOW)
        self.assertIn("核验：最近成功日期", text)
        self.assertNotIn("- 核验：无异常。", text)
        self.assertNotIn("- 核验：不适用。", text)

    def test_evidence_summary_includes_release_and_core_freshness(self):
        client = self.by_id["flclash"]
        observations = {
            "clients": {
                "flclash": {
                    "source": {"scope": d.source_scope(client), "last_success_at": "2026-09-15T01:00:00Z"},
                    "release": {"scope": d.release_scope(client), "last_success_at": "2026-09-10T01:00:00Z"},
                    "core_evidence": {"scope": d.core_evidence_scope(client), "last_success_at": "2026-09-14T01:00:00Z"},
                }
            }
        }
        summary = d.evidence_summary([client], observations, NOW)
        self.assertIn("成功记录 2026-09-10 至 2026-09-15", summary)
        self.assertIn("2026-09-15", summary)

    def test_evidence_summary_uses_shanghai_calendar_date(self):
        client = self.by_id["flclash"]
        stamp = "2026-09-17T18:17:00Z"
        observations = {
            "clients": {
                "flclash": {
                    "source": {"scope": d.source_scope(client), "last_success_at": stamp},
                    "release": {"scope": d.release_scope(client), "last_success_at": stamp},
                    "core_evidence": {"scope": d.core_evidence_scope(client), "last_success_at": stamp},
                }
            }
        }
        now = dt.datetime(2026, 9, 18, 0, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(
            d.evidence_summary([client], observations, now),
            "核验：最近成功日期 2026-09-18（北京时间，下同）。",
        )

    def test_release_and_warning_dates_use_shanghai_calendar_date(self):
        observations = self.base_observations()
        release = observations["clients"]["flclash"]["release"]
        release["version"] = "v-local-date"
        release["published_at"] = "2026-09-14T18:00:00Z"
        text = d.render_readme(self.clients, observations, NOW)
        section = text.split("### FlClash", 1)[1].split("### ", 1)[0]
        self.assertIn("- 版本：v-local-date（2026-09-15）", section)
        warning = d.component_warning(
            "版本",
            {"state": "ok", "observation_state": "error", "last_success_at": "2026-09-14T18:00:00Z"},
        )
        self.assertIn("最近成功核验 2026-09-15", warning)

    def test_readme_surfaces_release_and_core_anomalies(self):
        observations = self.base_observations()
        record = observations["clients"]["flclash"]
        record["release"] = {
            "state": "rollback", "version": "v1", "published_at": "2026-09-01",
            "observation_state": "fresh", "last_success_at": STAMP,
        }
        record["core_evidence"] = {
            "state": "mismatch", "observation_state": "fresh", "last_success_at": STAMP,
        }
        text = d.render_readme(self.clients, observations, NOW)
        section = text.split("### FlClash", 1)[1].split("### ", 1)[0]
        self.assertIn("版本时间早于已确认记录", section)
        self.assertIn("内核说明发生变化", section)

    def test_health_gate_rederives_instead_of_trusting_cached_anomalies(self):
        observations = self.base_observations()
        observations["health"]["anomalies"] = ["stale cached anomaly"]
        d.health_check(self.clients, observations, NOW)

    def test_health_gate_detects_current_stale_evidence_even_if_cached_green(self):
        observations = self.base_observations()
        observations["health"]["anomalies"] = []
        observations["clients"]["flclash"]["release"]["last_success_at"] = "2026-09-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "release evidence stale"):
            d.health_check(self.clients, observations, NOW)

    def test_atomic_write_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text("old\n", encoding="utf-8")
            with patch.object(d.os, "replace", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    d.atomic_write_text(path, "new\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")


class WorkflowTests(unittest.TestCase):
    def test_validate_requires_generated_readme_check(self):
        text = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/build_readme.py --check", text)
        self.assertIn("scripts/check_commit_identity.py", text)

    def test_refresh_publishes_safe_state_before_health_gate(self):
        text = (ROOT / ".github/workflows/refresh-directory.yml").read_text(encoding="utf-8")
        push = "git push origin HEAD:main"
        self.assertLess(text.index("--audit"), text.index(push))
        self.assertGreater(text.index("--health-check"), text.index(push))

    def test_refresh_uses_short_lived_github_token_for_writes(self):
        text = (ROOT / ".github/workflows/refresh-directory.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: write", text)
        self.assertNotIn("environment:", text)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", text)
        self.assertIn("persist-credentials: true", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("REFRESH_DEPLOY_KEY", text)
        self.assertNotIn("git fetch --no-tags", text)
        self.assertNotIn("merge-base --is-ancestor", text)
        self.assertNotIn("ssh.github.com", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("http.https://github.com/.extraheader", text)
        self.assertNotIn("GH_TOKEN:", text)

    def test_checkout_is_pinned_and_credentials_are_scoped_by_workflow(self):
        expected = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        validate = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        refresh = (ROOT / ".github/workflows/refresh-directory.yml").read_text(encoding="utf-8")
        self.assertIn(expected, validate)
        self.assertIn(expected, refresh)
        self.assertIn("persist-credentials: false", validate)
        self.assertIn("persist-credentials: true", refresh)

    def test_refresh_never_force_pushes(self):
        text = (ROOT / ".github/workflows/refresh-directory.yml").read_text(encoding="utf-8")
        self.assertNotIn("--force", text)
        self.assertNotIn("push -f", text)


if __name__ == "__main__":
    unittest.main()

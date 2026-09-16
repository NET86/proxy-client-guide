import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_readme as d

NOW = dt.datetime(2026, 9, 16, 0, 0, tzinfo=dt.timezone.utc)


class CatalogIntegrationTests(unittest.TestCase):
    def test_real_catalog_manual_sources_have_no_main_download_and_no_dynamic_component_contract(self):
        clients = d.load_clients(ROOT / "data" / "clients.json")
        manual = [client for client in clients if client["source_type"] == "manual"]
        self.assertTrue(manual)
        for client in manual:
            self.assertEqual(client.get("download_url", ""), "", client["id"])
            self.assertEqual(d.expected_observation_components(client), (), client["id"])
            repository, download = d.links_for(client, {}, NOW)
            self.assertEqual(download, "", client["id"])
            self.assertEqual(repository, client.get("repository_url", ""), client["id"])

    def test_v1_snapshot_migration_is_fail_closed_across_real_catalog(self):
        clients = d.load_clients(ROOT / "data" / "clients.json")
        fake_v1 = {
            "version": 1,
            "clients": {
                client["id"]: {
                    "source": {"state": "ok", "last_success_at": "2026-09-15T00:00:00Z"}
                }
                for client in clients
                if client["source_type"] != "manual"
            },
            "health": {"coverage": 1.0, "anomalies": []},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "observations.json"
            path.write_text(json.dumps(fake_v1), encoding="utf-8")
            migrated = d.load_observations(path)
        self.assertEqual(migrated["version"], d.OBSERVATION_VERSION)
        self.assertEqual(migrated["clients"], {})
        health = d.derive_health(clients, migrated, NOW)
        self.assertEqual(health["coverage"], 0.0)
        rendered = d.render_readme(clients, migrated, NOW)
        self.assertIn("核验：暂无成功记录。", rendered)
        for client in clients:
            if client["source_type"] == "manual":
                continue
            _, download = d.links_for(client, {}, NOW)
            self.assertEqual(download, "", client["id"])


if __name__ == "__main__":
    unittest.main()

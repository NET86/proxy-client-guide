"""Synthetic HTTP fixtures; no external requests or production data writes."""
import argparse
import datetime as dt
import http.client
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_directory as fixtures
from test_directory import NOW, STAMP, d


class ResponseIsolationTests(unittest.TestCase):
    @staticmethod
    def response(wire):
        class Socket:
            def makefile(self, mode):
                return io.BytesIO(wire)
        response = http.client.HTTPResponse(Socket())
        response.begin()
        return response

    @classmethod
    def text_response(cls, body=b"Mihomo", charset="utf-8"):
        return cls.response(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=" + charset.encode() + b"\r\nConnection: close\r\n\r\n" + body)

    @classmethod
    def json_response(cls, payload):
        return cls.text_response(json.dumps(payload).encode())

    @staticmethod
    def bad_wire(kind):
        return {
            "header": b"HTTP/1.1 200 OK\r\nX-Test: " + b"x" * 70000 + b"\r\n\r\n",
            "status": b"not an HTTP status\r\n\r\n",
            "status-length": b"HTTP/1.1 200 " + b"x" * 70000 + b"\r\n\r\n",
            "chunk-length": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"1" * 70000 + b"\r\n",
            "chunk-invalid": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nnot-hex\r\n",
        }[kind]

    def test_unknown_and_non_text_charsets_become_component_errors(self):
        for charset in ("unknown-fixture-charset", "base64_codec", "hex_codec", "rot_13"):
            with self.subTest(charset=charset):
                response = self.text_response(charset=charset)
                with patch.object(d.urllib.request, "urlopen", return_value=response) as opener:
                    with self.assertRaises(OSError):
                        d.request_text("https://example.invalid/text")
                self.assertTrue(response.closed)
                opener.assert_called_once()

    def test_real_malformed_http_responses_become_component_errors(self):
        for kind in ("header", "status", "status-length", "chunk-length", "chunk-invalid"):
            for request in (d.request_json, d.request_text):
                with self.subTest(kind=kind, request=request.__name__):
                    opened = []
                    def opener(*args, **kwargs):
                        response = self.response(self.bad_wire(kind))
                        opened.append(response)
                        return response
                    with patch.object(d.urllib.request, "urlopen", side_effect=opener) as urlopen:
                        with self.assertRaises(OSError):
                            request("https://example.invalid/response")
                    urlopen.assert_called_once()
                    self.assertTrue(all(response.closed for response in opened))

    def test_programming_errors_are_not_normalized(self):
        for request in (d.request_json, d.request_text):
            for error in (AssertionError("bug"), TypeError("bug"), LookupError("unrelated bug")):
                with self.subTest(request=request.__name__, error=type(error).__name__):
                    with patch.object(d.urllib.request, "urlopen", side_effect=error):
                        with self.assertRaises(type(error)):
                            request("https://example.invalid/response")

    def test_text_charset_success_still_decodes_and_replaces_invalid_bytes(self):
        for body, charset, expected in ((b"caf\xe9", "iso-8859-1", "caf\u00e9"), (b"x\xff", "utf-8", "x\ufffd")):
            with self.subTest(charset=charset):
                response = self.text_response(body, charset)
                with patch.object(d.urllib.request, "urlopen", return_value=response):
                    self.assertEqual(d.request_text("https://example.invalid/text"), expected)
                self.assertTrue(response.closed)

    def test_mixed_clients_persist_success_failure_coverage_and_identity_conflict(self):
        by_id = {client["id"]: client for client in d.load_clients()}
        good, bad, conflict = [by_id[cid] for cid in ("flclash", "clashx-meta", "clash-verge-rev")]
        clients = [good, bad, conflict]
        later = NOW + dt.timedelta(hours=1)
        helpers = fixtures.AuditTests()
        old = {"version": d.OBSERVATION_VERSION, "clients": {}}
        for client in clients:
            record = {component: d.positive_record(None, STAMP, d.component_scope(client, component), state="ok") for component in d.expected_observation_components(client)}
            record["source"].update(repo_id=client["official_repo_id"], full_name=client["github_repo"], last_activity_at=STAMP)
            record["release"].update(version="v1", release_id=1, published_at=STAMP, asset_count=1)
            old["clients"][client["id"]] = record
        for mode in ("charset-unknown", "charset-non-text", "header", "status", "status-length", "chunk-length", "json-header", "json-status", "json-chunk-length"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                observations = home / "observations.json"
                output = home / "README.md"
                d.write_json(observations, old)
                def opener(request, **kwargs):
                    url = request.full_url
                    for client in clients:
                        repo_url = f"https://api.github.com/repos/{client['github_repo']}"
                        if url == repo_url or url == f"https://api.github.com/repositories/{client['official_repo_id']}":
                            if client is bad and mode.startswith("json-"):
                                return self.response(self.bad_wire(mode.removeprefix("json-")))
                            return self.json_response(helpers.repo(client, id=-1 if client is conflict else client["official_repo_id"], pushed_at=d.iso(later)))
                        if url == repo_url + "/releases/latest":
                            return self.json_response(helpers.release("v2", d.iso(later), rid=2))
                        if url in {item["url"] for item in client.get("core_evidence", [])}:
                            if client is bad and not mode.startswith("json-"):
                                if mode.startswith("charset-"):
                                    return self.text_response(charset="base64_codec" if mode.endswith("non-text") else "unknown-fixture-charset")
                                return self.response(self.bad_wire(mode))
                            return self.text_response(helpers.evidence(url).encode())
                    raise AssertionError(f"Unexpected fixture URL: {url}")
                args = argparse.Namespace(catalog=home / "clients.json", observations=observations, output=output, audit=True, check=False, health_check=False)
                with patch.object(d, "parse_args", return_value=args), patch.object(d, "load_clients", return_value=clients), patch.object(d, "utc_now", return_value=later), patch.object(d.urllib.request, "urlopen", side_effect=opener):
                    self.assertEqual(d.main(), 0)
                saved = json.loads(observations.read_text(encoding="utf-8"))
                self.assertTrue(output.exists())
                self.assertEqual(saved["clients"][good["id"]]["release"]["version"], "v2")
                self.assertEqual(saved["clients"][good["id"]]["source"]["last_success_at"], d.iso(later))
                failed_component = "source" if mode.startswith("json-") else "core_evidence"
                failed = saved["clients"][bad["id"]][failed_component]
                self.assertEqual(failed["observation_state"], "error")
                self.assertEqual(failed["state"], "ok")
                self.assertEqual(failed["last_success_at"], STAMP)
                self.assertEqual(failed["consecutive_failures"], 1)
                self.assertEqual(failed["scope"], old["clients"][bad["id"]][failed_component]["scope"])
                if failed_component == "core_evidence":
                    self.assertEqual(saved["clients"][bad["id"]]["release"]["version"], "v2")
                conflicted = saved["clients"][conflict["id"]]
                self.assertEqual(conflicted["source"]["state"], "identity_mismatch")
                self.assertEqual(d.links_for(conflict, conflicted, later)[1], None)
                self.assertEqual(saved["health"]["attempted_last_run"], 3)
                self.assertEqual(saved["health"]["succeeded_last_run"], 1)
                self.assertEqual(saved["health"]["coverage"], 0.3333)
                self.assertTrue(any("identity_mismatch" in issue for issue in saved["health"]["anomalies"]))
                with self.assertRaisesRegex(ValueError, "Health anomalies"):
                    d.health_check(clients, saved, later)
                self.assertEqual(old["clients"][good["id"]]["release"]["version"], "v1")

    def test_hako_discovery_failure_preserves_completed_app_store_components(self):
        client = next(client for client in d.load_clients() if client["id"] == "hako")
        entry = {"trackId": int(client["app_store_id"]), "sellerName": client["app_store_seller"], "version": "v2", "currentVersionReleaseDate": STAMP, "description": "Hako builds on the open-source mihomo project"}
        for mode in ("charset", "header", "status", "chunk-length"):
            with self.subTest(mode=mode):
                def opener(request, **kwargs):
                    if request.full_url.startswith("https://itunes.apple.com/"):
                        return self.json_response({"resultCount": 1, "results": [entry]})
                    self.assertEqual(request.full_url, "https://clash.md/")
                    if mode == "charset":
                        return self.text_response(charset="unknown-fixture-charset")
                    return self.response(self.bad_wire(mode))
                with patch.object(d.urllib.request, "urlopen", side_effect=opener):
                    out = d.audit([client], {"clients": {}}, now=NOW)
                record = out["clients"][client["id"]]
                for component in ("source", "release", "core_evidence"):
                    self.assertEqual(record[component]["observation_state"], "fresh")
                self.assertEqual(record["github_discovery"]["observation_state"], "error")
                self.assertEqual(record["release"]["version"], "v2")

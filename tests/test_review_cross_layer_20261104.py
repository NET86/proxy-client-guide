"""Review-only probes spanning real HTTP dispatch and evidence transitions."""
import concurrent.futures
import copy
import socket
import threading
import unittest
from unittest.mock import patch

import sitecustomize
import test_directory as directory
import test_github_rediscovery as rediscovery
from test_http_receive_deadline import TrickleSocket

d = directory.d


class WireSocket(TrickleSocket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent = bytearray()

    def sendall(self, data):
        self.sent.extend(data)


class CrossLayerReviewTests(unittest.TestCase):
    def run_chain(self, wires, *, seconds=15.0, requests=4, tick=0.0, attempts=1):
        clock, sockets = [0.0], []

        def connect(connection):
            self.assertLess(len(sockets), len(wires), "unexpected physical dispatch")
            sock = WireSocket(wires[len(sockets)], clock, tick)
            sock.settimeout(connection.timeout)
            sockets.append(sock)
            connection.sock = sock

        with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(d.time, "sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             patch.object(d.urllib.request, "getproxies", return_value={}), \
             patch.object(d.urllib.request.AbstractHTTPHandler, "do_open", sitecustomize.STDLIB_HTTP_DO_OPEN), \
             patch.object(socket, "socket", side_effect=AssertionError("live socket forbidden")), \
             patch.object(d.http.client.HTTPSConnection, "connect", connect):
            budget = d.DiscoveryBudget(requests, seconds)
            try:
                result = d.request_json("https://api.github.com/start", token="fixture-token",
                                        budget=budget, attempts=attempts)
                error = None
            except (d.DiscoveryBudgetExceeded, d.ObservationError, OSError) as exc:
                result, error = None, exc
        self.assertTrue(all(s.closed and all(f.closed for f in s.files) for s in sockets))
        return result, error, clock[0], sockets, budget

    @staticmethod
    def redirect(target="https://official.example/final"):
        return (b"HTTP/1.1 302 Found\r\nLocation: " + target.encode()
                + b"\r\nContent-Length: 999999999\r\n\r\n")

    @staticmethod
    def success():
        return b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}'

    def test_real_redirect_discards_body_and_does_not_forward_credentials(self):
        head = self.redirect()
        result, error, _, sockets, budget = self.run_chain([head + b"unbounded redirect body", self.success()])
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(error)
        self.assertEqual(sockets[0].position, len(head))
        self.assertIn(b"Authorization: Bearer fixture-token", sockets[0].sent)
        self.assertNotIn(b"Authorization", sockets[1].sent)
        self.assertEqual(budget.attempts, 2)

    def test_deadline_is_shared_across_actual_redirect_header_parsers(self):
        head = self.redirect()
        seconds = (len(head) + 10) * 0.01
        _, error, elapsed, sockets, budget = self.run_chain(
            [head, self.success()], tick=0.01, seconds=seconds)
        self.assertIsNotNone(error)
        self.assertEqual(len(sockets), 2)
        self.assertEqual(budget.attempts, 2)
        self.assertLessEqual(elapsed, seconds + 0.001)
        self.assertLessEqual(sockets[1].timeouts[0], 0.101)

    def test_real_redirect_request_cap_prevents_second_connection(self):
        _, error, _, sockets, _ = self.run_chain([self.redirect()], requests=1)
        self.assertIsInstance(error, d.DiscoveryBudgetExceeded)
        self.assertEqual(error.reason, "request_budget_exceeded")
        self.assertEqual(len(sockets), 1)

    def test_downgrade_and_transient_retry_keep_transport_guards(self):
        _, error, _, sockets, _ = self.run_chain([self.redirect("http://official.example/plain")])
        self.assertIsInstance(error, d.ObservationError)
        self.assertEqual(len(sockets), 1)
        retry = b"HTTP/1.1 503 Unavailable\r\nContent-Length: 999999999\r\n\r\n"
        result, error, _, sockets, budget = self.run_chain([retry, self.success()], attempts=2)
        self.assertIsNone(error)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(budget.attempts, 2)
        self.assertEqual(sockets[0].position, len(retry))

    def test_simultaneous_openers_do_not_share_deadlines(self):
        local = threading.local()
        barrier = threading.Barrier(2)

        def connect(connection):
            local.sock = WireSocket(self.success(), local.clock, 0.01)
            local.sock.settimeout(connection.timeout)
            connection.sock = local.sock
            barrier.wait(timeout=10)

        def worker(seconds):
            local.clock = [0.0]
            budget = d.DiscoveryBudget(1, seconds)
            try:
                result = d.request_json("https://official.example/source", budget=budget, attempts=1)
                failed = False
            except (d.DiscoveryBudgetExceeded, OSError):
                result, failed = None, True
            return result, failed, local.clock[0], local.sock.closed, all(f.closed for f in local.sock.files)

        with patch.object(d.time, "monotonic", side_effect=lambda: getattr(local, "clock", [0.0])[0]), \
             patch.object(d.urllib.request, "getproxies", return_value={}), \
             patch.object(d.urllib.request.AbstractHTTPHandler, "do_open", sitecustomize.STDLIB_HTTP_DO_OPEN), \
             patch.object(socket, "socket", side_effect=AssertionError("live socket forbidden")), \
             patch.object(d.http.client.HTTPSConnection, "connect", connect):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                short = executor.submit(worker, 0.05)
                long = executor.submit(worker, 5.0)
                short_result, long_result = short.result(), long.result()
        self.assertTrue(short_result[1])
        self.assertLessEqual(short_result[2], 0.051)
        self.assertEqual(long_result[:2], ({"ok": True}, False))
        self.assertTrue(all(short_result[3:] + long_result[3:]))

    def test_rediscovery_confirmation_rejects_changed_id_or_owner(self):
        client = next(c for c in d.load_clients() if c["id"] == "karing")
        name = "KaringX/karing-next"
        for change in ({"id": 990002}, {"owner": {"id": 999999}}):
            with self.subTest(change=change):
                count = 0

                def api(url, token=None):
                    nonlocal count
                    if url in (f"https://api.github.com/repos/{client['github_repo']}",
                               f"https://api.github.com/repositories/{client['official_repo_id']}"):
                        return None
                    self.assertEqual(url, f"https://api.github.com/repos/{name}")
                    count += 1
                    metadata = rediscovery.GithubRediscoveryTests.repo(990001, 131734194, name, "Karing proxy")
                    return metadata if count == 1 else {**metadata, **change}

                out, _, ok = d.audit_github(client, {}, None, rediscovery.NOW, api,
                                            lambda url: f"https://github.com/{name}")
                self.assertFalse(ok)
                self.assertFalse(out["source"].get("rediscovered_repo"))
                self.assertNotIn(name, d.links_for(client, out, rediscovery.NOW)[0])

    def test_app_store_lookup_failure_does_not_renew_auxiliary_success(self):
        client = next(c for c in d.load_clients() if c["id"] == "hako")
        stamp = d.iso(directory.NOW)
        old = {
            key: d.positive_record(None, stamp, d.component_scope(client, key), state="ok")
            for key in ("source", "release", "core_evidence")
        }
        baseline = copy.deepcopy(old)

        def unavailable(*args):
            raise OSError("fixture lookup outage")

        out, _, ok = d.audit_app_store_source(client, old, directory.NOW, unavailable)
        self.assertFalse(ok)
        self.assertEqual(old, baseline)
        self.assertEqual(out["core_evidence"], old["core_evidence"])
        self.assertEqual(out["source"]["observation_state"], "error")
        self.assertEqual(d.activity_status(client, out, directory.NOW)[0], "❓")


if __name__ == "__main__":
    unittest.main()

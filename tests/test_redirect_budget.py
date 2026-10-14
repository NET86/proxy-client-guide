"""Exercise standard urllib redirects with mocked physical HTTPS dispatch."""
from email.message import Message
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_readme as d


class Response(io.BytesIO):
    def __init__(self, url, *, location=None):
        super().__init__(b'{"ok":true}')
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"
        self.code = self.status = 302 if location else 200
        self.msg = "Found" if location else "OK"
        if location:
            self.headers["Location"] = location

    def info(self):
        return self.headers


class RedirectBudgetTests(unittest.TestCase):
    def exercise(self, max_requests, outcomes, *, seconds=10.0, elapsed=None,
                 method=None, attempts=1):
        clock, dispatched, responses = [0.0], [], []
        outcomes = iter(outcomes)
        elapsed = iter(elapsed or [])
        def dispatch(handler, request):
            dispatched.append((request.full_url, request.timeout,
                               dict(request.headers), dict(request.unredirected_hdrs)))
            clock[0] += next(elapsed, 0.0)
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            response = Response(request.full_url, location=outcome)
            responses.append(response)
            return response
        def sleep(delay):
            clock[0] += delay
        with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(d.time, "sleep", side_effect=sleep), \
             patch.object(d.urllib.request.HTTPSHandler, "https_open", dispatch):
            budget = d.DiscoveryBudget(max_requests, seconds)
            try:
                if method is d.request_text:
                    result = method("https://example.invalid/start", budget=budget, attempts=attempts)
                else:
                    result = d.request_json("https://api.github.com/start", token="fixture-token",
                                            budget=budget, attempts=attempts)
                error = None
            except d.DiscoveryBudgetExceeded as exc:
                result, error = None, exc.reason
        self.assertTrue(all(response.closed for response in responses))
        return result, error, dispatched, budget

    def test_budget_one_prevents_second_physical_request(self):
        for method in (d.request_json, d.request_text):
            with self.subTest(method=method.__name__):
                result, error, calls, budget = self.exercise(1, ["https://example.invalid/end"], method=method)
                self.assertIsNone(result)
                self.assertEqual(error, "request_budget_exceeded")
                self.assertEqual(len(calls), 1)
                self.assertEqual(budget.attempts, 1)

    def test_budget_two_finishes_one_redirect_and_keeps_auth_unredirected(self):
        result, error, calls, budget = self.exercise(2, ["https://example.invalid/end", None])
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(error)
        self.assertEqual(budget.attempts, 2)
        self.assertIn("Authorization", calls[0][3])
        self.assertNotIn("Authorization", calls[1][2])
        self.assertNotIn("Authorization", calls[1][3])

    def test_multihop_exhaustion_prevents_third_request(self):
        _, error, calls, budget = self.exercise(2, ["https://example.invalid/two", "https://example.invalid/three"])
        self.assertEqual(error, "request_budget_exceeded")
        self.assertEqual(len(calls), 2)
        self.assertEqual(budget.attempts, 2)

    def test_elapsed_deadline_prevents_next_hop(self):
        _, error, calls, budget = self.exercise(4, ["https://example.invalid/two", "https://example.invalid/three"],
                                               seconds=1.0, elapsed=[0.6, 0.5])
        self.assertEqual(error, "time_budget_exceeded")
        self.assertEqual(len(calls), 2)
        self.assertEqual(budget.attempts, 2)
        self.assertAlmostEqual(calls[1][1], 0.4)

    def test_per_hop_timeout_never_exceeds_tiny_remaining_deadline(self):
        result, error, calls, _ = self.exercise(2, ["https://example.invalid/end", None],
                                               seconds=0.04, elapsed=[0.03])
        self.assertIsNone(error)
        self.assertEqual(result, {"ok": True})
        self.assertAlmostEqual(calls[0][1], 0.04)
        self.assertAlmostEqual(calls[1][1], 0.01)

    def test_retry_and_redirect_share_request_budget(self):
        _, error, calls, budget = self.exercise(2, [URLError("temporary"), "https://example.invalid/end"], attempts=2)
        self.assertEqual(error, "request_budget_exceeded")
        self.assertEqual(len(calls), 2)
        self.assertEqual(budget.attempts, 2)

    def test_retry_redirect_chain_can_finish_with_enough_budget(self):
        result, error, calls, budget = self.exercise(3, [URLError("temporary"), "https://example.invalid/end", None], attempts=2)
        self.assertIsNone(error)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(budget.attempts, 3)


if __name__ == "__main__":
    unittest.main()

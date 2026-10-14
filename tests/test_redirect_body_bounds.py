"""Exercise real urllib redirect processing without real network access."""
from email.message import Message
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_readme as d


class Response(io.BytesIO):
    def __init__(self, url, code=200, location=None, on_close=None):
        super().__init__(b'{"ok":true}')
        self.url = url
        self.code = self.status = code
        self.msg = "synthetic"
        self.headers = Message()
        if location is not None:
            self.headers["Location"] = location
        self.on_close = on_close
        self.reads = 0

    def info(self):
        return self.headers

    def read(self, *args, **kwargs):
        self.reads += 1
        if self.code != 200:
            raise AssertionError("redirect response body must never be consumed")
        return super().read(*args, **kwargs)

    def close(self):
        if not self.closed and self.on_close is not None:
            self.on_close()
        super().close()


class RedirectBodyBoundsTests(unittest.TestCase):
    def test_without_budget_skips_redirect_body_and_rejects_downgrade(self):
        for target in ("https://other.invalid/end", "http://other.invalid/end",
                       "ftp://other.invalid/end"):
            with self.subTest(target=target):
                responses = []

                def dispatch(handler, connection, request, **kwargs):
                    response = Response(request.full_url, 302, target) if not responses else Response(request.full_url)
                    responses.append(response)
                    return response

                # Exercise the old no-budget standard opener in memory too.
                # All transports stay mocked; no live URL is requested.
                def standard_open(request, *, timeout):
                    return d.urllib.request.build_opener().open(request, timeout=timeout)

                with patch.object(d.urllib.request, "urlopen", new=standard_open), patch.object(
                    d.urllib.request.AbstractHTTPHandler, "do_open", new=dispatch
                ), patch.object(d.urllib.request.FTPHandler, "ftp_open",
                                side_effect=AssertionError("FTP dispatch")):
                    if target.startswith("https:"):
                        self.assertEqual(d.request_json("https://example.invalid/start"), {"ok": True})
                        self.assertEqual(len(responses), 2)
                    else:
                        with self.assertRaises(d.ObservationError):
                            d.request_json("https://example.invalid/start")
                        self.assertEqual(len(responses), 1)
                self.assertTrue(all(response.closed for response in responses))
                self.assertEqual(responses[0].reads, 0)

    def test_all_redirect_codes_skip_body_close_before_dispatch_and_strip_token(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                responses = []
                requests = []

                def dispatch(handler, connection, request, **kwargs):
                    if responses:
                        self.assertTrue(responses[-1].closed)
                    requests.append(request)
                    self.assertGreater(request.timeout, 0)
                    self.assertLessEqual(request.timeout, 10)
                    if len(requests) == 1:
                        response = Response(request.full_url, code, "https://other.invalid/next")
                    elif len(requests) == 2:
                        response = Response(request.full_url, code, "https://api.github.com/end")
                    else:
                        response = Response(request.full_url)
                    responses.append(response)
                    return response

                with patch.object(d.time, "monotonic", return_value=0), patch.object(
                    d.urllib.request.AbstractHTTPHandler, "do_open", new=dispatch
                ):
                    budget = d.DiscoveryBudget(3, 10)
                    self.assertEqual(d.request_json("https://api.github.com/start", token="synthetic",
                                                   budget=budget), {"ok": True})
                self.assertEqual(budget.attempts, 3)
                self.assertEqual(requests[0].get_header("Authorization"), "Bearer synthetic")
                self.assertTrue(all(r.get_header("Authorization") is None for r in requests[1:]))
                self.assertTrue(all(r.closed for r in responses))
                self.assertEqual([r.reads for r in responses[:2]], [0, 0])

    def test_exhausted_request_or_time_budget_closes_without_next_dispatch(self):
        for mode in ("requests", "deadline"):
            with self.subTest(mode=mode):
                clock = [0.0]
                responses = []

                def dispatch(handler, connection, request, **kwargs):
                    def closed():
                        if mode == "deadline":
                            clock[0] = 11.0
                    response = Response(request.full_url, 302, "/next", on_close=closed)
                    responses.append(response)
                    return response

                with patch.object(d.time, "monotonic", side_effect=lambda: clock[0]), patch.object(
                    d.urllib.request.AbstractHTTPHandler, "do_open", new=dispatch
                ):
                    budget = d.DiscoveryBudget(1 if mode == "requests" else 3, 10)
                    with self.assertRaises(d.DiscoveryBudgetExceeded) as raised:
                        d.request_json("https://example.invalid/start", budget=budget)
                self.assertEqual(raised.exception.reason, "request_budget_exceeded" if mode == "requests"
                                 else "time_budget_exceeded")
                self.assertEqual(len(responses), 1)
                self.assertTrue(responses[0].closed)
                self.assertEqual(responses[0].reads, 0)

    def test_non_https_redirect_is_rejected_before_any_second_transport(self):
        for target in ("http://example.invalid/end", "ftp://example.invalid/end"):
            with self.subTest(target=target):
                response = Response("https://example.invalid/start", 302, target)
                with patch.object(d.urllib.request.AbstractHTTPHandler, "do_open", return_value=response) as opened, \
                     patch.object(d.urllib.request.FTPHandler, "ftp_open", side_effect=AssertionError("FTP dispatch")) as ftp:
                    with self.assertRaises(d.ObservationError):
                        d.request_json(response.url, budget=d.DiscoveryBudget(3, 10))
                self.assertEqual(opened.call_count, 1)
                ftp.assert_not_called()
                self.assertTrue(response.closed)
                self.assertEqual(response.reads, 0)

    def test_invalid_redirects_and_loops_close_every_response(self):
        for target in (None, "file:///not-used", "/start"):
            with self.subTest(target=target):
                responses = []

                def dispatch(handler, connection, request, **kwargs):
                    response = Response(request.full_url, 302, target)
                    responses.append(response)
                    return response

                with patch.object(d.urllib.request.AbstractHTTPHandler, "do_open", new=dispatch):
                    expected = d.ObservationError if target == "file:///not-used" else HTTPError
                    with self.assertRaises(expected):
                        d.request_json("https://example.invalid/start", budget=d.DiscoveryBudget(20, 10))
                self.assertTrue(responses)
                self.assertTrue(all(r.closed and r.reads == 0 for r in responses))

    def test_next_hop_failure_keeps_redirect_response_closed(self):
        response = Response("https://example.invalid/start", 302, "/next")
        with patch.object(d.urllib.request.AbstractHTTPHandler, "do_open",
                          side_effect=[response, URLError("synthetic failure")]) as opened:
            with self.assertRaises(URLError):
                d.request_json(response.url, attempts=1, budget=d.DiscoveryBudget(2, 10))
        self.assertEqual(opened.call_count, 2)
        self.assertTrue(response.closed)
        self.assertEqual(response.reads, 0)


if __name__ == "__main__":
    unittest.main()

"""Deny accidental live network access in unit tests.

Tests that exercise HTTP behavior must explicitly patch urllib.request.urlopen.
Set NET86_ALLOW_LIVE_TEST_NETWORK=1 only for a separately designated live test job.
"""
import os
import urllib.request

if os.environ.get("NET86_ALLOW_LIVE_TEST_NETWORK") != "1":
    def _deny_live_network(request, *args, **kwargs):
        url = getattr(request, "full_url", request)
        raise AssertionError(f"unexpected live network access in unit test: {url}")
    urllib.request.urlopen = _deny_live_network

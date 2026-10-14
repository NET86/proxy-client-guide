"""Independent second-review regression tests; no live service or shared oracle."""
import copy
import datetime as dt
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_readme as d

NOW = dt.datetime(2026, 10, 7, 0, 0, tzinfo=dt.timezone.utc)
COMMIT = 'a' * 40
TEXT = 'github.com/metacubex/mihomo/'


class ReviewIdentityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = next(c for c in d.load_clients() if c['id'] == 'flclash')
        self.base = 'https://api.github.com/repos/' + self.client['github_repo']
        self.calls = []

    def repo(self, name=None, rid=None):
        return {'id': rid if rid is not None else self.client['official_repo_id'],
                'full_name': name or self.client['github_repo'], 'owner': {'id': 181477152},
                'archived': False, 'disabled': False, 'pushed_at': '2026-10-06T00:00:00Z'}

    def release(self):
        return {'id': 9000, 'tag_name': 'v2', 'published_at': '2026-10-06T00:00:00Z',
                'draft': False, 'prerelease': False,
                'assets': [{'id': 90001, 'name': 'client.bin', 'size': 100, 'state': 'uploaded'}]}

    def api(self, url, token=None):
        self.calls.append(url)
        if url == self.base:
            return self.repo()
        if url == self.base + '/releases/latest':
            return self.release()
        if url == self.base + '/commits/main':
            return {'sha': COMMIT}
        self.fail('Unexpected API URL: ' + url)

    def test_known_identity_change_hides_links_including_cached_success(self):
        old, _, ok = d.audit_github(self.client, {}, None, NOW, self.api, lambda _: TEXT)
        self.assertTrue(ok)
        released = False

        def changed(url, token=None):
            nonlocal released
            if url == self.base + '/releases/latest':
                released = True
                return self.release()
            if url == self.base and released:
                return self.repo(rid=self.client['official_repo_id'] + 1)
            return self.api(url, token)

        out, _, ok = d.audit_github(self.client, old, None, NOW, changed, lambda _: TEXT)
        self.assertFalse(ok)
        self.assertEqual(out['source']['state'], 'identity_mismatch')
        self.assertEqual(d.links_for(self.client, out, NOW), ('', ''))

    def test_pinned_id_fallback_still_checks_the_path_used_for_content(self):
        canonical = 'trusted/renamed-client'
        fetched = []

        def changed(url, token=None):
            if url == self.base:
                return self.repo(rid=self.client['official_repo_id'] + 1)
            if url == 'https://api.github.com/repositories/' + str(self.client['official_repo_id']):
                return self.repo(name=canonical)
            if url == 'https://api.github.com/repos/' + canonical:
                return self.repo(name=canonical, rid=self.client['official_repo_id'] + 2)
            if url.endswith('/releases/latest'):
                return self.release()
            self.fail('Unexpected API URL: ' + url)

        out, _, ok = d.audit_github(self.client, {}, None, NOW, changed,
                                    lambda url: fetched.append(url) or TEXT)
        self.assertFalse(ok)
        self.assertEqual(out['source']['state'], 'identity_mismatch')
        self.assertEqual(d.links_for(self.client, out, NOW), ('', ''))
        self.assertEqual(fetched, [])

    def test_raw_core_evidence_is_fetched_at_immutable_commit_and_recorded(self):
        fetched = []
        out, _, ok = d.audit_github(self.client, {}, None, NOW, self.api,
                                    lambda url: fetched.append(url) or TEXT)
        self.assertTrue(ok)
        pinned = self.client['core_evidence'][0]['url'].replace('/main/', '/' + COMMIT + '/')
        self.assertEqual(fetched, [pinned])
        self.assertEqual(out['core_evidence']['evidence_snapshots'], [
            {'url': pinned, 'sha256': hashlib.sha256(TEXT.encode()).hexdigest()}])

    def test_current_flclash_config_evidence_recovers_old_missing_record(self):
        old_client = copy.deepcopy(self.client)
        old_client['core_evidence'][0]['url'] = (
            'https://raw.githubusercontent.com/chen08209/FlClash/main/core/common.go')
        old, _, ok = d.audit_github(old_client, {}, None, NOW, self.api, lambda _: None)
        self.assertFalse(ok)
        self.assertEqual(old['core_evidence']['state'], 'missing')
        with self.assertRaisesRegex(ValueError, 'core_evidence'):
            d.health_check([old_client], {'clients': {'flclash': old}}, NOW)

        pinned = f'https://raw.githubusercontent.com/chen08209/FlClash/{COMMIT}/core/config.go'
        content = 'package core\nimport "github.com/metacubex/mihomo/config"\n'
        fetched = []

        def fetch(url):
            fetched.append(url)
            return content if url == pinned else None

        refreshed_at = NOW + dt.timedelta(hours=1)
        out, issues, ok = d.audit_github(self.client, old, None, refreshed_at, self.api, fetch)
        self.assertTrue(ok, issues)
        self.assertEqual(fetched, [pinned])
        self.assertEqual(out['core_evidence']['state'], 'ok')
        self.assertEqual(out['core_evidence']['scope'], d.core_evidence_scope(self.client))
        self.assertEqual(out['core_evidence']['last_success_at'], d.iso(refreshed_at))
        self.assertEqual(out['core_evidence']['evidence_snapshots'], [
            {'url': pinned, 'sha256': hashlib.sha256(content.encode()).hexdigest()}])
        observations = {'clients': {'flclash': out}}
        d.health_check([self.client], observations, refreshed_at)
        self.assertIn('| FlClash | 🟢 |', d.render_readme([self.client], observations, refreshed_at))

    def test_current_flclash_config_evidence_still_requires_mihomo_import(self):
        for content, state in ((None, 'missing'), ('package core\nimport "example.com/other"\n', 'mismatch')):
            with self.subTest(state=state):
                out, _, ok = d.audit_github(self.client, {}, None, NOW, self.api, lambda _: content)
                self.assertFalse(ok)
                self.assertEqual(out['core_evidence']['state'], state)
                with self.assertRaisesRegex(ValueError, 'core_evidence'):
                    d.health_check([self.client], {'clients': {'flclash': out}}, NOW)

    def test_invalid_commit_response_never_falls_back_to_mutable_branch(self):
        for response in (None, {}, {'sha': 'main'}, {'sha': '../foreign'}, {'sha': 'z' * 40}):
            fetched = []

            def api(url, token=None):
                if '/commits/' in url:
                    return response
                return self.api(url, token)

            with self.subTest(response=response):
                out, _, ok = d.audit_github(self.client, {}, None, NOW, api,
                                            lambda url: fetched.append(url) or TEXT)
                self.assertFalse(ok)
                self.assertEqual(fetched, [])
                self.assertEqual(out['core_evidence']['observation_state'], 'error')

    def test_one_ref_is_resolved_once_for_multiple_evidence_files(self):
        client = copy.deepcopy(self.client)
        client['core_evidence'].append(dict(client['core_evidence'][0],
                                          url=client['core_evidence'][0]['url'].rsplit('/', 1)[0] + '/second.go'))
        out, _, ok = d.audit_github(client, {}, None, NOW, self.api, lambda _: TEXT)
        self.assertTrue(ok)
        self.assertEqual(self.calls.count(self.base + '/commits/main'), 1)
        self.assertEqual(len(out['core_evidence']['evidence_snapshots']), 2)
        self.assertEqual(len({s['url'] for s in out['core_evidence']['evidence_snapshots']}), 2)

    def test_wiki_evidence_records_digest_without_claiming_main_repo_commit(self):
        client = copy.deepcopy(self.client)
        wiki = 'https://raw.githubusercontent.com/wiki/' + client['github_repo'] + '/Cores.md'
        client['core_evidence'] = [{'url': wiki, 'patterns': ['mihomo']}]
        out, _, ok = d.audit_github(client, {}, None, NOW, self.api, lambda _: TEXT)
        self.assertTrue(ok)
        self.assertFalse(any('/commits/' in url for url in self.calls))
        self.assertEqual(out['core_evidence']['evidence_snapshots'], [
            {'url': wiki, 'sha256': hashlib.sha256(TEXT.encode()).hexdigest()}])


if __name__ == '__main__':
    unittest.main()

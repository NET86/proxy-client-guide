"""Confirmed rebuilt repositories retain lifecycle without relaxing first claims."""
import copy
import datetime as dt
import unittest
import test_github_rediscovery as fixtures

NOW, d = fixtures.NOW, fixtures.d


class ConfirmedRebuildLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.client = copy.deepcopy(next(c for c in d.load_clients() if c['id'] == 'karing'))
        self.base = 'https://api.github.com/repos/' + self.client['github_repo']
        self.rid = 990001
        self.archived = False
        self.disabled = False
        self.owner = 131734194
        self.description = 'Karing proxy utility'
        self.metadata_name = self.client['github_repo']
        self.missing = False

    def api(self, url, token=None):
        if url == 'https://api.github.com/repositories/' + str(self.client['official_repo_id']):
            return None
        if url == self.base:
            if self.missing:
                return None
            return dict(fixtures.GithubRediscoveryTests.repo(self.rid, self.owner, self.metadata_name, self.description),
                        archived=self.archived, disabled=self.disabled)
        if url == self.base + '/releases/latest':
            return fixtures.GithubRediscoveryTests.release()
        if '/commits/' in url:
            return {'sha': 'a' * 40}
        self.fail('Unexpected API URL: ' + url)

    def text(self, url):
        if url == 'https://karing.app/faq':
            return 'Official GitHub: https://github.com/' + self.client['github_repo']
        if 'raw.githubusercontent.com/KaringX/karing/' in url:
            return 'Built-in modified sing-box core'
        self.fail('Unexpected text URL: ' + url)

    def audit(self, old, day=0):
        return d.audit_github(self.client, old, None, NOW + dt.timedelta(days=day), self.api, self.text)

    def confirmed(self):
        out, issues, ok = self.audit({})
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        self.assertTrue(out['source']['rediscovered_repo'])
        return out

    def test_confirmed_rebuild_can_archive_keep_history_on_404_and_unarchive(self):
        active = self.confirmed()
        before = copy.deepcopy(active)
        self.archived = True
        archived, _, ok = self.audit(active, 1)
        self.assertTrue(ok)
        self.assertEqual(active, before)
        self.assertEqual(archived['source']['repo_id'], self.rid)
        self.assertEqual(archived['source']['state'], 'archived')
        self.assertEqual(d.effective_category(self.client, archived), 'legacy')
        self.assertEqual(d.activity_status(self.client, archived, NOW + dt.timedelta(days=1))[0], '🔴')
        d.health_check([self.client], {'clients': {self.client['id']: archived}}, NOW + dt.timedelta(days=1))
        self.missing = True
        missing, _, ok = self.audit(archived, 2)
        self.assertFalse(ok)
        self.assertEqual(d.effective_category(self.client, missing), 'legacy')
        self.assertEqual(d.activity_status(self.client, missing, NOW + dt.timedelta(days=2))[0], '❓')
        with self.assertRaises(ValueError):
            d.health_check([self.client], {'clients': {self.client['id']: missing}}, NOW + dt.timedelta(days=2))
        self.missing = False
        self.archived = False
        restored, _, ok = self.audit(missing, 3)
        self.assertTrue(ok)
        self.assertEqual(d.effective_category(self.client, restored), self.client['category'])

    def test_first_claim_of_inactive_repository_remains_rejected(self):
        known = self.confirmed()
        for archived, disabled in [(True, False), (False, True), (True, True)]:
            for old in [{}, {'github_discovery': known['github_discovery']}]:
                with self.subTest(archived=archived, disabled=disabled, source=bool(old)):
                    self.archived, self.disabled = archived, disabled
                    out, _, ok = self.audit(old, 1)
                    self.assertFalse(ok)
                    self.assertEqual(out['github_discovery']['candidates'][0]['reason'], 'inactive')

    def test_archive_exception_requires_confirmed_same_scope_and_repository_id(self):
        known = self.confirmed()
        variants = []
        for field, value in [('repo_id', self.rid + 1), ('scope', 'wrong-scope'),
                             ('rediscovered_repo', False), ('last_success_at', None)]:
            old = copy.deepcopy(known)
            old['source'][field] = value
            variants.append(old)
        self.archived = True
        for old in variants:
            with self.subTest(source=old['source']):
                out, _, ok = self.audit(old, 1)
                self.assertFalse(ok)
                self.assertEqual(out['github_discovery']['state'], 'candidate')

    def test_confirmation_does_not_bypass_current_project_trust_or_disabled_check(self):
        known = self.confirmed()
        for owner, description, disabled, name in [(99999, 'Karing proxy utility', False, self.client['github_repo']),
                  (131734194, 'Unrelated client', False, 'KaringX/unrelated'),
                  (131734194, 'Karing proxy utility', True, self.client['github_repo'])]:
            with self.subTest(owner=owner, description=description, disabled=disabled):
                self.archived = True
                self.owner, self.description, self.disabled = owner, description, disabled
                self.metadata_name = name
                out, _, ok = self.audit(known, 1)
                self.assertFalse(ok)
                self.assertEqual(out['github_discovery']['state'], 'candidate')


if __name__ == '__main__':
    unittest.main()

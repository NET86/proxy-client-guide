"""Unavailable App Store lookups must not erase independently scoped evidence."""
import copy
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_readme as directory


class AppStoreEvidenceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.client = next(c for c in directory.load_clients()
                           if c['source_type'] == 'app_store' and c.get('app_store_core_patterns')
                           and c.get('github_discovery'))
        self.now = dt.datetime(2026, 10, 9, 2, tzinfo=dt.timezone.utc)
        self.stamp = directory.iso(self.now - dt.timedelta(days=1))
        self.old = {
            'source': directory.positive_record({}, self.stamp, directory.source_scope(self.client),
                state='ok', seller=self.client['app_store_seller'], track_id=int(self.client['app_store_id'])),
            'release': directory.positive_record({}, self.stamp, directory.release_scope(self.client),
                state='ok', version='1.0', published_at=self.stamp),
            'core_evidence': directory.positive_record({}, self.stamp, directory.core_evidence_scope(self.client), state='ok'),
            'github_discovery': directory.positive_record({}, self.stamp, directory.github_discovery_scope(self.client),
                state='verified', repo_id=123, owner_id=456, full_name='example/project'),
        }
        self.before = copy.deepcopy(self.old)

    @staticmethod
    def unavailable(*args, **kwargs):
        raise OSError('temporary lookup failure')

    def assert_evidence_preserved(self, record):
        for component in ('core_evidence', 'github_discovery'):
            self.assertIn(component, record)
            self.assertEqual(record[component], self.before[component])
        self.assertEqual(self.old, self.before)

    def test_lookup_failure_keeps_auxiliary_evidence_without_renewing_it(self):
        record, _, ok = directory.audit_app_store_source(self.client, self.old, self.now, self.unavailable)
        self.assertFalse(ok)
        self.assert_evidence_preserved(record)
        self.assertEqual(record['source']['observation_state'], 'error')
        self.assertEqual(record['release']['last_success_at'], self.stamp)

    def test_missing_store_region_keeps_auxiliary_evidence(self):
        record, _, ok = directory.audit_app_store_source(
            self.client, self.old, self.now, lambda *args: {'resultCount': 0, 'results': []})
        self.assertFalse(ok)
        self.assert_evidence_preserved(record)
        self.assertEqual(record['source']['observation_state'], 'unverified')

    def test_identity_conflict_is_still_recorded_and_never_healed_by_retention(self):
        payload = {'resultCount': 1, 'results': [{'trackId': int(self.client['app_store_id']),
            'sellerName': 'Different publisher', 'version': '1.0', 'currentVersionReleaseDate': self.stamp}]}
        record, _, ok = directory.audit_app_store_source(self.client, self.old, self.now, lambda *args: payload)
        self.assertFalse(ok)
        self.assert_evidence_preserved(record)
        self.assertEqual(record['source']['state'], 'identity_mismatch')
        self.assertEqual(record['release']['state'], 'identity_mismatch')

    def test_batch_snapshot_keeps_evidence_but_reports_failed_observation(self):
        snapshot = directory.empty_observations()
        snapshot['clients'][self.client['id']] = copy.deepcopy(self.old)
        result = directory.audit([self.client], snapshot, request=self.unavailable, now=self.now)
        self.assert_evidence_preserved(result['clients'][self.client['id']])
        self.assertEqual(result['health']['succeeded_last_run'], 0)
        self.assertEqual(result['health']['coverage'], 0.0)
        self.assertEqual(snapshot['clients'][self.client['id']], self.before)

    def test_changed_catalog_scope_does_not_inherit_old_trust(self):
        changed = copy.deepcopy(self.client)
        changed['app_store_seller'] = 'Reviewed replacement publisher'
        record, _, ok = directory.audit_app_store_source(changed, self.old, self.now, self.unavailable)
        self.assertFalse(ok)
        self.assertFalse(directory.component_positive(changed, record, 'core_evidence'))
        self.assertFalse(directory.component_fresh(changed, record, 'github_discovery', self.now))


if __name__ == '__main__':
    unittest.main()

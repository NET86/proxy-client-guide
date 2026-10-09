"""Beijing display dates work without system IANA data or third-party packages."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StandardLibraryClockTests(unittest.TestCase):
    def test_import_and_midnight_boundary_without_any_timezone_database(self):
        code = '''
import datetime as dt
import sys
import zoneinfo
zoneinfo.reset_tzpath(())
sys.path.insert(0, 'scripts')
import build_readme as d
assert d.DISPLAY_TIMEZONE.utcoffset(None) == dt.timedelta(hours=8)
assert str(d.display_date(dt.datetime(2026, 12, 31, 15, 59, tzinfo=dt.timezone.utc))) == '2026-12-31'
assert str(d.display_date(dt.datetime(2026, 12, 31, 16, 0, tzinfo=dt.timezone.utc))) == '2027-01-01'
assert not any(name.startswith('tzdata') for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-S', '-B', '-c', code], cwd=ROOT,
            env=dict(os.environ, PYTHONTZPATH='', PYTHONUTF8='1'), capture_output=True,
            text=True, encoding='utf-8', timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

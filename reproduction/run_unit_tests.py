"""Run upstream tests offline with a workspace-local temporary directory."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'ChinaTravel'
sys.path.insert(0, str(REPO))
os.chdir(REPO)
scratch = REPO / 'artifacts' / 'test_tmp'
scratch.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(scratch)
with (ROOT / 'reproduction' / 'unit_tests.log').open('w', encoding='utf-8') as log:
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        suite = unittest.defaultTestLoader.discover(str(REPO / 'tests'), pattern='test_*.py')
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
summary = {
    'tests_run': result.testsRun, 'failures': len(result.failures),
    'errors': len(result.errors), 'skipped': len(result.skipped),
    'success': result.wasSuccessful(), 'offline': True,
}
(ROOT / 'reproduction' / 'unit_test_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary))
sys.exit(0 if result.wasSuccessful() else 1)

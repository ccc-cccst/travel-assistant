"""Check the actual Git index without printing credential contents."""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'possible_model_api_key': re.compile(rb'\b(?:sk|ark)-[A-Za-z0-9_-]{20,}'),
    'possible_github_token': re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}'),
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def main():
    top = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, text=True).strip()
    if Path(top).resolve() != ROOT.resolve():
        raise SystemExit('This directory is not the Git repository root.')
    records = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    total = 0
    files = 0
    for record in records:
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, blob, stage = metadata.split()
        name = raw_path.decode('utf-8')
        path = PurePosixPath(name)
        files += 1
        if stage != b'0' or mode not in (b'100644', b'100755'):
            failures.append((name, 'unexpected Git entry; possible conflict, symlink, or embedded repository'))
            continue
        if any(part in ('.git', '.venv', '__pycache__', 'node_modules') for part in path.parts):
            failures.append((name, 'environment or repository metadata'))
        if path.parts[0] in ('datasets', 'tmp') or name.startswith('reproduction/runs/'):
            failures.append((name, 'local data or raw experiment records'))
        if path.name.startswith('.env') and path.name != '.env.example':
            failures.append((name, 'local environment file'))
        if path.suffix.lower() in ('.pem', '.key', '.log') or path.name == 'translation_api_config.json':
            failures.append((name, 'local credential or log file'))
        data = subprocess.check_output(['git', 'cat-file', 'blob', blob.decode('ascii')], cwd=ROOT)
        total += len(data)
        if len(data) > 10 * 1024 * 1024:
            failures.append((name, 'file exceeds this project upload limit of 10 MiB'))
        for kind, pattern in PATTERNS.items():
            if pattern.search(data):
                failures.append((name, kind))
    for path, reason in failures:
        print(f'BLOCKED: {path}: {reason}')
    print(f'Checked {files} staged files, {total} bytes; findings: {len(failures)}.')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())

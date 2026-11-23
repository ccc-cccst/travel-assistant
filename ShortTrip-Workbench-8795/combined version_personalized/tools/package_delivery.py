"""Build the portable P042 source and data distribution from the development checkout."""
import argparse
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT.parent / 'ShortTrip'
DATA = BASELINE / 'artifacts/data'
SNAPSHOTS = ('5e0ee8a', '6de12f4')
TITLE = 'ShortTrip-P042'
PRIVATE_TOKEN = re.compile(rb'(?:sk-[a-zA-Z0-9_-]{18,}|gh[pousr]_[a-zA-Z0-9]{32,}|'
                           rb'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----)')


def entries():
    # Select source and public installed profiles explicitly. User jobs and logs live elsewhere.
    for name in ('personalize.py', 'requirements.txt', 'Run-Personalized.ps1', 'ARCHITECTURE.md',
                 'LESSONS.md', 'BASELINE_MANIFEST.json'):
        yield ROOT / name, Path(name)
    yield ROOT / 'docs/同学交付说明.md', Path('README.md')
    yield BASELINE / 'LICENSE', Path('LICENSE')
    summary = Path('result/experiments/P042_search_budget/paired_release/SUMMARY.json')
    yield ROOT / summary, summary
    trees = [(ROOT / group, Path(group)) for group in
             ('chinatravel', 'web', 'lessons', 'examples', 'experiments', 'docs', 'tools', 'tests')]
    trees.append((BASELINE / 'chinatravel/planning', Path('chinatravel/planning')))
    trees.extend((DATA / version, Path('artifacts/data') / version) for version in SNAPSHOTS)
    for folder, prefix in trees:
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        for source in sorted(folder.rglob('*')):
            relative = source.relative_to(folder)
            if not source.is_file() or '__pycache__' in relative.parts or source.suffix == '.pyc':
                continue
            if source.name == Path(__file__).name and folder == ROOT / 'tools':
                continue
            if ('.git' in relative.parts or '.venv' in relative.parts or
                    source.name.startswith('.env') and source.name != '.env.example'):
                raise ValueError(f'Unexpected private file in selected tree: {relative}')
            yield source, prefix / relative


def check_content(path):
    # Scan in chunks, also across chunk boundaries, without logging possible secret values.
    overlap = b''
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            data = overlap + block
            if PRIVATE_TOKEN.search(data):
                raise ValueError(f'Possible credential in {path.name}; archive not published')
            overlap = data[-128:]


def build(out):
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as temp:
        archive = Path(temp) / 'delivery.zip'
        seen = set()
        with ZipFile(archive, 'x', ZIP_DEFLATED, compresslevel=6) as bundle:
            for source, relative in entries():
                name = f'{TITLE}/{relative.as_posix()}'
                if name.casefold() in seen:
                    raise ValueError(f'Duplicate output path: {name}')
                seen.add(name.casefold())
                check_content(source)
                bundle.write(source, name)
        with ZipFile(archive) as bundle:
            corrupt = bundle.testzip()
            if corrupt:
                raise ValueError(f'ZIP CRC failed: {corrupt}')
        archive.rename(out)
    print(f'{out}: {len(seen)} files, {out.stat().st_size:,} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output.resolve())

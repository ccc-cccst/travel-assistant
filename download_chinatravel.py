"""Download pinned official ChinaTravel datasets and verify their contents."""
import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'datasets'


def fetch(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'ChinaTravel-reproduction-download'})
            with urllib.request.urlopen(request, timeout=35) as response:
                return response.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def download_repo(name):
    repo = 'LAMDA-NeSy/' + name
    upstream = json.loads((ROOT / 'UPSTREAM.json').read_text(encoding='utf-8'))
    revision = upstream['datasets'][name]['revision']
    listing = 'https://huggingface.co/api/datasets/' + repo + '/tree/' + revision + '?recursive=true&limit=1000'
    files = [entry for entry in json.loads(fetch(listing)) if entry['type'] == 'file']
    if len(files) >= 1000:
        raise RuntimeError('File list requires pagination')
    destination = DATA / name

    def download(entry):
        relative = entry['path']
        target = destination / relative
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError(relative)
        endpoint = 'resolve' if entry.get('lfs') else 'raw'
        url = 'https://huggingface.co/datasets/' + repo + '/' + endpoint + '/' + revision + '/' + urllib.parse.quote(relative, safe='/')
        download_source = 'existing verified local file'
        if target.exists():
            content = target.read_bytes()
        else:
            if name == 'ChinaTravel-Sandbox' and entry.get('lfs'):
                mirror = 'https://modelscope.cn/datasets/Cbphcr/' + name + '/resolve/master/' + urllib.parse.quote(relative, safe='/')
                try:
                    content = fetch(mirror)
                    download_source = mirror
                except Exception:
                    content = fetch(url)
                    download_source = url
            else:
                content = fetch(url)
                download_source = url
        if len(content) != entry['size']:
            raise ValueError('Size mismatch: ' + relative)
        sha256 = hashlib.sha256(content).hexdigest()
        if entry.get('lfs'):
            if sha256 != entry['lfs']['oid']:
                raise ValueError('SHA256 mismatch: ' + relative)
        elif entry.get('oid'):
            blob = b'blob ' + str(len(content)).encode() + b'\0' + content
            if hashlib.sha1(blob).hexdigest() != entry['oid']:
                raise ValueError('Git blob mismatch: ' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print('Verified ' + name + '/' + relative, flush=True)
        return {'path': relative, 'size': len(content), 'sha256': sha256, 'download_source': download_source}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(download, files))
    return {'repo': repo, 'revision': revision, 'files': records}


def extract_sandbox():
    environment = ROOT / 'ChinaTravel' / 'chinatravel' / 'environment'
    counts = {}
    for lang, folder in [('zh', 'database'), ('en', 'database_en')]:
        archive = DATA / 'ChinaTravel-Sandbox' / 'raw' / ('ChinaTravel_sandbox_' + lang + '.zip')
        with zipfile.ZipFile(archive) as zf:
            if zf.testzip() is not None:
                raise ValueError('Corrupt archive: ' + str(archive))
            for member in zf.infolist():
                target = environment / member.filename
                if not target.resolve().is_relative_to((environment / folder).resolve()):
                    raise ValueError('Unexpected archive member: ' + member.filename)
                if target.exists() and not member.is_dir():
                    if target.read_bytes() != zf.read(member):
                        raise FileExistsError('Refusing to overwrite different file: ' + str(target))
            zf.extractall(environment)
            counts[lang] = sum(not item.is_dir() for item in zf.infolist())
    return counts


def main():
    manifest = {'downloaded_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'datasets': []}
    for name in ['ChinaTravel', 'ChinaTravel-Sandbox']:
        manifest['datasets'].append(download_repo(name))
    manifest['sandbox_extracted_files'] = extract_sandbox()
    manifest['code_url'] = 'https://github.com/LAMDA-NeSy/ChinaTravel'
    manifest['code_commit'] = json.loads((ROOT / 'UPSTREAM.json').read_text(encoding='utf-8'))['commit']
    (ROOT / 'reproduction' / 'download_run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Download and extraction complete.', flush=True)


if __name__ == '__main__':
    main()

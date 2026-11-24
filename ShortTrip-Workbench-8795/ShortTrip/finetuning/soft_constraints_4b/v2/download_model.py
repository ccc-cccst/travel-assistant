"""Download a pinned official Qwen checkpoint through the accessible HF mirror."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.update(HF_HOME=str(ROOT/'cache/huggingface'), HF_ENDPOINT='https://hf-mirror.com',
                  HF_HUB_DISABLE_XET='1', HF_HUB_DOWNLOAD_TIMEOUT='60', HF_HUB_DISABLE_PROGRESS_BARS='1')
REVISION = '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'

def main():
    from huggingface_hub import HfApi, snapshot_download
    import requests
    manifest_path=ROOT/'model_manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['revision']!=REVISION:
            raise ValueError('Pinned model revision changed')
    else:
        info = HfApi(endpoint=os.environ['HF_ENDPOINT']).model_info('Qwen/Qwen3.5-4B', revision=REVISION, files_metadata=True)
        selected = [file for file in info.siblings if file.rfilename.endswith(('.json','.safetensors','.jinja')) or file.rfilename in ('merges.txt','vocab.json','LICENSE')]
        manifest = dict(model='Qwen/Qwen3.5-4B', revision=REVISION, endpoint=os.environ['HF_ENDPOINT'],
            files=[dict(name=file.rfilename,size=file.size,sha256=getattr(file.lfs,'sha256',None)) for file in selected])
        manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    pending=[file['name'] for file in manifest['files'] if not (ROOT/'base_model'/file['name']).exists() or (ROOT/'base_model'/file['name']).stat().st_size!=file['size']]
    # Fetch text/tokenizer files without the mirror's optional Brotli decoder path.
    for file in manifest['files']:
        if file['name'] not in pending or file['name'].endswith('.safetensors'):
            continue
        response=requests.get(f'https://hf-mirror.com/Qwen/Qwen3.5-4B/raw/{REVISION}/{file["name"]}',
            headers={'Accept-Encoding':'identity'},timeout=(15,60))
        response.raise_for_status()
        if len(response.content)!=file['size']:
            raise ValueError('Size mismatch '+file['name'])
        temporary=ROOT/'base_model'/(file['name']+'.tmp')
        temporary.parent.mkdir(parents=True,exist_ok=True)
        temporary.write_bytes(response.content)
        temporary.replace(ROOT/'base_model'/file['name'])
    pending=[name for name in pending if name.endswith('.safetensors')]
    if pending:
        print('Downloading pinned official checkpoint',REVISION,flush=True)
        snapshot_download('Qwen/Qwen3.5-4B',revision=REVISION,endpoint=os.environ['HF_ENDPOINT'],
            local_dir=ROOT/'base_model',allow_patterns=pending,max_workers=3)
    for file in manifest['files']:
        path=ROOT/'base_model'/file['name']
        if path.stat().st_size!=file['size']:
            raise ValueError('Size mismatch '+file['name'])
        if file['sha256']:
            with path.open('rb') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest!=file['sha256']:
                raise ValueError('SHA256 mismatch '+file['name'])
        print('Verified',file['name'],flush=True)
    (ROOT/'model_verified.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

if __name__=='__main__':
    main()

"""Translate one request through the deployed soft-constraint model."""
import argparse
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent


def translate(text):
    config = json.loads((ROOT / 'deployment.json').read_text(encoding='utf-8'))
    payload = {
        'model': config['alias'],
        'messages': [
            {'role': 'system', 'content': (ROOT / 'prompt.txt').read_text(encoding='utf-8')},
            {'role': 'user', 'content': text},
        ],
        'temperature': 0,
        'max_tokens': 512,
        'stream': False,
    }
    request = urllib.request.Request(
        f'http://127.0.0.1:{config["port"]}/v1/chat/completions',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=180) as response:
        result = json.load(response)
    output = result['choices'][0]['message']['content'].strip()
    if output.startswith('```'):
        output = output.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    return json.loads(output)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('text', nargs='?', help='User travel request. Reads stdin if omitted.')
    args = parser.parse_args()
    text = args.text if args.text is not None else sys.stdin.read().strip()
    if not text:
        parser.error('A travel request is required')
    print(json.dumps(translate(text), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

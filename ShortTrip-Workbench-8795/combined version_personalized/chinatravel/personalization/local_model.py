"""JSON requests to the bundled Qwen service on the machine running ShortTrip."""

from contextlib import closing
from copy import deepcopy
from http.client import HTTPConnection, HTTPException
import json
from time import perf_counter

from .tripspec import ModelReply


LOCAL_MODEL = 'qwen3.5-4b-q8'
LOCAL_PORT = 18080
LOCAL_EXTRACTION_TIMEOUT = 38
LOCAL_SKETCH_TIMEOUT = 24


def local_model_available():
    try:
        with closing(HTTPConnection('127.0.0.1', LOCAL_PORT, timeout=1)) as client:
            client.request('GET', '/v1/models')
            response = client.getresponse()
            return response.status == 200 and any(
                row.get('id') == LOCAL_MODEL for row in json.load(response)['data']
            )
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError):
        return False


def request_local_json(prompt, payload, *, max_tokens, timeout=LOCAL_EXTRACTION_TIMEOUT):
    started = perf_counter()
    reply = ModelReply(attempts=1, model=LOCAL_MODEL)
    request = {
        'model': LOCAL_MODEL, 'temperature': 0, 'max_tokens': max_tokens,
        'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': prompt}, {
            'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        }],
    }
    try:
        with closing(HTTPConnection('127.0.0.1', LOCAL_PORT, timeout=timeout)) as client:
            client.request('POST', '/v1/chat/completions', json.dumps(request, ensure_ascii=False).encode(),
                           {'Content-Type': 'application/json'})
            response = client.getresponse()
            if response.status != 200:
                reply.error = 'local_model_http_error'
                return reply
            decoded = json.load(response)
        reply.usage = {key: value for key, value in (decoded.get('usage') or {}).items()
                       if key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        choice = decoded['choices'][0]
        content = choice['message'].get('content')
        if not content or choice['finish_reason'] == 'length':
            reply.error = 'truncated_or_empty_model_output'
        else:
            value = json.loads(content)
            if isinstance(value, dict):
                reply.draft = value
            else:
                reply.error = 'invalid_model_output'
    except TimeoutError:
        reply.error = 'APITimeoutError'
    except (OSError, HTTPException):
        reply.error = 'local_model_unavailable'
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        reply.error = 'invalid_model_output'
    finally:
        reply.elapsed_seconds = round(perf_counter() - started, 5)
    return reply


def request_local_sketch(prompt, payload, *, max_tokens, timeout):
    from .trip_sketch import PLACE_LISTS

    packet = deepcopy(payload)
    full_ids = {}
    for i, row in enumerate(packet['places'], 1):
        original = row[0]
        row[0] = f'P{i}'
        full_ids[original] = row[0]
    for requirement in packet['named']:
        requirement['ids'] = [full_ids[point] for point in requirement['ids']]
    for appointment in packet['appointments']:
        appointment[1] = full_ids[appointment[1]]
    reply = request_local_json(prompt + '\n地点只使用 places 首列的完整短编号，例如 P1。', packet,
                               max_tokens=max_tokens, timeout=timeout)
    long_ids = {short: full for full, short in full_ids.items()}
    layouts = (reply.draft or {}).get('layouts')
    if isinstance(layouts, list):
        for layout in layouts:
            for day in layout if isinstance(layout, list) else ():
                if not isinstance(day, dict):
                    continue
                for field in PLACE_LISTS:
                    if isinstance(values := day.get(field), list):
                        day[field] = [long_ids.get(ref, ref) if isinstance(ref, str) else ref for ref in values]
    return reply

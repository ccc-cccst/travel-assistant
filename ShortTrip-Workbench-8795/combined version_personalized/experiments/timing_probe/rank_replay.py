"""Experimental control: hold a ranking answer fixed, still paying real API cost."""
import hashlib
import json
from pathlib import Path

PHASES={'outbound_ranking','hotel_ranking','return_ranking'}


def control_answer(answer,phase,request_hash,directory):
    if not directory or phase not in PHASES or not isinstance(answer.get('content'),str):
        return answer, None
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    path=root/(request_hash+'.json')
    generated=answer['content']
    if path.exists():
        recorded=json.loads(path.read_text(encoding='utf-8'))
        if recorded['phase']!=phase or recorded['request_sha256']!=request_hash:
            raise ValueError('Ranking replay identity mismatch')
        content=recorded['content'];replayed=True
    else:
        content=generated;replayed=False
        path.write_text(json.dumps({'phase':phase,'request_sha256':request_hash,'content':content},
                                   ensure_ascii=False,indent=2),encoding='utf-8')
    # Usage and timing refer to the real generated response, not replay length.
    return {**answer,'content':content},{'replayed':replayed,
        'generated_sha256':hashlib.sha256(generated.encode()).hexdigest(),
        'used_sha256':hashlib.sha256(content.encode()).hexdigest(),
        'changed_generated_answer':content!=generated}

"""ChinaTravel-compatible hard_logic extraction using the planner's model."""

import ast
import json
import re
from pathlib import Path

from openai import OpenAIError

from chinatravel.data.load_datasets import ORACLE_FIELDS, save_json_file
from chinatravel.environment.language import city_names, normalize_lang
from .constraint_compiler import _catalog, _number_present, comparison, numeric_source_clause
from .extraction_alignment import derived_fields, fallback_translation, literal
from .translation_contract import TranslationValidationError
from .prompts.chinatravel_dsl import instruction


FORMAT = "chinatravel_hard_logic_v2"
INPUT_FIELDS = ("uid", "start_city", "target_city", "days", "people_number", "nature_language")
PRICE_AND_ROOM_FIELDS = {"cost", "hotel_price", "food_price", "rooms", "room_type"}


def requested_room_value(query, field, value):
    source=query['nature_language']
    quantity=r'\d+|[零一二两三四五六七八九十百千万]+|one|two|three|four|five|six|seven|eight|nine|ten|a|an'
    if field=='rooms':
        pattern=rf'({quantity})\s*(?:(?:single|double|twin|king|queen)\s+)?(?:间|rooms?\b)'
        matches=re.finditer(pattern,source,re.I)
        return any((value==1 and m[1].lower() in ('a','an')) or _number_present(value,m[1]) for m in matches)
    if field=='room_type':
        beds=set()
        if re.search(r'单床|大床|\b(?:single|double|king|queen)[ -]bed\b',source,re.I):
            beds.add(1)
        if re.search(r'双床|\btwin\b|\btwo beds\b',source,re.I):
            beds.add(2)
        if value in beds:
            return True
        pattern=rf'({quantity})\s*(?:张床|beds?\b)'
        return any(_number_present(value,m[1]) for m in re.finditer(pattern,source,re.I))
    return False


def explicit_zero(query, field):
    try:
        clause = numeric_source_clause(field, query["nature_language"])
    except TranslationValidationError:
        return False
    return _number_present(0, clause)


def extract_hard_logic(query, model, lang):
    prompt = instruction(query["nature_language"], lang)
    answer = model([{"role": "user", "content": prompt}], one_line=False, json_mode=True)
    try:
        # ChinaTravel accepts a JSON object inside a prose or Markdown reply.
        result = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
        entries = result["hard_logic"]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise TranslationValidationError("ChinaTravel format requires a hard_logic JSON array") from error
    if not isinstance(entries, list) or not entries or any(not isinstance(text, str) or not text.strip() for text in entries):
        raise TranslationValidationError("ChinaTravel hard_logic must contain nonempty constraint strings")
    known = derived_fields(query)
    constraints = [f"{name}=={value}" for name, value in known.items()]
    catalog = None
    for text in entries:
        text = text.strip()
        atom = comparison(text)
        # Keep the confirmed input values for the basic itinerary invariants.
        if atom and atom[0] in known:
            continue
        if (atom and atom[0] in ('rooms','room_type') and atom[3]=='scalar'
                and not requested_room_value(query,atom[0],atom[2])):
            print('TRANSLATION_NORMALIZATION dropped unsupported '+text)
            continue
        if (atom and atom[0] in PRICE_AND_ROOM_FIELDS and atom[3] == "scalar"
                and type(atom[2]) in (int, float) and atom[2] == 0 and not explicit_zero(query, atom[0])):
            continue
        if atom and atom[0] == "food_type" and atom[3] == "set" and isinstance(atom[1], ast.LtE):
            names = atom[2]
            if isinstance(names, set) and all(isinstance(name, str) and name in query["nature_language"] for name in names):
                if catalog is None:
                    catalog = _catalog(query["target_city"])
                if names <= catalog["restaurant_names"] and names.isdisjoint(catalog["food_type"]):
                    text = literal(names) + "<=restaurant_names"
        constraints.append(text)
    return list(dict.fromkeys(constraints))


def translate(query, model, lang, cache_dir, load_cache=False):
    from . import nl2sl_hybrid as backend
    from .extraction_alignment import TRANSLATION_FLOW_VERSION

    lang = normalize_lang(lang)
    if lang == "en":
        from . import nl2sl_hybrid_en as backend

    clean = {key: value for key, value in query.items() if key not in ORACLE_FIELDS}
    clean.pop("translation_flow_version", None)
    folder = Path(cache_dir) / f"translation_{model.name}_{FORMAT}"
    folder.mkdir(parents=True, exist_ok=True)
    cache_path = folder / f"{query['uid']}.json"
    if load_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (isinstance(cached, dict) and cached.get("translation_mode") == FORMAT
                    and cached.get("translation_flow_version") != TRANSLATION_FLOW_VERSION
                    and "translation_fallback" not in cached
                    and all(cached.get(key) == clean.get(key) for key in INPUT_FIELDS)
                    and not backend.check(cached)[0]):
                return {**clean, **{key: cached[key] for key in
                                   ("hard_logic", "hard_logic_py", "hard_logic_py_iter_3", "translation_mode",
                                    "reflect_cnt", "translation_program_sources") if key in cached}}
        except (OSError, ValueError, TypeError, KeyError):
            pass

    value = dict(clean, translation_mode=FORMAT)
    try:
        supported = city_names(lang)
        if any(clean.get(key) not in supported for key in ("start_city", "target_city")):
            raise TranslationValidationError("Input city is outside the active snapshot")
        value["hard_logic"] = extract_hard_logic(clean, model, lang)
        # Reuse local safe compilation, program checking and bounded repair.
        backend.nl2sl_step2(value, model)
        checker = backend.HardLogicPyChecker(value["target_city"])
        backend.nl2sl_step3(value, model, checker)
        value["hard_logic_py_iter_3"] = value["hard_logic_py"]
    except (OpenAIError, TranslationValidationError) as error:
        value = fallback_translation(clean, {}, error)
        value["translation_mode"] = FORMAT
        print("TRANSLATION_FALLBACK " + json.dumps(value["translation_fallback"], ensure_ascii=False), flush=True)
        return value
    save_json_file(value, str(cache_path))
    return value


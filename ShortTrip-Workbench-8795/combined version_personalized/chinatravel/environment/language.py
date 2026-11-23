import os
from pathlib import Path


CITY_SLUGS = [
    "beijing",
    "shanghai",
    "nanjing",
    "suzhou",
    "hangzhou",
    "shenzhen",
    "chengdu",
    "wuhan",
    "guangzhou",
    "chongqing",
]

CITY_NAMES = {
    "zh": ["北京", "上海", "南京", "苏州", "杭州", "深圳", "成都", "武汉", "广州", "重庆"],
    "en": [
        "Beijing",
        "Shanghai",
        "Nanjing",
        "Suzhou",
        "Hangzhou",
        "Shenzhen",
        "Chengdu",
        "Wuhan",
        "Guangzhou",
        "Chongqing",
    ],
}

_EXPANDED_ZH_SLUGS = []

_ZH_DATABASE = os.environ.get("CHINATRAVEL_DATABASE_ROOT") or os.environ.get("CHINATRAVEL_ZH_DATABASE")
_EN_DATABASE = os.environ.get("CHINATRAVEL_DATABASE_ROOT_EN")
if _ZH_DATABASE:
    _ZH_DATABASE = Path(_ZH_DATABASE).resolve()
    if not _ZH_DATABASE.is_dir():
        raise FileNotFoundError(f"Chinese sandbox not found: {_ZH_DATABASE}")
    for slug, name in (("xian", "西安"), ("xiamen", "厦门")):
        if (_ZH_DATABASE / "poi" / slug / "poi.json").is_file():
            _EXPANDED_ZH_SLUGS.append(slug)
            CITY_NAMES["zh"].append(name)


def normalize_lang(lang=None, en_version=False):
    if en_version:
        return "en"
    if lang is None:
        return "zh"
    lang = str(lang).lower()
    if lang in {"zh", "cn", "chinese"}:
        return "zh"
    if lang in {"en", "english"}:
        return "en"
    raise ValueError("lang must be one of: zh, en")


def city_names(lang):
    return CITY_NAMES[normalize_lang(lang)]


def city_slugs(lang):
    return CITY_SLUGS + _EXPANDED_ZH_SLUGS if normalize_lang(lang) == "zh" else CITY_SLUGS


def city_lookup(lang, cities=None):
    mapping = dict(zip(city_slugs(lang), city_names(lang)))
    if cities is None:
        return mapping
    return {slug: name for slug, name in mapping.items() if name in cities}


def database_dirname(lang):
    return "database_en" if normalize_lang(lang) == "en" else "database"


def database_path(lang, *parts):
    root = _ZH_DATABASE if normalize_lang(lang) == "zh" else _EN_DATABASE
    if root:
        return Path(root).resolve().joinpath(*parts)
    return Path(__file__).resolve().parent.joinpath(database_dirname(lang), *parts)


def relative_database_path(lang, *parts):
    return str(database_path(lang, *parts))

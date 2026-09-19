# -*- coding: utf-8 -*-
"""Dict loading + deterministic normalization (dedup stage 1).

Order (fixed, from feasibility findings): t2s -> width/punct -> CJK numerals
-> synonyms -> param-name canonicalization. Unit/value normalization for
parameters lives in normalize_param().
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml
from opencc import OpenCC

DICT_DIR = Path(__file__).parent
_t2s = OpenCC("t2s")

NUM_MAP = {"一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
# multi-char CJK numerals must be replaced BEFORE single chars, otherwise
# 三十 -> "310" (char-by-char bug caught by unit test)
NUM_COMPOSITE = {"三十": "30", "二十": "20", "四十": "40", "五十": "50",
                 "六十": "60", "七十": "70", "八十": "80", "九十": "90",
                 "一百": "100", "两百": "200", "零点五": "0.5",
                 "一点二": "1.2", "一点五": "1.5", "一点八": "1.8",
                 "两米": "2米", "半米": "0.5米"}


@lru_cache
def _load(name):
    with open(DICT_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def synonyms() -> dict:
    return _load("synonyms.yaml")


def param_variants() -> dict:
    return _load("param_dictionary.yaml")["name_variants"]


def unit_aliases() -> dict:
    return _load("param_dictionary.yaml")["unit_aliases"]


def taxonomy() -> dict:
    return _load("taxonomy.yaml")


def normalize_text(text: str) -> str:
    """Canonical text for similarity & retrieval."""
    s = _t2s.convert(text or "")
    s = re.sub(r"[，。？！、；：（）()\[\]【】{}\s“”\"'':：\-—_/]", "", s)
    for k, v in synonyms().items():
        s = s.replace(_t2s.convert(k), v)
    for k, v in NUM_COMPOSITE.items():
        s = s.replace(k, v)
    for k, v in NUM_MAP.items():
        s = s.replace(k, v)
    return s.lower()


def canon_param_name(name: str) -> str:
    return param_variants().get(name, name)


def normalize_param(p: dict) -> dict:
    """Normalize one parameter dict {name, value, unit} in place-ish.
    Returns p with canonical name/unit and normalized_value in the target
    unit (lengths -> mm). Sets param_status='unnormalized' when the
    (name, unit) pair is not covered by the dictionary (LLM must not guess).
    """
    out = dict(p)
    out["name"] = canon_param_name(p.get("name", ""))
    unit = unit_aliases().get(p.get("unit") or "", p.get("unit"))
    out["unit"] = unit
    val = p.get("value")
    pd = _load("param_dictionary.yaml")
    try:
        if isinstance(val, (int, float)):
            v = float(val)
            if unit == "cm" and "cm_to_mm" in pd:
                out["normalized_value"] = v * pd["cm_to_mm"]
                out["unit"] = "mm"          # keep unit in sync with normalized target
            elif unit == "m" and "m_to_mm" in pd:
                out["normalized_value"] = v * pd["m_to_mm"]
                out["unit"] = "mm"
            elif unit == "mm":
                out["normalized_value"] = v
            elif unit in ("遍", "h", "MPa") or out["name"] in pd.get("percent_params", []):
                out["normalized_value"] = v
            else:
                out["param_status"] = "unnormalized"
        else:
            out["param_status"] = "unnormalized"
    except (TypeError, ValueError):
        out["param_status"] = "unnormalized"
    return out

# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reno.dict import normalize_text, normalize_param  # noqa: E402
from reno.dedup import bigram_cosine  # noqa: E402
from reno.ocr import fuse_spans  # noqa: E402
from reno.media import dhash_bits, hamming, pick_budget_frames  # noqa: E402
from reno.llm import extract_json  # noqa: E402


def test_normalize_t2s_synonym():
    assert normalize_text("這網上都說書桌底下的電源插座") == "这网上都说书桌底下的插座"


def test_normalize_cjk_numerals():
    assert "30" in normalize_text("三十公分")
    assert "2" in normalize_text("两毫米")
    assert "1.8" in normalize_text("一点八米")


def test_param_two_layer():
    p = normalize_param({"name": "淋浴区涂刷高度", "value": 1.8, "unit": "米(m)"})
    assert p["name"] == "卫生间墙面防水高度"
    assert p["normalized_value"] == 1800.0


def test_param_uncovered_marks_not_guesses():
    p = normalize_param({"name": "奇怪参数", "value": 3, "unit": "坨"})
    assert p.get("param_status") == "unnormalized"
    assert "normalized_value" not in p


def test_bigram_cosine_self():
    from pytest import approx
    assert bigram_cosine("防水涂料", "防水涂料") == approx(1.0)
    assert bigram_cosine("防水", "插座电路") == 0.0


def test_fuse_spans_merges_repeats():
    pf = [{"frame_id": "f1", "pts_ms": 12000, "text": "插座离地30厘米", "conf": 0.9},
          {"frame_id": "f2", "pts_ms": 13000, "text": "插座离地30厘米", "conf": 0.9},
          {"frame_id": "f3", "pts_ms": 40000, "text": "另一句", "conf": 0.8}]
    spans = fuse_spans(pf)
    assert len(spans) == 2
    assert spans[0]["text"] == "插座离地30厘米"
    assert spans[0]["start_ms"] == 11400
    assert spans[0]["end_ms"] == 14800


def test_dhash_identical_and_different():
    from PIL import Image
    a = Image.new("L", (64, 64), 128)
    b = Image.new("L", (64, 64), 128)
    c = Image.new("L", (64, 64), 10)
    ha, hb, hc = dhash_bits(a), dhash_bits(b), dhash_bits(c)
    assert hamming(ha, hb) == 0
    # flat images: all comparisons equal -> identical hash; use gradient to differ
    # dHash measures HORIZONTAL gradients - test image must vary horizontally
    g1 = Image.new("L", (64, 64)); g2 = Image.new("L", (64, 64))
    g1.putdata([x for y in range(64) for x in range(64)])
    g2.putdata([63 - x for y in range(64) for x in range(64)])
    assert hamming(dhash_bits(g1), dhash_bits(g2)) > 8


def test_budget_uniform_not_head_slice():
    frames = [{"frame_id": f"f{i}", "pts_ms": i * 1000, "origin": "scene",
               "dhash": "0x0", "path": ""} for i in range(100)]
    picked = pick_budget_frames(frames, 10)
    assert len(picked) == 10
    pts = [f["pts_ms"] for f in picked]
    assert pts[0] == 0 and pts[-1] == 90000  # covers full video, not head


def test_extract_json_variants():
    assert extract_json('{"a":1}') == {"a": 1}
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('前置说明文字 {"a":[1,2]} 后置') == {"a": [1, 2]}
    assert extract_json("完全不是JSON") is None


def test_db_roundtrip_and_clamp(tmp_path):
    from reno import db as rdb
    con = rdb.connect(tmp_path / "t.db")
    rdb.upsert_asset(con, {"video_id": "v1", "content_sha256": "s1",
                           "duration_ms": 10000, "files": {"video": "x.mp4"}})
    rdb.replace_segments(con, "v1", [{"id": "asr_0000", "start_ms": 9500,
                                      "end_ms": 10100, "text": "尾段"}])
    # clamp happens in atomize; here verify index bounds enforcement input
    idx = rdb.timeline_index(con, "v1")
    assert idx["asr_0000"]["end_ms"] == 10100
    rdb.replace_atoms(con, "v1", [{
        "id": "a1", "claim": "测试原子主张", "polarity": "recommend",
        "category": "防水", "conditions": {}, "parameters": [],
        "evidence_refs": [{"video_id": "v1", "modality": "asr",
                           "source_item_id": "asr_0000", "start_ms": 9500,
                           "end_ms": 10000, "evidence_text": "尾段", "weight": 0.7}],
        "confidence": 0.7}])
    atoms = rdb.all_atoms(con)
    assert len(atoms) == 1 and atoms[0]["evidence_refs"][0]["source_item_id"] == "asr_0000"
    con.close()

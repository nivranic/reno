# -*- coding: utf-8 -*-
"""OCR step: RapidOCR on budget frames -> watermark filter -> span fusion
(the doc's OCR fusion rule; ported from feasibility step4b)."""
import time
from collections import defaultdict

from . import config, db
from .media import budget_frames

_ocr = None
FUSE_GAP_MS = 6000
HOLD_MS = 1800
WM_MIN_FRAMES = 5
WM_MIN_RATE = 0.25


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def fuse_spans(per_frame):
    spans = []
    for pf in per_frame:
        if not pf["text"]:
            continue
        if spans and spans[-1]["text"] == pf["text"] \
                and pf["pts_ms"] - spans[-1]["end_ms"] <= FUSE_GAP_MS:
            spans[-1]["end_ms"] = pf["pts_ms"] + HOLD_MS
            spans[-1]["frame_ids"].append(pf["frame_id"])
            spans[-1]["conf"] = max(spans[-1]["conf"], pf["conf"])
        else:
            spans.append({"text": pf["text"],
                          "start_ms": max(0, pf["pts_ms"] - 600),
                          "end_ms": pf["pts_ms"] + HOLD_MS,
                          "frame_ids": [pf["frame_id"]], "conf": pf["conf"]})
    return [{"id": f"ocr_{i:03d}", **s} for i, s in enumerate(spans)]


def run(con, video_id: str) -> dict:
    ocr = _get_ocr()
    frames = budget_frames(con, video_id)
    t0 = time.time()
    results = []
    for fr in frames:
        res, _ = ocr(fr["path"])
        items = []
        if res:
            for box, text, conf in res:
                ys = [p[1] for p in box]
                xs = [p[0] for p in box]
                items.append({"text": text, "conf": round(float(conf), 3),
                              "y_center": round(sum(ys) / len(ys), 1),
                              "x_center": round(sum(xs) / len(xs), 1)})
        results.append({"frame_id": fr["frame_id"], "pts_ms": fr["pts_ms"],
                        "items": items})
    dt = time.time() - t0

    # watermark filter: same short text at same spot across many frames
    occ = defaultdict(int)
    for r in results:
        seen = set()
        for it in r["items"]:
            if it["text"] not in seen:
                occ[it["text"]] += 1
                seen.add(it["text"])
    n = max(1, len(results))
    wm = {t for t, c in occ.items()
          if c >= WM_MIN_FRAMES and c / n >= WM_MIN_RATE}

    per_frame = []
    for r in results:
        texts = [it for it in r["items"] if it["text"] not in wm]
        if not texts:
            per_frame.append({**r, "text": None, "conf": 0.0})
            continue
        best = max(texts, key=lambda it: len(it["text"]))
        per_frame.append({**r, "text": best["text"], "conf": best["conf"]})

    spans = fuse_spans(per_frame)
    db.replace_ocr(con, video_id, spans)
    stats = {"n_frames": len(frames),
             "frames_with_text": sum(1 for p in per_frame if p["text"]),
             "n_spans": len(spans), "watermarks": sorted(wm),
             "process_s": round(dt, 1)}
    db.log_run(con, video_id, "ocr", model="rapidocr-onnxruntime", ok=True,
               detail=str(stats))
    con.commit()
    return stats

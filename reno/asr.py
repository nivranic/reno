# -*- coding: utf-8 -*-
"""ASR step: faster-whisper, segment-level ms timestamps, VAD."""
import os
import time

from . import config, db

_model = None


def _get_model():
    global _model
    if _model is None:
        cache = config.get("hf_cache_dir") or str(config.ROOT / "cache_hf")
        os.environ.setdefault("HF_HOME", cache)
        os.environ.setdefault("HF_ENDPOINT", config.get("hf_endpoint"))
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.get("asr_model"), device="cpu",
                              compute_type=config.get("asr_compute"))
    return _model


def run(con, video_id: str) -> dict:
    asset = db.get_asset(con, video_id)
    import json as _json
    files = _json.loads(asset["files_json"])
    wav = config.AUDIO / video_id / "audio16k.wav"
    t0 = time.time()
    model = _get_model()
    segments, info = model.transcribe(str(wav), language="zh", vad_filter=True,
                                      beam_size=5, condition_on_previous_text=False)
    segs = []
    dur = asset["duration_ms"] or 1
    for i, s in enumerate(segments):
        end = min(int(s.end * 1000), dur)  # clamp: VAD can overrun container
        segs.append({"id": f"asr_{i:04d}", "start_ms": int(s.start * 1000),
                     "end_ms": max(end, int(s.start * 1000) + 200),
                     "text": s.text.strip()})
    dt = time.time() - t0
    db.replace_segments(con, video_id, segs)
    stats = {"n_segments": len(segs), "rtf": round(dt / (dur / 1000), 3),
             "process_s": round(dt, 1), "model": config.get("asr_model")}
    db.log_run(con, video_id, "asr", model=config.get("asr_model"), ok=True,
               detail=str(stats))
    con.commit()
    return stats

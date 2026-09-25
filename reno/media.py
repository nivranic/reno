# -*- coding: utf-8 -*-
"""Media step: 16k audio, scene detection, uniform-budget keyframe sampling
+ dHash near-dup removal. Feasibility lessons baked in:
- DASH video has no audio -> audio comes from the audio file
- frame budget must be UNIFORM across the video, never a head slice
"""
import subprocess
import time
from pathlib import Path

from PIL import Image

from . import config, db


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_audio(src: Path, out: Path) -> None:
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
    subprocess.run(cmd, check=True)


def detect_scenes(v: Path):
    from scenedetect import detect, ContentDetector
    scene_list = detect(str(v), ContentDetector(), show_progress=False)
    return [(int(s.get_seconds() * 1000), int(e.get_seconds() * 1000))
            for s, e in scene_list]


def dhash_bits(img: Image.Image) -> int:
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            if px[r * 9 + c] > px[r * 9 + c + 1]:
                bits |= 1 << (r * 8 + c)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def extract_frame(v: Path, ms: int, out: Path, max_w: int = 960) -> bool:
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{ms/1000:.3f}",
           "-i", str(v), "-frames:v", "1", "-vf",
           f"scale='min({max_w},iw)':-2", "-q:v", "3", str(out)]
    subprocess.run(cmd, capture_output=True)
    return out.exists() and out.stat().st_size > 0


def sample_targets(scenes, duration_ms: int, interval_s: float):
    """Per-scene head/mid/tail + periodic safety frames (doc strategy)."""
    targets = {}
    for (s_ms, e_ms) in scenes:
        span = e_ms - s_ms
        cand = [s_ms + 200, (s_ms + e_ms) // 2, e_ms - 200]
        if span < 900:
            cand = [(s_ms + e_ms) // 2]
        for ms in cand:
            if 0 <= ms < duration_ms:
                targets[ms] = "scene"
    ms = 0
    while ms < duration_ms:
        targets.setdefault(ms, "safety")
        ms += int(interval_s * 1000)
    return targets


def pick_budget_frames(all_frames, budget: int):
    """Uniform spread across the video, scene-origin first, safety fill.
    The head-slice bug (feasibility defect #3) must never come back."""
    scene = [f for f in all_frames if f["origin"] == "scene"]
    safety = [f for f in all_frames if f["origin"] == "safety"]
    if len(scene) > budget:
        step = len(scene) / budget
        picked = [scene[int(i * step)] for i in range(budget)]
    else:
        picked = list(scene)
    if len(picked) < budget and safety:
        step = max(1, len(safety) // max(1, budget - len(picked)))
        picked += safety[::step][:budget - len(picked)]
    return sorted(picked, key=lambda f: f["pts_ms"])


def run(con, video_id: str) -> dict:
    asset = dict(db.get_asset(con, video_id))
    import json as _json
    files = _json.loads(asset["files_json"]) if isinstance(asset["files_json"], str) else asset["files_json"]
    v = config.ROOT / files["video"]
    a = config.ROOT / files["audio"] if files.get("audio") else None
    vdir = config.FRAMES / video_id
    vdir.mkdir(parents=True, exist_ok=True)
    adir = config.AUDIO / video_id
    adir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    wav = adir / "audio16k.wav"
    if not wav.exists():
        extract_audio(a if a and Path(a).exists() else v, wav)
    t_audio = time.time() - t0

    t1 = time.time()
    scenes = detect_scenes(v) if v.suffix == ".mp4" else []
    t_scene = time.time() - t1

    t2 = time.time()
    duration_ms = asset["duration_ms"] or 1
    targets = sample_targets(scenes, duration_ms, config.get("safety_interval_s"))
    kept, hashes = [], []
    for ms in sorted(targets):
        fp = vdir / f"f{ms:07d}.jpg"
        if not extract_frame(v, ms, fp):
            continue
        h = dhash_bits(Image.open(fp))
        if any(hamming(h, k) <= 8 for k in hashes):
            fp.unlink()
            continue
        hashes.append(h)
        kept.append({"frame_id": fp.stem, "pts_ms": ms,
                     "origin": targets[ms], "dhash": hex(h), "path": str(fp)})
    t_frames = time.time() - t2

    db.replace_frames(con, video_id, kept)
    stats = {"n_scene_cuts": len(scenes), "n_targets": len(targets),
             "n_frames_kept": len(kept),
             "audio_s": round(t_audio, 1), "scene_s": round(t_scene, 1),
             "frames_s": round(t_frames, 1)}
    db.log_run(con, video_id, "media", model="ffmpeg/scenedetect", ok=True,
               detail=str(stats))
    con.commit()
    return stats


def budget_frames(con, video_id: str):
    budget = int(config.get("max_frames_per_video"))
    rows = con.execute("SELECT frame_id, pts_ms, origin, dhash, path FROM frame WHERE video_id=? ORDER BY pts_ms",
                       (video_id,)).fetchall()
    return pick_budget_frames([dict(r) for r in rows], budget)

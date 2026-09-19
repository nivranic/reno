# -*- coding: utf-8 -*-
"""Zhipu GLM API router: chat with model fallback chain, vision, JSON parsing.

Fallback matters in practice: free flash models return 1305 (overloaded)
under load - observed during setup. reasoning_content is stripped.
"""
import base64
import json
import re
import time
import urllib.error
import urllib.request

from . import config


def _post(path: str, body: dict, timeout: int = 120) -> dict:
    url = config.get("zhipu_base_url").rstrip("/") + path
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {config.get('zhipu_api_key')}",
                 "Content-Type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")[:400]
            last = RuntimeError(f"HTTP {e.code}: {payload}")
            if e.code in (429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def chat(messages, model=None, temperature=0.3, max_tokens=4096, json_mode=True,
         timeout: int = None):
    """Chat with fallback chain. Returns content string."""
    models = [model] if model else config.get("judge_models")
    if model is None:
        models = [config.get("atomize_model")]
    if timeout is None:
        # long structured outputs (atomize) need far more than default 120s
        timeout = max(120, int(max_tokens * 0.06))
    errors = []
    for m in models:
        try:
            body = {"model": m, "messages": messages,
                    "temperature": temperature, "max_tokens": max_tokens}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            d = _post("/chat/completions", body, timeout=timeout)
            msg = d["choices"][0]["message"]
            content = msg.get("content") or ""
            if not content.strip():
                raise RuntimeError("empty content (reasoning-only response)")
            return content, m
        except Exception as e:  # noqa: BLE001 - fall through to next model
            errors.append(f"{m}: {e}")
    raise RuntimeError("all models failed: " + " | ".join(str(e) for e in errors))


def chat_json(messages, model=None, max_tokens=4096):
    """Chat that must return a JSON object. Tolerates code fences and
    reasoning prefixes; one repair retry with an explicit reminder."""
    content, m = chat(messages, model=model, max_tokens=max_tokens)
    obj = extract_json(content)
    if obj is None:
        content, m = chat(
            messages + [{"role": "user",
                         "content": "你上一条回复不是合法JSON。请重新输出,只输出JSON对象,不要任何其他文字。"}],
            model=model, max_tokens=max_tokens)
        obj = extract_json(content)
    if obj is None:
        raise ValueError(f"model {m} failed to produce JSON: {content[:200]}")
    return obj, m


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # find first {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
        break
    return None


def vision(image_path: str, prompt: str, max_tokens=300) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = {
        "model": config.get("vlm_model"),
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt}]}],
        "temperature": 0.1, "max_tokens": max_tokens}
    d = _post("/chat/completions", body, timeout=90)
    return d["choices"][0]["message"].get("content") or ""

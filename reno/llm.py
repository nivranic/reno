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
    backoffs = {429: (6, 18, 54), 500: (2, 4, 8), 502: (2, 4, 8), 503: (2, 4, 8)}
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")[:400]
            last = RuntimeError(f"HTTP {e.code}: {payload}")
            if e.code in backoffs and attempt < 3:
                time.sleep(backoffs[e.code][attempt])
                # rebuild request: the consumed body can't be reused
                req = urllib.request.Request(
                    url, data=json.dumps(body).encode("utf-8"),
                    headers={"Authorization": f"Bearer {config.get('zhipu_api_key')}",
                             "Content-Type": "application/json"})
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


# GLM-5.3 family: NOT covered by the paas v4 resource packs of the coding
# plan (1113) but fully available on the anthropic-compatible endpoint under
# the SAME plan quota (verified 2026-09-20, incl. thinking disable + 128k).
ANTHROPIC_PREFIX = "glm-5.3"
ANTHROPIC_URL = "https://open.bigmodel.cn/api/anthropic/v1/messages"


def _is_anthropic(model: str) -> bool:
    return (model or "").lower().startswith(ANTHROPIC_PREFIX)


def _post_anthropic(messages, model, temperature, max_tokens, timeout) -> str:
    """chat() path for GLM-5.3* models via /api/anthropic (coding-plan quota).
    Same backoff policy as _post; JSON tolerance handled by extract_json."""
    system = "\n\n".join(m.get("content", "") for m in messages
                         if m.get("role") == "system")
    rest = [{"role": m["role"], "content": m.get("content", "")}
            for m in messages if m.get("role") != "system"]
    body = {"model": model,
            "max_tokens": max(128000, max_tokens),  # cap, not target
            "temperature": temperature,
            "messages": rest}
    if system:
        body["system"] = system
    if not config.get("llm_thinking_enabled"):
        body["thinking"] = {"type": "disabled"}

    def build():
        return urllib.request.Request(
            ANTHROPIC_URL, data=json.dumps(body).encode("utf-8"),
            headers={"x-api-key": config.get("zhipu_api_key"),
                     "Authorization": f"Bearer {config.get('zhipu_api_key')}",
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"})

    req = build()
    last = None
    backoffs = {429: (6, 18, 54), 500: (2, 4, 8), 502: (2, 4, 8), 503: (2, 4, 8)}
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            text = "".join(p.get("text", "") for p in d.get("content", [])
                           if p.get("type") == "text").strip()
            if not text:
                raise RuntimeError("empty content (reasoning-only response)")
            return text
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")[:400]
            last = RuntimeError(f"HTTP {e.code}: {payload}")
            if e.code in backoffs and attempt < 3:
                time.sleep(backoffs[e.code][attempt])
                req = build()  # body stream was consumed
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
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
            if _is_anthropic(m):
                # anthropic endpoint has no response_format; extract_json handles
                return _post_anthropic(messages, m, temperature, max_tokens,
                                       timeout), m
            body = {"model": m, "messages": messages,
                    "temperature": temperature, "max_tokens": max_tokens}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if not config.get("llm_thinking_enabled"):
                body["thinking"] = {"type": "disabled"}
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

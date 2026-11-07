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

from . import budget, budget_contract, config, model_budget
from .sync_source_opportunity17 import ReadmissionRequired17

_BUDGET_ERRORS = (budget_contract.BudgetDeferred, budget_contract.BudgetContractError,
                  budget.BudgetLedgerError, ReadmissionRequired17)


def _send_response(req, payload, timeout):
    if req.data != payload:
        raise budget.BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return model_budget.ProviderResponse(getattr(response, "status", 200), response.read())
    except urllib.error.HTTPError as error:
        # A complete HTTP response is a receipt, including a retryable error.
        # Read failures still propagate as an uncertain transport observation.
        return model_budget.ProviderResponse(error.code, error.read())


def _provider_outcome(adapter, response):
    if response.status >= 400:
        return "http_retry" if response.status in {429, 500, 502, 503} else "terminal_error"
    value = json.loads(response.body.decode("utf-8"))
    if adapter == "glm53_anthropic":
        content = "".join(part.get("text", "") for part in value.get("content", []) if part.get("type") == "text")
    else:
        content = value["choices"][0]["message"].get("content") or ""
    if model_budget.expects_json():
        return "full_result" if content.strip() and extract_json(content) is not None else "repair"
    return "full_result" if content.strip() else "terminal_error"


def _http_error(response):
    payload = response.body.decode("utf-8", "replace")[:400]
    return RuntimeError(f"HTTP {response.status}: {payload}")


def _post(path: str, body: dict, timeout: int = 120) -> dict:
    if config.is_cloud() and _is_anthropic(body.get("model")):
        raise RuntimeError("node_role=cloud forbids glm-5.3* (coding-plan) models")
    url = config.get("zhipu_base_url").rstrip("/") + path
    def build():
        return urllib.request.Request(
            url, data=model_budget.payload_bytes(body),
            headers={"Authorization": f"Bearer {config.get('zhipu_api_key')}", "Content-Type": "application/json"})
    req = build()
    last = None
    backoffs = {429: (6, 18, 54), 500: (2, 4, 8), 502: (2, 4, 8), 503: (2, 4, 8)}
    for attempt in range(4):
        try:
            response = model_budget.request_once("paas_v4", body, lambda payload: _send_response(req, payload, timeout),
                classify=lambda result: _provider_outcome("paas_v4", result),
                transport_admission=_coding_plan_admission if _is_anthropic(body.get("model")) else None)
            if response.status >= 400:
                last = _http_error(response)
                if response.status in backoffs and attempt < 3:
                    time.sleep(backoffs[response.status][attempt])
                    req = build()
                    continue
                raise last
            return json.loads(response.body.decode("utf-8"))
        except _BUDGET_ERRORS:
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
            req = build()
    raise last


# GLM-5.3 family: NOT covered by the paas v4 resource packs of the coding
# plan (1113) but fully available on the anthropic-compatible endpoint under
# the SAME plan quota (verified 2026-09-20, incl. thinking disable + 128k).
ANTHROPIC_PREFIX = "glm-5.3"
ANTHROPIC_URL = "https://open.bigmodel.cn/api/anthropic/v1/messages"


def _is_anthropic(model: str) -> bool:
    return (model or "").lower().startswith(ANTHROPIC_PREFIX)


def _coding_plan_admission():
    # Entry guards keep their historical public error. Once work is underway,
    # role changes must stop only a new physical opportunity, before dispatch.
    if config.is_cloud():
        raise budget_contract.BudgetDeferred("unsupported_capability")


def _post_anthropic(messages, model, temperature, max_tokens, timeout) -> str:
    """chat() path for GLM-5.3* models via /api/anthropic (coding-plan quota).
    Same backoff policy as _post; JSON tolerance handled by extract_json."""
    if config.is_cloud():
        raise RuntimeError("node_role=cloud forbids glm-5.3* (coding-plan) models")
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
            ANTHROPIC_URL, data=model_budget.payload_bytes(body),
            headers={"x-api-key": config.get("zhipu_api_key"),
                     "Authorization": f"Bearer {config.get('zhipu_api_key')}",
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"})

    req = build()
    last = None
    backoffs = {429: (6, 18, 54), 500: (2, 4, 8), 502: (2, 4, 8), 503: (2, 4, 8)}
    for attempt in range(4):
        try:
            response = model_budget.request_once("glm53_anthropic", body,
                lambda payload: _send_response(req, payload, timeout),
                classify=lambda result: _provider_outcome("glm53_anthropic", result),
                transport_admission=_coding_plan_admission)
            if response.status >= 400:
                last = _http_error(response)
                if response.status in backoffs and attempt < 3:
                    time.sleep(backoffs[response.status][attempt])
                    req = build()
                    continue
                raise last
            d = json.loads(response.body.decode("utf-8"))
            text = "".join(p.get("text", "") for p in d.get("content", [])
                           if p.get("type") == "text").strip()
            if not text:
                if model_budget.is_tracked() and model_budget.expects_json():
                    return text
                raise RuntimeError("empty content (reasoning-only response)")
            return text
        except _BUDGET_ERRORS:
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
            req = build()
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
    if config.is_cloud() and any(_is_anthropic(m) for m in models):
        # coding-plan quota must never be spent from a cloud node - fail fast
        # before any request leaves the process (docs/sync-plan.md §1/§6)
        raise RuntimeError(
            "node_role=cloud forbids glm-5.3* (coding-plan) models; "
            "configure ask_model/atomize_model to a paas model instead")
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
                if not (model_budget.is_tracked() and model_budget.expects_json()):
                    raise RuntimeError("empty content (reasoning-only response)")
            return content, m
        except _BUDGET_ERRORS:
            raise
        except Exception as e:  # noqa: BLE001 - fall through to next model
            errors.append(f"{m}: {e}")
    raise RuntimeError("all models failed: " + " | ".join(str(e) for e in errors))


def chat_json(messages, model=None, max_tokens=4096):
    """Chat that must return a JSON object. Tolerates code fences and
    reasoning prefixes; one repair retry with an explicit reminder."""
    with model_budget.classification(expects_json=True):
        content, m = chat(messages, model=model, max_tokens=max_tokens)
        obj = extract_json(content)
        if obj is None:
            with model_budget.phase("repair"):
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

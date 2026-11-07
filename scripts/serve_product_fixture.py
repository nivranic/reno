"""Serve only a validated synthetic product fixture, with in-process LLM stubs.

This entry point never starts a worker at startup. Explicit maintenance tasks
may start the production worker against this isolated fixture. The production
app has no route or config switch that can enable these stubs.
"""
import argparse
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
_request_misses: ContextVar[list | None] = ContextVar("qa_model_misses", default=None)


def load_manifest(path: Path) -> dict:
    path = path.expanduser().resolve(strict=True)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = Path(manifest["root"]).resolve(strict=True)
    if path != root / "qa-manifest.json" or REPO.is_relative_to(root):
        raise ValueError("Manifest must belong to an isolated QA root")
    expected_paths = {"config_file": root / "config.test.json", "data_dir": root / "data",
                      "media_dir": root / "media", "frame_cache": root / "frames_cache",
                      "questions_file": root / "product_questions.json"}
    for key, expected in expected_paths.items():
        actual = Path(manifest[key]).resolve(strict=True)
        if actual != expected or not actual.is_relative_to(root):
            raise ValueError(f"Invalid QA manifest path: {key}")
    port = manifest["port"]
    if type(port) is not int or not 1024 <= port <= 65535 or port == 8765:
        raise ValueError("QA port must be non-production, default 18765")
    if manifest["base_url"] != f"http://127.0.0.1:{port}":
        raise ValueError("QA server must listen on loopback")
    if not re.fullmatch(r"[0-9a-f]{32}", manifest["qa_instance_id"]):
        raise ValueError("Invalid QA instance id")
    local = json.loads(Path(manifest["config_file"]).read_text(encoding="utf-8"))
    if (local.get("zhipu_api_key") != "" or local.get("ytdlp_cookies") != ""
            or local.get("auth_token") != manifest["auth_token"]
            or manifest["auth_token"] != "reno-qa-" + manifest["qa_instance_id"]):
        raise ValueError("QA config must contain only empty provider credentials and the test token")
    questions = Path(manifest["questions_file"]).read_bytes()
    if hashlib.sha256(questions).hexdigest() != manifest["questions_sha256"]:
        raise ValueError("QA question fixture changed after preparation")
    fixtures = json.loads(questions)
    if fixtures["fixture_version"] != manifest["fixture_version"] or len(fixtures["questions"]) != 20:
        raise ValueError("Unsupported QA fixture")
    # Check database identity read-only, before importing anything that can write.
    import sqlite3
    database = root / "data" / "reno.db"
    if database.resolve(strict=True) != database:
        raise ValueError("QA database must not link outside its declared path")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as con:
        identity = con.execute("SELECT value FROM meta WHERE key='qa_instance_id'").fetchone()
    if identity is None or identity[0] != manifest["qa_instance_id"]:
        raise ValueError("QA database does not match manifest")
    return manifest


def configure_environment(manifest: dict):
    # Clear inherited provider credentials, paths, role and arbitrary overrides
    # before reno.config can be imported in THIS service process.
    for key in list(os.environ):
        if key.startswith("RENO_") or key in {
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY",
        }:
            os.environ.pop(key)
    os.environ.update(RENO_QA_ROOT=manifest["root"],
                      RENO_CONFIG_FILE=manifest["config_file"],
                      RENO_DATA_DIR=manifest["data_dir"], RENO_LLM_TESTS="0")


class StubMiss(RuntimeError):
    """An unexpected model request must fail instead of making a network call."""


def install_model_stubs(manifest: dict):
    from reno import llm
    fixtures = json.loads(Path(manifest["questions_file"]).read_text(encoding="utf-8"))
    atoms = {item["id"]: item for item in fixtures["atoms"]}
    questions = {text: item for item in fixtures["questions"]
                 for text in [item["question"], *item.get("aliases", [])]}

    def refuse(*args, **kwargs):
        misses = _request_misses.get()
        if misses is not None:
            misses.append("qa_model_stub_miss")
        raise StubMiss("qa_model_stub_miss: no registered synthetic model response")

    def chat(messages, model=None, **kwargs):
        if model != "qa-fixture" or kwargs.get("json_mode", True):
            return refuse()
        user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        if not isinstance(user, str) or "## 当前问题\n" not in user:
            return refuse()
        question = user.rsplit("## 当前问题\n", 1)[1].split("\n##", 1)[0].strip()
        if manifest.get("derivation_fixture") and question == "P04 防水方案甲和乙有什么差别？":
            found = [match.group(1) for line in user.splitlines()
                     if (match := re.match(r"^\[(\d+)\] .*P04", line))]
            if not found:
                return refuse()
            return ("人工关联检查回答 " + " ".join(f"[{number}]" for number in found[:4])
                    + "。请核对历史关联；这是合成模型响应。", "qa-fixture")
        item = questions.get(question)
        if item is None:
            return refuse()
        if question == "延迟回答：卫生间防水怎么做？":
            import time
            time.sleep(1.5)
        cited = []
        for atom_id in item["expected_atom_ids"]:
            claim = atoms[atom_id]["claim"]
            for line in user.splitlines():
                match = re.match(r"^\[(\d+)\] ", line)
                if match and claim in line:
                    cited.append(f"{claim} [{match[1]}]")
                    break
        if not cited:
            return refuse()
        answer = "\n\n".join(cited)
        answer += "\n\n需确认：" + "、".join(item["required_conditions"]) + "。"
        answer += "\n\n此回答使用人工验收样本，不代表真实标准、价格或模型判断。"
        return answer, "qa-fixture"

    llm.chat = chat
    llm.chat_json = refuse
    llm._post = refuse
    llm._post_anthropic = refuse
    if manifest.get("derivation_fixture"):
        marker = manifest["derivation_fixture"]
        if marker.get("version") != 1 or marker.get("synthetic") is not True:
            raise ValueError("Invalid synthetic derivation fixture")
        from reno import judge
        trace_path = Path(manifest["root"]) / "derivation-model-calls.json"

        def synthetic_judge(candidates, batch_size=15):
            trace = json.loads(trace_path.read_text()) if trace_path.exists() else {"calls": 0}
            trace["calls"] += 1
            trace["candidate_count"] = len(candidates)
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            judgments = []
            for candidate in candidates:
                same_pair = (candidate["a"].startswith("p04_") and candidate["b"].startswith("p04_")
                             and candidate["a"].rsplit("_", 1)[0] == candidate["b"].rsplit("_", 1)[0])
                judgments.append({"pair_id": candidate["pair_id"], "relation": "conflicting" if same_pair else "different",
                                  "conflict_type": "method_conflict", "reason": "P04显式重算的合成判断"})
            return judgments, "qa-derivation"

        judge._judge_llm = synthetic_judge


def create_fixture_app(manifest: dict, *, dist: Path | None = None):
    if dist is not None:
        dist = dist.resolve(strict=True)
        if not dist.is_dir() or not (dist / "index.html").is_file() or not (dist / "assets").is_dir():
            raise ValueError("QA frontend must be an existing Vite build")
    configure_environment(manifest)
    sys.path.insert(0, str(REPO))
    if "reno.config" in sys.modules:
        raise RuntimeError("QA environment must be configured before importing reno.config")
    from reno import config, db
    config.get()
    db.initialize()
    install_model_stubs(manifest)
    from app import main as app_main
    if dist is not None:
        app_main.DIST = dist
        app = app_main.create_app()
    else:
        app = app_main.app
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def qa_identity(request, call_next):
        misses = []
        context_token = _request_misses.set(misses)
        try:
            response = await call_next(request)
            # The production ask service may catch a model error and degrade.
            # QA must expose unexpected calls as an explicit failure instead.
            if misses:
                response = JSONResponse({"detail": "qa_model_stub_miss"}, status_code=500)
            response.headers["X-Reno-QA-Instance"] = manifest["qa_instance_id"]
            if request.url.path == "/api/health":
                response.headers["X-Reno-QA-Fixture"] = manifest["fixture_version"]
            return response
        finally:
            _request_misses.reset(context_token)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dist", type=Path, help="Use an isolated frontend build without replacing production dist")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    # Reserve the actual listening socket first, closing the race between a
    # free-port probe and uvicorn startup. Never fall back to another port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # A previous isolated service may leave accepted connections in
            # TIME_WAIT. Reuse that origin, while retaining one live listener.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", manifest["port"]))
        listener.listen(128)
        import uvicorn
        app = create_fixture_app(manifest, dist=args.dist)
        print(json.dumps({"qa_instance_id": manifest["qa_instance_id"],
                          "root": manifest["root"], "port": manifest["port"]}), flush=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=manifest["port"], log_level="warning"))
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()

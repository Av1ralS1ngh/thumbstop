"""Thumbstop hosted app: a small Starlette server behind the single-page UI in thumbstop/web.

    .venv/bin/uvicorn server:app --port 8501        # then open http://localhost:8501/?demo=1

POST /api/upload takes a zip (kept in memory for an hour, never written to disk). POST /api/run starts a run in a
background thread and GET /api/jobs/<id> reports its stage, log and, at the end, the results the page renders.
Each run works in a temporary folder that is deleted when it ends, and API keys are used for that one run only.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from thumbstop import hosted
from thumbstop.llm import load_config
from thumbstop.pipeline import Options

WEB = Path(__file__).resolve().parent / "thumbstop" / "web"
MAX_UPLOAD = 50 * 1024 * 1024
KEEP_S = 3600                      # uploads and finished jobs are dropped after an hour
POOL = ThreadPoolExecutor(max_workers=2)
JOBS: dict[str, dict] = {}
UPLOADS: dict[str, dict] = {}
LOCK = threading.Lock()
# pipeline log lines -> how far the run has got (0 read, 1 mark, 2 cut, 3 check, 4 rank, 5 done)
STAGES = (("Show:", 1), ("[1/5]", 1), ("[2/5]", 2), ("[3/5]", 3), ("[4/5]", 3), ("[5/5]", 4), ("Done in", 5))
NO_STORE = {"Cache-Control": "no-store"}


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status, headers=NO_STORE)


def _sweep() -> None:
    now = time.time()
    for store in (JOBS, UPLOADS):
        for k in [k for k, v in store.items() if now - v["at"] > KEEP_S and v.get("state") != "running"]:
            store.pop(k, None)


def _have_aws_login() -> bool:
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def _settings(mode: str, body: dict) -> tuple[Options, dict[str, str | None]]:
    """Options and per-run environment from the request; raises ValueError with a message for the page."""
    if mode == hosted.RECORDED:
        return hosted.recorded_options(), {}
    s = body.get("settings") or {}
    top = int(s.get("top") or 20)
    lo, hi = float(s.get("min") or 20), float(s.get("max") or 60)
    if not (1 <= top <= 50):
        raise ValueError("Top N must be between 1 and 50.")
    if not (5 <= lo < hi <= 180):
        raise ValueError("The length range must be between 5 and 180 seconds, shortest first.")
    episodes = hosted.parse_range(str(s.get("episodes") or ""))
    fast = bool(s.get("fast"))
    opt = Options(top=top, gate_n=max(12, top + 4) if fast else max(40, top + 4), repair=not fast, episodes=episodes,
                  min_len=lo, max_len=hi, seeds=not fast)
    k = body.get("keys") or {}
    env: dict[str, str | None] = {}
    if mode == hosted.BEDROCK:
        env["AWS_REGION"] = (k.get("region") or "us-east-1").strip()
        if k.get("key_id") and k.get("secret"):
            env |= {"AWS_ACCESS_KEY_ID": k["key_id"].strip(), "AWS_SECRET_ACCESS_KEY": k["secret"].strip(),
                    "AWS_SESSION_TOKEN": (k.get("token") or "").strip() or None, "AWS_PROFILE": None}
        elif not _have_aws_login():
            raise ValueError("Enter your AWS access keys: this server has no AWS login of its own.")
    elif mode == hosted.CUSTOM:
        base, model = (k.get("base_url") or "").strip(), (k.get("model") or "").strip()
        if not (base and model):
            raise ValueError("Enter the API's base URL and the model name.")
        env = {"LLM_BASE_URL": base, "LLM_MODEL": model, "LLM_API_KEY": (k.get("api_key") or "").strip() or "none"}
    return opt, env


def _work(job: dict, mode: str, env: dict, opt: Options, zip_bytes: bytes | None, name: str) -> None:
    def log(msg: object) -> None:
        for line in str(msg).splitlines():
            job["log"].append(line[:300])
            if "falling back to rules" in line:
                job["fallbacks"] += 1
            for key, stage in STAGES:
                if line.strip().startswith(key):
                    job["stage"] = max(job["stage"], stage)
        del job["log"][:-200]

    try:
        res = hosted.execute(mode, env, opt, zip_bytes, name, log)
        job.update(result=res, stage=5, state="done")
    except hosted.NotRecorded as e:
        job.update(state="error", kind="not_recorded", error=str(e))
    except (Exception, SystemExit) as e:   # the pipeline exits with SystemExit on an empty episode range
        job.update(state="error", kind="failed", error=f"{type(e).__name__}: {str(e)[:400]}")
    finally:
        job["at"] = time.time()


async def index(request: Request) -> Response:
    return FileResponse(WEB / "index.html", headers=NO_STORE)


async def health(request: Request) -> Response:
    return PlainTextResponse("ok", headers=NO_STORE)


async def info(request: Request) -> Response:
    return JSONResponse({
        "modes": [{"id": k, "label": label, "desc": desc} for k, (label, desc) in hosted.MODES.items()],
        "default": hosted.RECORDED if hosted.DEMO_ANSWERS.exists() else hosted.OFFLINE,
        "recorded_covers_pack": hosted.has_extra_answers(),
        "roles": load_config().get("roles", {}),
        "demo": {"name": hosted.DEMO_NAME, "episodes": len(hosted.DEMO_EPISODES)},
    }, headers=NO_STORE)


async def upload(request: Request) -> Response:
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        return _err(413, "The zip is larger than 50 MB.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD:
            return _err(413, "The zip is larger than 50 MB.")
    try:
        count = hosted.episode_files(bytes(body))
    except ValueError as e:
        return _err(400, str(e)[:1].upper() + str(e)[1:] + ".")
    name = Path(request.headers.get("x-filename") or "scripts.zip").name[:120]
    uid = secrets.token_urlsafe(16)
    with LOCK:
        _sweep()
        UPLOADS[uid] = {"data": bytes(body), "name": name, "at": time.time()}
    return JSONResponse({"upload_id": uid, "name": name, "size": len(body), "episodes": count}, headers=NO_STORE)


async def start(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return _err(400, "Bad request.")
    mode = body.get("mode")
    if mode not in hosted.MODES:
        return _err(400, "Pick a model.")
    zip_bytes, name = None, hosted.DEMO_NAME
    if body.get("source") == "upload":
        up = UPLOADS.get(str(body.get("upload_id") or ""))
        if not up:
            return _err(400, "Please upload the zip again; the server no longer has it.")
        zip_bytes = up["data"]
        name = re.sub(r"[^\w\- ]+", "_", Path(up["name"]).stem)[:60].strip() or "show"
    try:
        opt, env = _settings(mode, body)
    except ValueError as e:
        return _err(400, str(e))
    jid = secrets.token_urlsafe(12)
    job = {"state": "running", "stage": 0, "log": [], "fallbacks": 0, "result": None, "error": None, "kind": None,
           "at": time.time(), "mode": mode}
    with LOCK:
        _sweep()
        JOBS[jid] = job
    POOL.submit(_work, job, mode, env, opt, zip_bytes, name)
    return JSONResponse({"job_id": jid}, headers=NO_STORE)


async def status(request: Request) -> Response:
    job = JOBS.get(request.path_params["jid"])
    if not job:
        return _err(404, "This run has expired. Please run it again.")
    out = {k: job[k] for k in ("state", "stage", "error", "kind", "fallbacks", "mode")} | {"log": job["log"][-14:]}
    if job["state"] == "done":
        res = job["result"]
        meta = res["doc"]["meta"]
        out["result"] = {"view": res["view"], "same_family": res["same_family"], "files": list(hosted.OUTPUTS),
                         "meta": {k: meta.get(k) for k in ("models", "usage", "seconds", "funnel", "ingest")}}
    return JSONResponse(out, headers=NO_STORE)


async def download(request: Request) -> Response:
    job, name = JOBS.get(request.path_params["jid"]), request.path_params["name"]
    if not job or job["state"] != "done" or name not in hosted.OUTPUTS:
        return _err(404, "Not found.")
    return Response(job["result"]["files"][name], media_type=hosted.OUTPUTS[name],
                    headers=NO_STORE | {"Content-Disposition": f'attachment; filename="{name}"'})


class Headers(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp


app = Starlette(routes=[
    Route("/", index),
    Route("/health", health),
    Route("/api/info", info),
    Route("/api/upload", upload, methods=["POST"]),
    Route("/api/run", start, methods=["POST"]),
    Route("/api/jobs/{jid}", status),
    Route("/api/jobs/{jid}/files/{name}", download),
    Mount("/assets", StaticFiles(directory=WEB), name="assets"),
], middleware=[Middleware(Headers)])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8501")))

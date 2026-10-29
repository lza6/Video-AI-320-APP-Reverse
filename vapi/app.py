"""Video AI (com.codeway.videoapp) 本地网关 + Demo（R1）。

模式：
  MOCK  本地模拟流水线，不触达上游（用于 Demo 与压测）
  LIVE  真实 Firestore 任务链路（写 videos 文档 + 轮询）
  AUTO  上游可用时 LIVE，不可用时降级 MOCK

启动：
  python -m vapi.app                # 默认 AUTO，端口 8979
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import cerebro, firestore

PROXY = os.getenv("VAPI_PROXY", "http://127.0.0.1:10808")
MODE = os.getenv("VAPI_MODE", "auto").lower()
PORT = int(os.getenv("VAPI_PORT", "8979"))
LIVE_STUCK_SECONDS = int(os.getenv("VAPI_LIVE_STUCK", "90"))

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

app = FastAPI(title="Video AI v3.2.0 Gateway (R1)", version="0.1.0")

_clients: dict[str, Any] = {}


def _cerebro() -> cerebro.CerebroClient:
    if "cerebro" not in _clients:
        _clients["cerebro"] = cerebro.CerebroClient(proxy=PROXY)
    return _clients["cerebro"]


def _fs() -> firestore.FirestoreClient:
    if "fs" not in _clients:
        _clients["fs"] = firestore.FirestoreClient(proxy=PROXY)
    return _clients["fs"]

_state: dict[str, Any] = {
    "uid": None,
    "config": None,
    "config_at": 0.0,
    "upstream": {"config": None, "user": None, "firestore": None},
}
_jobs: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------- 模型
class UserReq(BaseModel):
    uid: str | None = None


class JobReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    style_id: str | None = None
    mode: str | None = None
    guidance_scale: float | None = 7.5
    num_frames: int | None = 121
    num_inference_steps: int | None = 30


# ---------------------------------------------------------------- 工具
def _resolved_mode(requested: str | None) -> str:
    if requested and requested.lower() in {"mock", "live"}:
        return requested.lower()
    if MODE in {"mock", "live"}:
        return MODE
    return "live" if _state["upstream"]["config"] == "ok" else "mock"


async def _ensure_uid(uid: str | None = None) -> str:
    if uid:
        _state["uid"] = uid
        return uid
    if _state["uid"]:
        return _state["uid"]
    return await _register_user(None)


async def _register_user(uid: str | None) -> str:
    res = await asyncio.to_thread(_cerebro().register_user, uid)
    _state["upstream"]["user"] = "ok" if res.ok else f"http-{res.status}"
    if not res.ok:
        raise HTTPException(502, f"cerebro /user/android failed: {res.status} {res.body[:200].decode(errors='replace')}")
    _state["uid"] = uid or _state["uid"] or str(uuid.uuid4())
    return _state["uid"]


async def _ensure_config(uid: str) -> dict[str, Any]:
    if _state["config"] and time.time() - _state["config_at"] < 300:
        return _state["config"]
    payload = await asyncio.to_thread(_cerebro().fetch_config, uid)
    _state["config"] = payload
    _state["config_at"] = time.time()
    _state["upstream"]["config"] = "ok"
    return payload


def _config_summary(payload: dict[str, Any]) -> dict[str, Any]:
    def _json(key: str) -> Any:
        raw = payload.get(key)
        if not isinstance(raw, str):
            return raw
        import json

        try:
            return json.loads(raw)
        except ValueError:
            return raw

    templates = _json("all_templates") or {}
    styles = _json("all_styles") or {}
    return {
        "app": "com.codeway.videoapp",
        "premium_flag": payload.get("premium"),
        "pricing_tier": payload.get("pricing_tier"),
        "min_version": payload.get("min_version"),
        "ai_models": sorted({t.get("ai_model") for t in templates.values() if isinstance(t, dict) and t.get("ai_model")}),
        "templates": len(templates),
        "styles": len(styles) if isinstance(styles, dict) else 0,
        "txt2vid": _json("txt2vid_configuration"),
        "credit_limits": _json("credit_limit_configuration"),
        "ai_model_used_credit_counts": _json("ai_model_used_credit_counts"),
        "ai_model_remaining_times": _json("ai_model_remaining_times"),
        "deforum_durations": _json("deforum_durations"),
        "style_sample": list(styles.items())[:3] if isinstance(styles, dict) else [],
    }


# ---------------------------------------------------------------- 本地模拟流水线
async def _mock_pipeline(job: dict[str, Any]) -> None:
    steps = [(0.35, "WAITING"), (1.1, "PROCESSING"), (2.4, "COMPLETED")]
    started = time.time()
    for delay, status in steps:
        await asyncio.sleep(delay)
        job["status"] = status
        job["updatedAt"] = time.time()
        job["elapsed"] = round(time.time() - started, 3)
        if status == "COMPLETED":
            job["outputVideoUrl"] = None
            job["preview"] = {
                "kind": "mock",
                "note": "MOCK 流水线：未触达上游，仅验证网关/协议/并发",
                "frames": 121,
                "prompt": job["prompt"],
                "styleId": job.get("styleId"),
            }


# ---------------------------------------------------------------- 路由
@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": MODE,
        "resolved": _resolved_mode(None),
        "uid": _state["uid"],
        "upstream": _state["upstream"],
        "jobs": len(_jobs),
        "proxy": PROXY,
    }


@app.post("/api/user")
async def api_user(req: UserReq) -> dict[str, Any]:
    uid = await _register_user(req.uid)
    return {"uid": uid, "upstream": _state["upstream"]["user"]}


@app.get("/api/config")
async def api_config(uid: str | None = None) -> dict[str, Any]:
    uid = await _ensure_uid(uid)
    try:
        payload = await _ensure_config(uid)
    except RuntimeError as exc:
        _state["upstream"]["config"] = "failed"
        raise HTTPException(502, str(exc)) from exc
    return {"uid": uid, "keys": len(payload), "summary": _config_summary(payload)}


@app.get("/api/config/raw")
async def api_config_raw(uid: str | None = None) -> JSONResponse:
    uid = await _ensure_uid(uid)
    payload = await _ensure_config(uid)
    return JSONResponse({"uid": uid, "payload": payload})


@app.post("/v1/jobs")
async def create_job(req: JobReq) -> dict[str, Any]:
    mode = _resolved_mode(req.mode)
    uid = await _ensure_uid(None)
    job_id = uuid.uuid4().hex
    job: dict[str, Any] = {
        "id": job_id,
        "mode": mode,
        "status": "CREATED",
        "prompt": req.prompt,
        "styleId": req.style_id,
        "cerebroId": uid,
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    _jobs[job_id] = job

    if mode == "mock":
        asyncio.create_task(_mock_pipeline(job))
        return {"job_id": job_id, "mode": "mock", "status": job["status"]}

    try:
        doc_id = await asyncio.to_thread(
            _fs().create_video_job,
            uid,
            req.prompt,
            style_id=req.style_id,
            guidance_scale=req.guidance_scale,
            num_frames=req.num_frames,
            num_inference_steps=req.num_inference_steps,
        )
        _state["upstream"]["firestore"] = "ok"
    except firestore.FirestoreError as exc:
        _state["upstream"]["firestore"] = f"http-{exc.status}"
        job.update(status="FAILED", error=f"firestore {exc.status}: {exc.body[:160].decode(errors='replace')}")
        return JSONResponse(status_code=502, content={"job_id": job_id, "mode": "live", "status": "FAILED", "error": job["error"]})

    job.update(mode="live", docId=doc_id)
    return {"job_id": job_id, "doc_id": doc_id, "mode": "live", "status": "CREATED"}


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")

    if job["mode"] == "mock":
        return job

    doc_id = job.get("docId")
    if not doc_id:
        return job
    try:
        doc = await asyncio.to_thread(_fs().get_video_job, doc_id)
    except firestore.FirestoreError as exc:
        raise HTTPException(502, f"firestore {exc.status}") from exc

    age = time.time() - job["createdAt"]
    status = doc.get("status") or "CREATED"
    job.update(
        status=status,
        doc=doc,
        elapsed=round(age, 1),
        error=doc.get("error"),
        outputVideoUrl=doc.get("outputVideoUrl"),
    )
    if status == "CREATED" and age > LIVE_STUCK_SECONDS:
        job["status"] = "BLOCKED_NO_WORKER"
        job["blocker"] = (
            "文档已写入但上游 Worker 未接管（见 docs/PROTOCOL.md §4.3）："
            "cerebro-users 用户文档无法创建（403）、Storage 上传 403，"
            "Worker 可能要求有效用户/积分或服务端私有 sig。"
        )
    return job


@app.get("/api/live/probe")
async def live_probe() -> dict[str, Any]:
    """复现 Firestore 规则证据（只读/可写探测，写入一个探测文档）。"""
    uid = await _ensure_uid(None)
    out: dict[str, Any] = {"uid": uid, "checks": {}}

    def _probe() -> dict[str, Any]:
        checks: dict[str, Any] = {}
        try:
            doc_id = _fs().create_video_job(uid, "probe")
            checks["create_videos_with_cerebroId"] = {"status": 200, "docId": doc_id}
            checks["get_videos_by_id"] = {"status": 200, "doc": _fs().get_video_job(doc_id).get("status")}
        except firestore.FirestoreError as exc:
            checks["create_videos_with_cerebroId"] = {"status": exc.status, "body": exc.body[:200].decode(errors="replace")}
        try:
            checks["get_cerebro_user"] = {"user": _fs().get_user(uid)}
        except firestore.FirestoreError as exc:
            checks["get_cerebro_user"] = {"status": exc.status}
        return checks

    out["checks"] = await asyncio.to_thread(_probe)
    return out


@app.get("/demo")
async def demo() -> FileResponse:
    return FileResponse(ROOT / "demo.html")


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "videoai-gateway", "demo": "/demo", "health": "/health", "docs": "/api/config"}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()

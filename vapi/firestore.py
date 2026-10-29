"""Firestore 任务客户端（REST，匿名）。

规则实测（2026-10，见 docs/PROTOCOL.md §4.2）：
  - create `videos` 必须带 cerebroId 字段，否则 403
  - 单文档 get 允许匿名；collection list 拒绝
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# 均从环境变量注入（原值由 APK 静态提取，见 docs/PROTOCOL.md §1/§4）
API_KEY = os.getenv("FIREBASE_API_KEY", "")
PROJECT = os.getenv("FIREBASE_PROJECT", "video-app-codeway")
SIG_SECRET = os.getenv("VIDEOAI_SIG_SECRET", "")   # libapp.so 内联的签名盐


def api_base() -> str:
    if not API_KEY:
        raise RuntimeError("缺少环境变量 FIREBASE_API_KEY（见 docs/PROTOCOL.md §1）")
    return f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
STATUS_CREATED = "CREATED"
STATUS_WAITING = "WAITING"
STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
TERMINAL = {STATUS_COMPLETED, STATUS_FAILED}


class FirestoreError(RuntimeError):
    def __init__(self, status: int, body: bytes):
        super().__init__(f"firestore {status}: {body[:200]!r}")
        self.status = status
        self.body = body


def make_sig(cerebro_id: str, created_at_ms: int) -> str:
    if not SIG_SECRET:
        raise RuntimeError("缺少环境变量 VIDEOAI_SIG_SECRET（见 docs/PROTOCOL.md §4）")
    raw = f"{SIG_SECRET}_{cerebro_id}_{created_at_ms}"
    return hashlib.sha256(raw.encode()).hexdigest().upper()


def build_video_fields(
    cerebro_id: str,
    prompt: str,
    ai_model: str = "txt2vid",
    style_id: str | None = None,
    txt2vid_model: str = "fal_ai_stable_video",
    guidance_scale: float | None = 7.5,
    num_inference_steps: int | None = 30,
    num_frames: int | None = 121,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造与 App 一致的 Firestore 字段（Firestore REST 值类型包装）。"""
    created_at = int(time.time() * 1000)
    fields: dict[str, Any] = {
        "createdAt": {"integerValue": str(created_at)},
        "aiModel": {"stringValue": ai_model},
        "cerebroId": {"stringValue": cerebro_id},
        "prompts": {"arrayValue": {"values": [{"stringValue": prompt}]}},
        "sig": {"stringValue": make_sig(cerebro_id, created_at)},
    }
    if ai_model == "txt2vid":
        fields["txt2vidModel"] = {"stringValue": txt2vid_model}
        if style_id:
            fields["styleId"] = {"stringValue": style_id}
        if guidance_scale is not None:
            fields["guidanceScale"] = {"doubleValue": float(guidance_scale)}
        if num_inference_steps is not None:
            fields["numInferenceSteps"] = {"integerValue": str(int(num_inference_steps))}
        if num_frames is not None:
            fields["numFrames"] = {"integerValue": str(int(num_frames))}
    elif style_id:
        fields["styleId"] = {"stringValue": style_id}
    for key, value in (extra or {}).items():
        fields[key] = value
    return fields


def _unwrap(value: dict[str, Any]) -> Any:
    """Firestore REST 值 -> Python 值（递归）。"""
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "timestampValue" in value:
        return value["timestampValue"]
    if "nullValue" in value:
        return None
    if "arrayValue" in value:
        return [_unwrap(v) for v in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return {k: _unwrap(v) for k, v in value["mapValue"].get("fields", {}).items()}
    return value


def flatten_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: _unwrap(v) for k, v in (doc.get("fields") or {}).items()}


class FirestoreClient:
    def __init__(self, proxy: str | None = None, timeout: float = 25.0):
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPSHandler(context=ssl.create_default_context())
        ]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self._open = urllib.request.build_opener(*handlers).open
        self.timeout = timeout

    def _call(self, url: str, body: dict[str, Any] | None = None, method: str = "GET") -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"content-type": "application/json", "accept-encoding": "gzip"},
        )
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise FirestoreError(exc.code, exc.read()) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FirestoreError(0, repr(exc).encode()) from exc
        if raw[:2] == b"\x1f\x8b":
            import gzip

            raw = gzip.decompress(raw)
        return json.loads(raw)

    # ---- 任务 -------------------------------------------------------------
    def create_job(self, collection: str, fields: dict[str, Any]) -> str:
        doc = self._call(f"{api_base()}/{collection}?key={API_KEY}", {"fields": fields}, "POST")
        return doc["name"].rsplit("/", 1)[-1]

    def create_video_job(self, cerebro_id: str, prompt: str, **kwargs: Any) -> str:
        return self.create_job("videos", build_video_fields(cerebro_id, prompt, **kwargs))

    def get_job(self, collection: str, doc_id: str) -> dict[str, Any]:
        doc = self._call(f"{api_base()}/{collection}/{doc_id}?key={API_KEY}")
        flat = flatten_doc(doc)
        flat["_createTime"] = doc.get("createTime")
        flat["_updateTime"] = doc.get("updateTime")
        flat["_id"] = doc_id
        return flat

    def get_video_job(self, doc_id: str) -> dict[str, Any]:
        return self.get_job("videos", doc_id)

    def get_user(self, cerebro_id: str) -> dict[str, Any] | None:
        """读 cerebro-users/{id}；不存在返回 None（单文档读匿名可用）。"""
        try:
            return self.get_job("cerebro-users", cerebro_id)
        except FirestoreError as exc:
            if exc.status == 404:
                return None
            raise


def random_cerebro_id() -> str:
    return str(uuid.uuid4())

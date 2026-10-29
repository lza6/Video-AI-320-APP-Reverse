"""Cerebro (Codeway) 网关客户端：配置拉取 + 用户上报，含 HMAC 签名。

协议来源：blutter 反汇编 `package:cerebro/cerebro.dart`
    HelperExtension.generateToken / CerebroConfigService._configUrl / UserService.setUserData
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

CONFIG_URL_RELEASE = "https://config-api.cdwapi.com/android"
USER_URL_RELEASE = "https://gateway.cerebroapi.com/user/android"
LOG_URL_RELEASE = "https://gateway.cerebroapi.com/log/android"

# 敏感常量从环境变量注入（原值由 libapp.so 静态提取，见 docs/PROTOCOL.md §2）
APP_ID = os.getenv("VIDEOAI_APP_ID", "com.codeway.videoapp")
API_KEY = os.getenv("CEREBRO_API_KEY", "")      # 请求头 authorization 固定值
HMAC_KEY = os.getenv("CEREBRO_HMAC_KEY", "")    # 签名密钥
APP_VERSION = os.getenv("VIDEOAI_APP_VERSION", "3.2.0")


def require_keys() -> None:
    missing = [n for n, v in (("CEREBRO_API_KEY", API_KEY), ("CEREBRO_HMAC_KEY", HMAC_KEY)) if not v]
    if missing:
        raise RuntimeError(f"缺少环境变量: {', '.join(missing)}（从 APK 的 libapp.so 提取，见 docs/PROTOCOL.md）")


@dataclass
class CerebroResult:
    status: int
    body: bytes
    data: Any = None

    @property
    def ok(self) -> bool:
        return self.status == 200


def _opener(proxy: str | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPSHandler(context=ssl.create_default_context())
    ]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def sign_token(body_json: str, ts_ms: str, path: str) -> str:
    """token = HMAC_SHA256(HMAC_KEY, f"{body}_{API_KEY}_{ts}_{path}") -> hex"""
    require_keys()
    msg = f"{body_json}_{API_KEY}_{ts_ms}_{path}"
    return hmac.new(HMAC_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()


class CerebroClient:
    def __init__(self, proxy: str | None = None, timeout: float = 20.0):
        require_keys()
        self._open = _opener(proxy).open
        self.timeout = timeout
        self.proxy = proxy

    # ---- 低层 -------------------------------------------------------------
    def _request(self, url: str, data: bytes | None, headers: dict[str, str], method: str) -> CerebroResult:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return CerebroResult(resp.status, raw, _maybe_json(raw))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return CerebroResult(exc.code, raw, _maybe_json(raw))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return CerebroResult(0, repr(exc).encode(), None)

    # ---- 远程配置 ---------------------------------------------------------
    def fetch_config(
        self,
        uid: str,
        version: str = APP_VERSION,
        language: str = "en",
        country: str = "US",
    ) -> dict[str, Any]:
        """GET config-api：不需要签名，uid 任意 UUID 即可。返回 payload dict。"""
        query = urllib.parse.urlencode(
            {"aid": APP_ID, "uid": uid, "version": version, "language": language, "country": country}
        )
        res = self._request(
            f"{CONFIG_URL_RELEASE}?{query}",
            None,
            {"content-type": "application/json", "accept-encoding": "gzip", "user-agent": "Dart/3.5 (dart:io)"},
            "GET",
        )
        if res.status != 200 or not isinstance(res.data, dict):
            raise RuntimeError(f"config-api failed: {res.status} {res.body[:200]!r}")
        payload = res.data.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("config-api: payload missing")
        return payload

    # ---- 用户上报 ---------------------------------------------------------
    def register_user(self, uid: str | None = None, version: str = APP_VERSION) -> CerebroResult:
        """POST /user/android（HMAC 签名）。实测任意 UUID 返回 200 Successful。"""
        uid = uid or str(uuid.uuid4())
        payload = {
            "app_id": APP_ID,
            "version": version,
            "user_id": uid,
            "vendor_id": uid,
            "advertising_id": uid,
            "tracker_name": "Organic",
            "operating_system": "android",
            "operating_system_version": "13",
            "time_zone_offset_seconds": 0,
            "language": "en",
            "mobile_brand_name": "samsung",
            "mobile_os_hardware_model": "SM-G991B",
            "device_region": "US",
            "set_user": True,
        }
        return self.post_signed(USER_URL_RELEASE, "/user/android", payload)

    def post_signed(self, url: str, path: str, payload: dict[str, Any]) -> CerebroResult:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        ts = str(int(time.time() * 1000))
        headers = {
            "authorization": API_KEY,
            "rs": sign_token(body, ts, path),
            "ts": ts,
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/4.12.0",
        }
        return self._request(url, body.encode(), headers, "POST")


def _maybe_json(raw: bytes) -> Any:
    import gzip

    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


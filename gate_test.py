"""R2: 验证 Worker 的账号门禁（不做任何余额伪造）。

步骤：
  1) 用与任务文档相同的 sig 规则，在 cerebro-users/{自己生成的UUID} 建一个 credits=0 的账号记录
  2) 提交任务文档
  3) 轮询观察 Worker 是否接管（预期：接管并因积分不足失败 → 证明门禁=用户/积分）
"""

import json
import ssl
import time
import hashlib
import uuid
import urllib.error
import urllib.request

import os

PROXY = os.getenv("VAPI_PROXY", "http://127.0.0.1:10808")
KEY = os.getenv("FIREBASE_API_KEY", "")
PROJECT = os.getenv("FIREBASE_PROJECT", "video-app-codeway")
SALT = os.getenv("VIDEOAI_SIG_SECRET", "")
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"

op = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)


def sig_of(cid: str, ts: int) -> str:
    return hashlib.sha256(f"{SALT}_{cid}_{ts}".encode()).hexdigest().upper()


def call(url, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                headers={"content-type": "application/json"})
    try:
        with op.open(req, timeout=25) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return 0, repr(exc).encode()


def main() -> None:
    cid = str(uuid.uuid4())
    ts = int(time.time() * 1000)
    user_doc = {
        "cerebroId": {"stringValue": cid},
        "createdAt": {"integerValue": str(ts)},
        "updatedAt": {"integerValue": str(ts)},
        "credits": {"integerValue": "0"},
        "sig": {"stringValue": sig_of(cid, ts)},
    }
    st, raw = call(f"{BASE}/cerebro-users/{cid}?key={KEY}", {"fields": user_doc}, "PATCH")
    print("user doc create:", st, raw[:120], flush=True)

    st, raw = call(f"{BASE}/cerebro-users/{cid}?key={KEY}")
    print("user doc read  :", st, raw[:200], flush=True)

    jts = int(time.time() * 1000)
    job = {
        "createdAt": {"integerValue": str(jts)},
        "aiModel": {"stringValue": "txt2vid"},
        "txt2vidModel": {"stringValue": "fal_ai_stable_video"},
        "prompts": {"arrayValue": {"values": [{"stringValue": "a neon cat walking in rain"}]}},
        "cerebroId": {"stringValue": cid},
        "sig": {"stringValue": sig_of(cid, jts)},
    }
    st, raw = call(f"{BASE}/videos?key={KEY}", {"fields": job}, "POST")
    doc_id = json.loads(raw)["name"].rsplit("/", 1)[-1] if st == 200 else None
    print("job create     :", st, doc_id, flush=True)
    if not doc_id:
        return

    start = time.time()
    for _ in range(12):
        time.sleep(15)
        st, raw = call(f"{BASE}/videos/{doc_id}?key={KEY}")
        try:
            d = json.loads(raw)
            f = d.get("fields", {})
            print(f"[{int(time.time()-start)}s] status={(f.get('status') or {}).get('stringValue')} "
                  f"error={(f.get('error') or {}).get('stringValue')} upd={d.get('updateTime')} "
                  f"keys={sorted(f)}", flush=True)
        except Exception:  # noqa: BLE001
            print(f"[{int(time.time()-start)}s] {st}", flush=True)


if __name__ == "__main__":
    main()

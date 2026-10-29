"""协议单元测试（无网络）：签名与任务文档构造。"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试用假密钥（真实值从 APK 提取，仓库内不落盘）
os.environ.setdefault("CEREBRO_API_KEY", "TEST_API_KEY")
os.environ.setdefault("CEREBRO_HMAC_KEY", "TEST_HMAC_KEY")
os.environ.setdefault("FIREBASE_API_KEY", "TEST_FIREBASE_KEY")
os.environ.setdefault("VIDEOAI_SIG_SECRET", "TEST_SIG_SECRET")

from vapi import cerebro, firestore  # noqa: E402


def test_sign_token_shape_and_sensitivity() -> None:
    token = cerebro.sign_token('{"a":1}', "1700000000000", "/user/android")
    assert len(token) == 64 and token.islower()
    assert token == cerebro.sign_token('{"a":1}', "1700000000000", "/user/android")
    assert token != cerebro.sign_token('{"a":2}', "1700000000000", "/user/android")
    assert token != cerebro.sign_token('{"a":1}', "1700000000001", "/user/android")
    assert token != cerebro.sign_token('{"a":1}', "1700000000000", "/receipt/verify")


def test_sign_token_matches_reference_formula() -> None:
    # 独立参考实现：HMAC_SHA256(key, f"{body}_{apiKey}_{ts}_{path}").hex()
    body, ts, path = '{"a":1}', "1700000000000", "/user/android"
    expect = hmac.new(cerebro.HMAC_KEY.encode(), f"{body}_{cerebro.API_KEY}_{ts}_{path}".encode(), hashlib.sha256).hexdigest()
    assert cerebro.sign_token(body, ts, path) == expect


def test_make_sig_uppercase_hex() -> None:
    sig = firestore.make_sig("uid-1", 1700000000000)
    assert len(sig) == 64 and sig == sig.upper()
    assert sig == firestore.make_sig("uid-1", 1700000000000)
    assert sig != firestore.make_sig("uid-2", 1700000000000)


def test_build_video_fields_matches_app_shape() -> None:
    fields = firestore.build_video_fields("uid-1", "a cat", style_id="s12")
    assert fields["cerebroId"]["stringValue"] == "uid-1"
    assert fields["aiModel"]["stringValue"] == "txt2vid"
    assert fields["txt2vidModel"]["stringValue"] == "fal_ai_stable_video"
    assert fields["prompts"]["arrayValue"]["values"][0]["stringValue"] == "a cat"
    assert fields["styleId"]["stringValue"] == "s12"
    assert int(fields["createdAt"]["integerValue"]) > 0
    # 关键：规则要求 cerebroId，客户端不写 status（由 Worker 回写）
    assert "status" not in fields

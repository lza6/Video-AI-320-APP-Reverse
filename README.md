# Video AI v3.2.0 (com.codeway.videoapp) — R1 逆向交付

Codeway 出品 Flutter 应用（Wonder/Video AI 同族）。R1 完成**协议层全量还原 + 本地网关 + Demo + 压测**，
真实出片链路卡在账号/素材授权（详见 `docs/PROTOCOL.md` §4.3、§6）。

## 1. 一句话结论

- 该 App **没有传统 REST 生成接口**：任务是通过**匿名写入 Firestore `videos`/`images` 文档**提交，
  服务端 Worker 异步回写 `status` 与产物地址；客户端用单文档监听读结果。
- 三条链路全部打通并可复现：**远程配置（免鉴权）→ 用户 HMAC 上报 → Firestore 任务读写**。
- **未打通的是"Worker 真出片"**：`cerebro-users` 用户文档不可写（403）、Storage 上传 403（403/402），
  实测三种任务形态（bare / WAITING / 带 inputs）在 200s 内均未被 Worker 接管。

## 2. 目录

> 仓库内**不包含任何密钥**：`CEREBRO_API_KEY` / `CEREBRO_HMAC_KEY` / `FIREBASE_API_KEY` /
> `VIDEOAI_SIG_SECRET` 全部走环境变量，提取位置见 `docs/PROTOCOL.md` §7。

```
docs/PROTOCOL.md      # 完整协议还原（端点/签名/文档结构/规则实测/证据等级）
vapi/cerebro.py       # config-api + HMAC 签名的 /user/android、/log/android
vapi/firestore.py     # Firestore REST 任务客户端（create/get/轮询/状态机常量）
vapi/app.py           # FastAPI 网关（MOCK/LIVE/AUTO）+ /api/live/probe 规则探针
demo.html             # Demo 页（配置摘要 + 提交 + 轮询 + 结果展示）
stress_test.py        # 压测（MOCK 全链路 / 创建吞吐）
e2e_demo.py           # Playwright 真浏览器验证
tests/test_protocol.py# 签名与任务文档结构单测
artifacts/            # 浏览器截图
watch_jobs.log        # LIVE 任务 200s 无 Worker 接管的观察日志（证据）
config_raw.json       # 远端配置原始响应（51 键）
```

## 3. 运行

```bash
pip install -r requirements.txt
cp .env.example .env         # 填入从 APK 提取的常量（见 docs/PROTOCOL.md §7）
set -a; source .env; set +a  # Windows: 用 $env:VAR="x" 逐个设置

python -m vapi.app           # 打开 http://127.0.0.1:8979/demo
python stress_test.py --url http://127.0.0.1:8979 --concurrency 16 --total 160 --no-verify
python e2e_demo.py
```

`VAPI_MODE`: `mock`（本地流水线，不触达上游）/ `live`（真实 Firestore）/ `auto`（默认，配置可用即 live）。
`VAPI_PROXY`: 走本地代理（默认 `http://127.0.0.1:10808`，本机访问上游必需）。

## 4. 网关 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 模式 / uid / 上游状态 |
| POST | `/api/user` | 注册设备用户（HMAC 签名，实测 200） |
| GET | `/api/config` | 远端配置摘要（106 模板 / 26 风格 / 5 个 ai_model / 积分策略） |
| GET | `/api/config/raw` | 51 键原始 payload |
| POST | `/v1/jobs` | `{prompt, style_id?, mode}` → `{job_id, doc_id?, mode}` |
| GET | `/v1/jobs/{id}` | 轮询：mock 走本地状态机；live 读 Firestore，超过 90s 未接管标记 `BLOCKED_NO_WORKER` |
| GET | `/api/live/probe` | 复现规则证据（videos 可写/可读、cerebro-users 拒绝） |
| GET | `/demo` | Demo 页 |

## 5. 关键协议速查

```
配置（免鉴权）
GET https://config-api.cdwapi.com/android?aid=com.codeway.videoapp&uid=<任一UUID>&version=3.2.0&language=en&country=US

用户上报（HMAC）
POST https://gateway.cerebroapi.com/user/android
  authorization: <CEREBRO_API_KEY>
  ts: <ms>   rs: HMAC_SHA256(<CEREBRO_HMAC_KEY>, f"{body}_{apiKey}_{ts}_/user/android").hex

任务（Firestore，匿名）
POST projects/video-app-codeway/databases/(default)/documents/videos
  fields: {createdAt, aiModel, cerebroId, prompts[], txt2vidModel|styleId, sig, ...}
  sig = SHA256((<SIG_SALT> + "_" + cerebroId + "_" + createdAtMs)).hex.upper()
  规则：缺 cerebroId → 403；单文档 GET 允许；collection list 拒绝
状态：CREATED → WAITING → PROCESSING → COMPLETED / FAILED
```

## 6. 卡点与下一轮

| 卡点 | 现象 | 可能原因 | 解除条件 |
|---|---|---|---|
| Worker 不接管任务 | 文档写入成功但 `updateTime` 不变 | ① 需要 `cerebro-users/{id}` 且有余量（我们 403 无法创建）② Worker 用私有密钥重算 sig ③ 需要隐藏字段（如 workflowId） | 真机/arm64 设备 + 合法订阅账号跑一次 img2img 抓包 |
| 素材无法上传 | Firebase Storage 403 / 402 | Storage 规则要求鉴权；App 内无 firebase_auth 代码（待确证其上传凭据来源） | 同上，或找到 App 的 Storage token 来源 |
| 无 arm64 设备 | 本机 AVD 均 x86_64，APK 仅 arm64-v8a | — | 物理机 / ARM 云机 |

new-api 渠道接入：**暂不落地**——当前只能做"配置/模板/任务提交"层，出片不通，接渠道无意义。
解除卡点后可直接复用 `vapi/firestore.py` 写 new-api channel（任务提交 + 轮询即可，无需异步队列改造）。

# new-api 对接交接书 — Video AI v3.2.0 (com.codeway.videoapp)

> 读者：**负责在 new-api 里落地渠道的会话**。
> 仓库：https://github.com/lza6/Video-AI-320-APP-Reverse
> 协议全量：本仓库 `docs/PROTOCOL.md`；规则/门禁证据：`docs/EVIDENCE.md`。

---

## 0. 先说结论（必读）

| 问题 | 答案 |
|---|---|
| 现在能接成**出片**渠道吗？ | **不能**。任务文档能写入、能按 id 读回，但服务端 Worker 不接管我们构造的任务（6 种形态 × 100–200s 全部零变化，见 EVIDENCE §5）。 |
| 现在能接成**元数据**渠道吗？ | 能（配置/模板/风格/积分策略），但那种渠道没有业务价值，不建议做。 |
| 什么时候可以接？ | 满足 §6「解卡判据」全部三条件之后。届时按 §3/§4/§5 直接写 `relay/channel/videoai/`，**渠道号现查（80 已被 Animefy 占用，建议 81）**。 |

**不要在 Worker 解卡之前写 relay 代码**，否则交付的是空壳渠道（提交成功但永远拿不到视频）。

---

## 1. 上游协议速查（实现时对照）

```
# 1) 远程配置（免鉴权，任意 UUID 即可）
GET  https://config-api.cdwapi.com/android?aid=com.codeway.videoapp&uid=<uuid>&version=3.2.0&language=en&country=US
→ {"status":{...},"payload":{ 51 keys }}

# 2) 用户上报（HMAC 签名）
POST https://gateway.cerebroapi.com/user/android
  authorization: <CEREBRO_API_KEY>
  ts: <epoch_ms>
  rs: HMAC_SHA256(<CEREBRO_HMAC_KEY>, f"{body}_{apiKey}_{ts}_/user/android").hex
  content-type: application/json

# 3) 建任务（Firestore REST，匿名；create-only）
POST https://firestore.googleapis.com/v1/projects/video-app-codeway/databases/(default)/documents/videos?key=<FIREBASE_API_KEY>
  fields:
    createdAt        integer(ms)
    aiModel          "txt2vid" | "img2vid" | "vid2vid" | "deforum" | "img2img"
    cerebroId        string  <UUID>            # 规则强制，缺失 → 403
    prompts          array<string>
    txt2vidModel     "fal_ai_stable_video"     # txt2vid 专用，可由远端配置覆盖
    guidanceScale / numInferenceSteps / numFrames
    sig              string  = SHA256("<SIG_SALT>_{cerebroId}_{createdAtMs}").hex.**upper()   # 规则真值校验
  → 200 {"name":".../documents/videos/{docId}"}

# 4) 查任务（单文档读，匿名）
GET  .../documents/videos/{docId}?key=<FIREBASE_API_KEY>
  → fields.status: CREATED | WAITING | PROCESSING | COMPLETED | FAILED
    fields.outputVideoUrl / outputVideoDurationInSeconds / thumbnailVideoUrl / error

# 不可用：collection list（403）、更新任务（403）、cerebro-users 之外的用户数据、Storage 上传（403/402）
```

关键常量（**不在仓库里**，按 `docs/PROTOCOL.md §7` 从 APK 提取）：
`CEREBRO_API_KEY` / `CEREBRO_HMAC_KEY` / `FIREBASE_API_KEY` / `SIG_SALT`。

---

## 2. 渠道形态选择

| 方案 | 形态 | 现在可行 | 说明 |
|---|---|---|---|
| A | `TaskAdaptor` 异步视频渠道（type=80） | ❌ 待解卡 | 与现有 AIVG / AIVM 同构：提交任务 → `FetchTask` 轮询 → 回填产物 URL |
| B | `Adaptor` 同步文本渠道 | ❌ | 上游没有同步接口 |
| C | 只读元数据渠道（模板/风格） | ✅ | 无生成能力，不接 |

选定方案 A。参照实现：`relay/channel/aivg/`（同步 t2v + 异步 t2i 混合）、`relay/channel/aivm/`。

---

## 3. new-api 侧改动清单（解卡后执行）

### 3.1 渠道号与模型名

| 项 | 值 |
|---|---|
| `constant.ChannelTypeVideoAI` | **开工时现查**：`grep -oE 'ChannelType[A-Za-z0-9_]+ += +[0-9]+' constant/channel.go \| sort -t= -k2 -n \| tail -1`。截至本文写作内置最大为 79（`ChannelTypeCodeAI`），但 **80 已被 Animefy 预留**，建议取 **81** |
| 模型名 | `videoai-txt2vid` / `videoai-img2vid` / `videoai-vid2vid` / `videoai-deforum` |
| 计费 | 建议按次（`ai_model_used_credit_counts`：txt2vid=1、img2img=1），时长档位见远端 `deforum_durations` |

### 3.2 文件

```
relay/channel/videoai/adaptor.go        # 实现 relay/channel.TaskAdaptor
relay/channel/videoai/client.go         # Firestore REST + sig + config 拉取
relay/channel/videoai/models.go         # ModelList / 参数映射
relay/channel/videoai/adaptor_test.go   # 单测（mock Firestore 响应）
```

### 3.3 必须实现的接口（`relay/channel/adapter.go` 的 `TaskAdaptor`）

```go
Init(info *relaycommon.RelayInfo)
ValidateRequestAndSetAction(c, info) *taskdto.TaskError
EstimateBilling(c, info) map[string]float64
AdjustBillingOnSubmit(info, taskData) map[string]float64
AdjustBillingOnComplete(task, taskResult) int
BuildRequestURL(info) (string, error)
BuildRequestHeader(c, req, info) error
BuildRequestBody(c, info) (io.Reader, error)
DoRequest(c, info, body) (*http.Response, error)
ParseResponse(c, resp, info) (*TaskSubmitResponse, *taskdto.TaskError)
GetModelList() []string
GetChannelName() string
FetchTask(baseUrl, key, task, proxy) (*http.Response, error)
ParseTaskResult(task, resp, body) (*relaycommon.TaskInfo, error)
```

要点：
- `ParseResponse` 里创建 Firestore 文档，`UpstreamTaskID = docId`，`ClientResponse` 返回 OpenAI 风格 `{"id": "...", "status": "queued"}`。
- `FetchTask` 用 `task.UpstreamTaskID` 去 `GET /documents/videos/{id}`，把 Firestore 的 `status` 映射为 `relaycommon.TaskInfo.Status`（queued/in_progress/completed/failed）。
- **`sig` 必须在提交时算**（`createdAt` 与 `sig` 用同一个 ms 值，否则 403）。
- Firestore REST 是 JSON 包装（`{"fields":{"k":{"stringValue":"v"}}}`），不要用 map 直传。

### 3.4 渠道 Key 设计

渠道 `key` 用 JSON（一账号一 Key，便于多 cerebroId 轮换）：

```json
{
  "cerebro_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "sig_salt": "<SIG_SALT>",
  "firebase_api_key": "<FIREBASE_API_KEY>",
  "cerebro_api_key": "<CEREBRO_API_KEY>",
  "cerebro_hmac_key": "<CEREBRO_HMAC_KEY>"
}
```

`cerebro_id` 必须与服务端 `cerebro-users/{id}` 对应（解卡后才知道如何合法获得，见 §6）。

---

## 4. 参数映射（客户端 → 上游）

| OpenAI 侧 | Firestore 字段 | 备注 |
|---|---|---|
| `prompt` | `prompts[]` | txt2vid 支持数组，`prompt_configuration` 限 free 1 / pro 3 |
| `seconds` | `numFrames` / `outputVideoDurationInSeconds` | 远端 `txt2vid_configuration.outputVideoDurationInSeconds = 5`，`numFrames=121`（≈24fps×5s） |
| `size` | `width`/`height` | 配置 `512×512`；img2vid 走 `useSizeOfIpImage` |
| `style`（自定义字段） | `styleId` | 取值见 `all_styles`（26 个） |
| — | `guidanceScale` / `numInferenceSteps` | 默认 7.5 / 30，可由远端配置覆盖 |
| — | `aiModel` | 决定 Worker 走哪条流水线 |
| 设备身份 | `cerebroId` | 账号维度 |

状态映射：`CREATED|WAITING → queued`，`PROCESSING → in_progress`，`COMPLETED → completed`（取 `outputVideoUrl`），`FAILED → failed`（取 `error`）。

---

## 5. 最小验收标准（写完后必须逐条跑）

1. mock 单测：Firestore 请求体字段齐全、`sig` 由 `createdAt` 算出、`docId` 回填 `UpstreamTaskID`。
2. 真实提交：`POST /v1/videos`（或对应任务端点）→ 返回 task id，且 Firestore 里能 `GET` 到该 docs。
3. 轮询：`FetchTask` 三次内完成状态映射（构造 mock 响应覆盖 queued/in_progress/completed/failed 四态）。
4. **真实出片**：解卡后跑一次端到端，落盘 MP4 并按任务计费入账。
5. 三库一致性/构建：`go build ./...` + 目标测试通过（渠道不涉 DB migration，无需三库矩阵）。

---

## 6. 解卡判据（Worker 接管确认，接渠道前必须先满足）

以下三条**同时**满足才算解卡：

1. 用真实 App（arm64 设备）跑一次 img2img / txt2vid，抓包得到一份"App 写的 Firestore 文档"与"我方构造文档"的字段 diff（含 Storage 对象路径）；
2. 我方向 `videos` 提交后，`updateTime` 出现变化且 `status` 进入 `WAITING/PROCESSING`；
3. `outputVideoUrl` 是可下载的真实产物（非空、可 200 拉取）。

当前状态（`已验证`）：1 未做（无 arm64 设备）、2/3 均不满足。

---

## 7. 红线

- **禁止伪造余额**：`cerebro-users.credits` 不得由我方写入非 0/非法值（本项目探测时 credits 恒为 0）。
- 不做批量任务灌入，不做绕过付费的号池轮换；上游 `fal.ai` 是真实计费算力。
- 密钥只放渠道 Key/环境变量，禁止硬编码进源码与仓库。
- 提交频率按单账号 < 1 req/10s，出现 429/403 立即熔断。

---

## 8. 附录：错误码对照（实测）

| 场景 | 上游返回 | 处理建议 |
|---|---|---|
| `cerebroId` 缺失 | 403 `PERMISSION_DENIED` | 400 参数错误，不重试 |
| `sig` 错误/漏盐/小写 | 403 `PERMISSION_DENIED` | 500 内部错误（我方 bug），需修正算法 |
| 更新已存在任务文档 | 403 | 客户端只能 create，状态回写交给上游 |
| collection list | 403 | 只允许按 docId 读 |
| Storage 上传 | 403 / 402 | 输入素材链路当前不可用 |
| config-api 缺 `uid` | 400 `{"errorMessage":"invalid body"}` | 补 uuid |

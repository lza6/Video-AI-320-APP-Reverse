# PLAYBOOK — Firebase / Firestore 后端型 App 的逆向与 2API 判定

> 适用：Flutter/原生 App，业务数据落在 **Cloud Firestore**，没有 REST 生成接口。
> 首个实战：Video AI v3.2.0 `com.codeway.videoapp`（Codeway Cerebro/Codewall SDK）。
> 与"传统 REST 2API"（找 baseUrl + 签名 + 轮询）是两条完全不同的路，先分诊再动手。

---

## 0. 三分钟分诊：它是不是 Firestore 型？

```bash
# dex / libapp.so 里有没有这套 SDK 指纹
grep -c "cloud_firestore\|firebase_core" lib/arm64-v8a/libapp.so
grep -ao "package:[a-z]*/services/firebase/[a-z_]*\.dart" lib/arm64-v8a/libapp.so | sort -u
grep -ao "StreamById\|FirestoreService\|cerebro-users" lib/arm64-v8a/libapp.so | sort -u
```

命中以下任一 → 走本 playbook：
- `*Firestore|create*ProcessAndGetCreationId|*StreamById`
- `firebase_analytics/firebase_core` 存在但 `firebase_auth` **不存在**（说明鉴权另有机制）
- 反汇编里全工程 `http.post` 只有配置/上报两处

进一步确认：`res/values/strings.xml` 里 `project_id` + `google_storage_bucket` + `google_api_key`。

---

## 1. 三把钥匙（按顺序，每把都能独立验证）

### 1.1 远程配置接口（最容易被忽略、价值最高）

几乎一定存在一个"配置 API"，因为白标 App 要能远程改模板/Paywall。

```
GET https://config-api.<vendor>.com/android?aid=<pkg>&uid=<任意UUID>&version=&language=&country=
```

- `uid`/`aid` 常常**不校验**：随便给一个 UUID 就能拿到 50+ 键的完整 payload（模板、风格、积分策略、AB 桶、商品目录）。
- 若返回 `{"errorMessage":"invalid body"}` 或 `500 Invalid body`，说明**缺查询参数**（先试 `uid`）。
- 拿到 payload 就等于拿到"产品全貌 + 定价 + 模板 ID 空间"，做选型层/展示层无需逆向。

### 1.2 用户/设备上报接口（找签名算法）

在 monolith SDK（如 Cerebro）里搜 `generateToken`：

```
token = HMAC_SHA256(key=<常量>, f"{body_json}_{apiKey}_{ts_ms}_{path}").hex
headers: authorization=<apiKey>, rs=<token>, ts=<ms>
```

- `body_json` 必须与实际发送字节完全一致（签名对原始串）。
- 常量就在 `libapp.so` 常量池里，`d.find(b'generateToken')` 前后 400 字节直接肉眼可读。
- 验证方式：随便造 UUID 打一发，200 = 签名对。

### 1.3 Firestore 规则指纹（黑盒，单变量 A/B）

写一个探针脚本，对每个假设只改**一个变量**：

| 探针 | 结论 |
|---|---|
| `POST <coll>` 少填疑似必需字段 | 403 vs 200 → 该字段是规则强制项 |
| `sig` 用正确算法 vs 随机 64 hex vs 小写 vs 漏盐 | 若只有正确算法 200 → **规则在做真值校验**（说明算法+盐 100% 正确） |
| `PATCH /coll/{已有id}` | 403 → 客户端 **create-only**，状态回写只能服务端做 |
| `GET /coll?pageSize=1` | 403 → 禁止枚举；但 `GET /coll/{id}` 常允许（凭 id 可读结果） |
| `PATCH /coll/{预生成id}` 且 `id` 字段 == docId | 200 → 复刻 `doc().set(merge:true)` 的正确写法 |
| 用户文档 `PATCH /users/{uid}` + 同算法 sig | 200 → 用户文档不是服务端独占 |

这一步能省掉"Worker 为什么不处理我"的大量瞎猜：**只要 sig 探针通过，就能排除签名问题**。

---

## 2. sig / 签名类字段的挖掘模板

```python
d = open('lib/arm64-v8a/libapp.so','rb').read()
i = d.find(b'_getSignature')          # 或 generateToken / sign / hmac
print(re.sub(rb'[^\x20-\x7e]', b'|', d[i-600:i+600]).decode('latin-1'))
```

读反汇编时抓三件事：
1. 拼接数组的**顺序与分隔符**（`field_f/field_13/...` 依次是 `secret` `_` `id` `_` `ts`）；
2. 结尾是 `Digest::toString`（hex 小写）还是再跟一次 `GDT[...]`（常见 `toUpperCase`/`base64Encode`）；
3. 时间戳来源：`NTPService.now()` → **毫秒**；且**签名用的 ts 必须原样写进文档**（否则规则不认）。

---

## 3. 任务文档结构 = 模型层 `toJson()`

别猜字段。直接抽 Output\* 模型的 `toJson` 字符串序列：

```bash
sed -n "$(grep -n 'toJson' asm/app/models/output_video.dart | head -1 | cut -d: -f1),+700p" \
  asm/app/models/output_video.dart | grep -oE 'r[0-9]* = "[A-Za-z_]{2,40}"' | uniq
```

配套再看：
- 集合名：`collection("videos")` / `collection("images")`；
- 状态机：`where('status','whereIn',["CREATED","WAITING","FAILED","PROCESSING","COMPLETED"])`；
- 产物字段：`outputVideoUrl` / `thumbnailVideoUrl` / `outputVideoDurationInSeconds` / `error`。

---

## 4. 必然卡住的地方，以及"何时停手"

Firestore 型应用的生成 Worker 是**服务端黑盒**，客户端看不到触发条件。判定标准：

| 现象 | 含义 |
|---|---|
| 写入 200，`updateTime` 50s 内不动 | Worker 未接管（先排除字段/sig/账号三类原因） |
| 6 种形态（最少字段 / status=WAITING / inputs / id+PATCH / 有账号 / 完整 toJson）× 100s+ 全部无变化 | 触发条件在服务端私有处（白名单/订阅态/Storage 事件）→ **停手，别再猜** |
| Storage 上传 403 且 App 内无 firebase_auth | 客户端凭据来源不明 → 需要动态抓包才能解 |

**停手后的正确动作**：把"能复现的部分"交付（协议文档 + 任务客户端 + mock 网关 + 压测），
把卡点写成可判定的**解卡判据**（见 `docs/NEW-API-HANDOFF.md §6`），而不是硬造一个空壳渠道。

---

## 5. 交付物标准（Firestore 型）

```
docs/PROTOCOL.md           协议全量（含每条结论的证据等级）
docs/EVIDENCE.md           探针台账（单变量 A/B 的原始结果）
docs/NEW-API-HANDOFF.md    对接交接书（渠道号、接口清单、解卡判据、红线）
vapi/ 或 lib/              config + HMAC + Firestore 任务客户端
demo.html + server          MOCK/LIVE/AUTO 三模式（MOCK 必须明确标注"未触达上游"）
stress_test.py             本地压测（MOCK 全链路 + 并发吞吐）
tests/                     签名/文档结构单测（用假密钥，真密钥永不入库）
```

## 6. 复用性提示

`cerebroapi.com` / `cdwapi.com` / `codewall` 这套 SDK 是 **Codeway 全家桶共用**（Wonder、Video AI 等）。
一次摸清 `config-api` + `user/android` HMAC + `sig` 规则校验，后续同族 App 只需要换
`aid`（包名）、`project_id`、以及各自的 `toJson()` 字段即可，**不必重新逆向 SDK 层**。

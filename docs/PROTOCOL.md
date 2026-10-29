# Video AI v3.2.0 (com.codeway.videoapp) — 协议还原（R1）

> 证据等级标注：`已验证`=实际跑通并观察到响应；`静态确认`=读 blutter/jadx 反汇编确认；
> `合理推断`=有间接证据；`待验证`=缺条件。

## 0. 目标基本信息

| 项 | 值 | 证据 |
|---|---|---|
| 包名 | `com.codeway.videoapp` | apktool AndroidManifest（`已验证`） |
| 版本 | 3.2.0（远端 `min_version=3.6.0`，即服务端认为该版本已过旧但接口仍可用） | `res`/config payload（`已验证`） |
| 架构 | Flutter（`lib/arm64-v8a/libflutter.so` + `libapp.so`，Dart 3.5.4 / snapshot `80a49c7111088100a233b2ae788e1f48`，**未混淆**，符号与类名完整） | blutter 3.5.4（`已验证`） |
| ABI | 仅 `arm64-v8a`（无 armeabi/x86_64），x86_64 模拟器无法直接运行 | `unzip -l`（`已验证`） |
| 签名证书 SHA1（本 Mod 包） | `24:12:F8:8A:C7:37:78:C3:D6:59:D4:7A:31:12:E2:78:98:F9:53:B9`（1024bit，重打包证书） | keytool（`已验证`） |
| 出品方 | Codeway（`codeway.videoapp` / `videoai.world` / `cdwapi.com` / `cerebroapi.com`） | — |

关键结论：**这是一个"客户端写 Firestore → 服务端 Worker 异步出片"的架构，不是普通 REST 2API 的同步/轮询接口。**

## 1. 后端拓扑

```
Flutter App
 ├─GET  config-api.cdwapi.com/android?aid=&uid=&version=&language=&country=   ← 远程配置（全部 UI/模板/积分策略）
 ├─POST gateway.cerebroapi.com/user/android                                   ← 用户/设备上报（HMAC 签名）
 ├─GET  gateway.cerebroapi.com/user/latest-status                              ← 订阅状态
 ├─POST gateway.cerebroapi.com/log/android                                     ← 埋点
 ├─POST gateway.cerebroapi.com/receipt/verify | /receipt/acknowledge           ← Google Play 收据校验
 ├─POST gateway.cerebroapi.com/consumable/verify | /consumable/consume         ← 消耗型内购
 ├─Firestore  projects/video-app-codeway  (create "videos"/"images" 文档)
 └─Firebase Storage  video-app-codeway.appspot.com  (上传输入素材)
```

Firebase 常量（`res/values/strings.xml`，`静态确认`）：

```
google_app_id      1:1093494993243:android:8c409c4f2a112d4e90aa7e
project_id         video-app-codeway        (project number 1093494993243)
google_api_key     <FIREBASE_API_KEY>   # 见 §1 说明：从 strings.xml 提取
storage_bucket     video-app-codeway.appspot.com
```

## 2. Cerebro 签名（网关侧）

`HelperExtension.generateToken`（`asm/cerebro/cerebro.dart`，`静态确认`）：

```
token = HMAC_SHA256(key = "<CEREBRO_HMAC_KEY>",
                    msg = f"{body_json}_{apiKey}_{ts_ms}_{path}")   # hex 小写
请求头：
  authorization: <CEREBRO_API_KEY>      # 固定 apiKey
  rs:            <token>
  ts:            <ts_ms>                    # 毫秒时间戳（NTPService.now）
  content-type:  application/json; charset=utf-8
```

`body_json` 必须与实际发送的 body 字节完全一致（服务端按原始串重算）。

`/user/android` 请求体键（`UserService._getData`，`静态确认`）：

```
app_id, version, adjust_id, user_id, vendor_id, advertising_id, tracker_name,
operating_system, operating_system_version, time_zone_offset_seconds, language,
mobile_brand_name, mobile_os_hardware_model, device_region, device_token,
appsflyer_id, is_sandbox_device_token, account_id, set_user
```

实测：任意 UUID 作为 `user_id/vendor_id/advertising_id` 均返回
`{"status":{"code":200,"message":"Successful"}}`（`已验证`）。

## 3. 远程配置（无需鉴权，最关键的公开面）

```
GET https://config-api.cdwapi.com/android?aid=com.codeway.videoapp&uid=<任意UUID>&version=<ver>&language=en&country=US
→ {"status":{...},"payload":{ 51 个键 }}
```

`已验证`：`uid` 必须存在（否则 400 `{"errorMessage":"invalid body"}`），任意 UUID 即可，**不需要注册、不需要签名头**。

payload 关键键（全部实测返回，`已验证`）：

| 键 | 作用 / 摘要 |
|---|---|
| `all_templates` | 模板表，实测 **106 个模板**（id/name/ai_model/is_premium/image_url/…） |
| `template_videos` / `recommended_video_categories` / `*_categories` | 精选与分类（txt2vid/img2vid/vid2vid/img2img/deforum） |
| `all_styles` | 风格表，实测 **26 个风格**（id/name/image_url） |
| `txt2vid_configuration` | `txt2vidModel: "fal_ai_stable_video"`、guidanceScale/numFrames/numInferenceSteps、enableSelectStyle 等 |
| `img2vid_configuration` / `vid2vid_configuration` / `img2img_configuration` | 各模型参数与时长/积分规则 |
| `deforum_rotations` / `deforum_durations` | Deforum 旋转/时长档位（含 credit_count） |
| `ai_model_used_credit_counts` | `{"img2img":1,"txt2vid":1}` |
| `ai_model_remaining_times` | 免费额度：`img2vid:2, vid2vid:4, deforum:2, img2img:0.167, txt2vid:0.40` |
| `credit_limit_configuration` | `{"limit_pro":50,"limit_free":0,"limit_type":"weekly"}` |
| `iap_catalog` / `codewall_iap_catalog` / `paywall_structure` | 订阅商品与 Paywall 结构 |
| `isl_keys` | 加密的 ISL 键（`ISLService` 解密，未解） |
| `pricing_tier` | `t10`（AB 分桶） |

**用途**：模板/风格/积分策略全部公开可拉取，可直接做展示与选型层，无需 APK。

## 4. Firestore 任务协议（核心）

### 4.1 文档结构

`VideoProcessesFirestore.createVideoProcessAndGetCreationId`
（`asm/videoapp/services/firebase/firestore_service.dart`，`静态确认`）：

```
POST firestore.googleapis.com/v1/projects/video-app-codeway/databases/(default)/documents/videos
fields = 输入字段 map
       + { txt2vidModel | styleId }        // txt2vid 分支，默认 "fal_ai"
       + { guidanceScale, numInferenceSteps, numFrames }
       + { sig: 签名, id: docId }           // set(merge: true)
```

`sig` 算法（`FirestoreService._getSignature`，`静态确认`）：

```
sig = SHA256_HEX_UPPER( "<SIG_SALT>_" + cerebroId + "_" + createdAtMillis )
```

`<SIG_SALT>` 是二进制里的字面量（非占位符替换结果，`静态确认`：字符串存在于 `libapp.so` 字符串表，`_getSignature` 直接内联常量）。

任务文档字段（`output_video.dart` / `output_image.dart` / asm 字符串，`静态确认`）：

```
createdAt(int ms), aiModel("img2img"|"img2vid"|"vid2vid"|"deforum"|"txt2vid"),
cerebroId, prompts[str], styleId, templateId, txt2vidModel,
guidanceScale, numInferenceSteps, numFrames,
inputs.<key>  (输入素材 storage 路径), inputImageUrl, inputVideoUrl,
inputIpImageUrl, inputIpVideoUrl, ipVideoDurationInSeconds,
inputVideoDurationInSeconds, outputVideoDurationInSeconds,
thumbnailImageUrl, thumbnailVideoUrl, templateSound, originalSound,
width, height, skin, gender, rotation, categoryName, selectedAiModelPage,
usedCreditCount, status, error, sig, id
```

服务端回写的状态机（`getUserImages`/`getUserVideos` 的 `whereIn`，`静态确认`）：

```
CREATED → WAITING → PROCESSING → COMPLETED
                              ↘ FAILED
DELETED（客户端删除标记）
```

客户端读取使用 `videoProcessStreamById`（单文档监听），因此**结果只需按 docId 单文档读取**。

### 4.2 安全规则实测（`已验证`，逐条 curl）

| 操作 | 结果 |
|---|---|
| `GET /videos?pageSize=1`（list） | **403** PERMISSION_DENIED |
| `GET /videos/{docId}`（单文档） | **200**（无鉴权可读，凭 docId 即可） |
| `POST /videos` 带 `cerebroId` 字段 | **200** 创建成功 |
| `POST /videos` 缺 `cerebroId` | **403**（规则强制要求该字段） |
| `GET /cerebro-users/{uid}` | **404**（可读，但文档不存在） |
| `POST /cerebro-users/...` | **403** |
| Firebase Storage 上传（`/o?uploadType=media`，多种路径） | **403 / 402** |
| `identitytoolkit accounts:signUp`（匿名登录） | **400** CONFIGURATION_NOT_FOUND（项目未启用匿名 Auth，且 App 内无任何 firebase_auth 代码，`静态确认`） |

**结论**：可以匿名写入任务文档、匿名按 id 读取结果文档；但不能创建用户记录、不能上传素材。

### 4.3 端到端实测（`已验证`，失败结论）

用真实字段结构创建 `videos` 文档（含 `cerebroId`/`sig`/`prompts`/`txt2vidModel`），
并轮询 6+ 分钟（含 `status=CREATED` / `WAITING` / 带 `inputs` 三种形态）：

- 文档创建成功，`docId` 返回正常；
- `updateTime` 始终等于 `createTime`，**没有任何 Worker 接管**（状态未变成 WAITING/PROCESSING/FAILED，也没写 `error`）。

R2 追加（详见 `docs/EVIDENCE.md`）：**sig 由 Firestore 规则做真值校验**（随机/漏盐/小写 sig 一律 403，
只有 `SHA256(<SIG_SALT>_{cerebroId}_{createdAtMs}).upper()` 通过），说明算法与盐 100% 正确；
`cerebro-users` 也能用同一 sig 创建（docId == cerebroId，PATCH 200），客户端写入是 **create-only**（update 403）。
在此基础上又试了 6 种任务形态（含完整 `toJson()` 形状、账号存在、`id` 字段、`inputs`、`status=WAITING`），仍无 Worker 接管。

触发阻塞的可能原因（`合理推断`，尚未确证）：
1. Worker 校验 `cerebro-users/{cerebroId}` 是否存在且有积分 → 我们无法创建该文档（403）；
2. Worker 用服务端私有密钥重算 `sig`，与 `<SIG_SALT>` 字面量不一致 → 静默丢弃；
3. Worker 以 `status == "CREATED"` + 其他隐藏字段（如 `workflowId`）为拉取条件。

要确证必须做一次**真机/arm64 动态抓包**（真实账号跑一次 img2img），当前环境不具备：
- 本机 AVD 全部为 x86_64，APK 仅含 arm64-v8a；
- `adb devices` 无物理设备；
- Storage 上传 403，无法构造 img2vid/img2img 的合法输入。

## 5. 其它组件

| 组件 | 说明 |
|---|---|
| `ISLService` + `isl_keys` | 远端配置中的加密键（`Failed to decrypt ISL string`），用途未定位 |
| `play-store-verifier.cdwapi.com` | Google Play 收据校验（需要真实购买 token） |
| Adjust / AppsFlyer / Firebase Analytics / Crashlytics | 埋点，非功能链路 |
| `dio`/`http` | 仅 `config` 与 `cerebro` 两条 HTTPS；生成链路 100% 走 Firestore（`静态确认`：全工程 `http.get/post` 仅 1 处 memory helper） |


## 6. R1 交付物与实测汇总

| 项 | 结果 | 证据 |
|---|---|---|
| 远程配置拉取 | 51 键 / 106 模板 / 26 风格 / 5 个 ai_model（img2img, img2vid, vid2vid, deforum, txt2vid） | `GET /api/config`（已验证） |
| 用户上报（HMAC） | 任意 UUID → 200 Successful | `POST /api/user`（已验证） |
| Firestore 写任务 | 200，docId 正常返回 | `POST /v1/jobs?mode=live`（已验证） |
| Firestore 读任务 | 200（按 docId） | `GET /v1/jobs/{id}`（已验证） |
| Worker 接管 | **未接管**（CREATED 状态 > 200s 不动；bare / WAITING / 带 inputs 三种形态均无效） | `watch_jobs.log`（已验证） |
| 用户文档创建 | 403（`cerebro-users`） | `GET /api/live/probe`（已验证） |
| 素材上传 | 403 / 402（Firebase Storage） | 探针（已验证） |
| MOCK 全链路 | 24/24 COMPLETED，e2e p50 4.08s | `stress_test.py`（已验证） |
| MOCK 创建吞吐 | c=8: 542 rps / p50 9.0ms；c=16: 575 rps / p50 21.5ms；c=32: 675 rps / p50 33.3ms，0 错误 | `stress_test.py`（已验证） |
| 浏览器 | demo 页加载配置、提交、轮询到 COMPLETED，console error = 0 | Playwright（已验证，`artifacts/demo_completed.png`） |

**R1 结论**：协议层（config / user / Firestore 任务读写 / 状态机）已 100% 还原并可复现；
**真实出片仍卡在账号与素材授权**，需要一次真机动态抓包或有效订阅账号才能解除，属于下一轮的前置条件。


## 7. 常量提取方法（自行复现）

```bash
# Firebase 常量
apktool d -f -s app.apk -o out && grep -E 'google_app_id|google_api_key|project_id|google_storage_bucket' out/res/values/strings.xml
# Cerebro apiKey / HMAC key / 签名盐：都在 libapp.so 字符串表里
python - <<'EOF'
import re
d=open('lib/arm64-v8a/libapp.so','rb').read()
print(sorted({m.group().decode() for m in re.finditer(rb'[ -~]{4,60}', d)})[:0] or '见下方 grep')
EOF
# 三个常量都紧邻 HelperExtension.generateToken / FirestoreService._getSignature 的常量池
python -c "d=open('lib/arm64-v8a/libapp.so','rb').read(); i=d.find(b'generateToken'); print(d[i-400:i+400])"
python -c "d=open('lib/arm64-v8a/libapp.so','rb').read(); i=d.find(b'_getSignature'); print(d[i-400:i+400])"
```

（`<CEREBRO_API_KEY>` / `<CEREBRO_HMAC_KEY>` / `<SIG_SALT>` / `<FIREBASE_API_KEY>` 的位置：
`asm/cerebro/cerebro.dart` 的 `HelperExtension.generateToken`、`firestore_service.dart` 的
`FirestoreService._getSignature`、`res/values/strings.xml` 的 `google_api_key`。）

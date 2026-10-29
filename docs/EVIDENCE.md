# R2 证据台账（Firestore 规则与 Worker 门禁）

所有结论均来自对本项目 Firestore / Storage / IdentityToolkit 的真实请求（2026-10，单机 + 本地代理）。
密钥不落库，运行前按 `docs/PROTOCOL.md §7` 填入环境变量。

## 1. sig 被写规则**真值校验**（关键发现）

同一 `cerebroId` / `createdAt` / 字段集合，仅改 `sig` 的值，各试 2 轮：

| sig 取值 | 结果 |
|---|---|
| `SHA256(␣<SIG_SALT>_{cerebroId}_{createdAt}`).hex.upper() | **200**（两轮均 200） |
| 随机 32 字节 hex，转大写（64 字符） | **403** |
| 上述正确值改为小写 | **403** |
| `SHA256("{cerebroId}_{createdAt}")`（漏盐） | **403** |
| 无 `sig` 字段 | **403** |
| `sig = "DEADBEEF"` | **403** |

→ 规则不是"形状校验"，而是按同一算法+同一盐做真值比对。**说明我们还原的算法与盐与生产一致**
（这也是"任务文档能写入"的唯一解释），因此 Worker 不接管与 sig 无关。

## 2. 客户端写入是 **create-only**

| 操作 | 结果 |
|---|---|
| `PATCH /videos/{客户端预生成ID}`（新文档，`id` 字段 == docId） | **200** |
| `POST /videos`（自动 ID） | **200** |
| `PATCH /videos/{已存在文档}` 补 `id` 字段 | **403**（update 被拒） |
| `GET /videos?pageSize=1`（list） | **403** |
| `GET /videos/{id}` | **200** |

→ 状态回写只能由服务端（Admin SDK）完成；客户端永远看不到别人的任务列表。

## 3. 账号文档（cerebro-users）

| 操作 | 结果 |
|---|---|
| `PATCH /cerebro-users/{cerebroId}` + `sig`（docId == cerebroId） | **200** |
| `GET /cerebro-users/{cerebroId}` | **200 / 404** |
| `POST /cerebro-users`（自动 ID） | **403** |

→ 用户文档不是服务端独占：同样用 sig 保护。**本项目未做任何余额伪造**，测试账号 `credits` 恒为 0。

## 4. 其它集合 / 服务

| 操作 | 结果 |
|---|---|
| `POST /images`（img2img，带 cerebroId/sig/64 位大写 sig） | **403**（该集合写入条件与 videos 不同，未解） |
| `POST /consumables` | **403** |
| `POST /videos` 缺 `cerebroId` | **403** |
| Firebase Storage `o?uploadType=media`（多种路径） | **403** / 路径 `inputs/{id}/input.jpg` 返回 **402** |
| IdentityToolkit `accounts:signUp?key=<FIREBASE_API_KEY>` | **400** `CONFIGURATION_NOT_FOUND`（项目未启用匿名 Auth；APK 内也无 firebase_auth 代码） |

## 5. Worker 接管实验（全部失败，共 6 种形态）

| 形态 | 说明 | 观察窗口 | 结果 |
|---|---|---|---|
| A `bare` | 最少字段（createdAt/aiModel/cerebroId/prompts/txt2vidModel/sig） | 200s | 无变化 |
| B `status=WAITING` | 客户端直接写 WAITING | 200s | 无变化 |
| C `inputs` 结构 | 带 `inputs.<key> = {type,path,url}` | 200s | 无变化 |
| D `id` 字段 + PATCH | 复刻 `doc().set(merge:true)`，`id==docId` | 144s | 无变化 |
| E 账号存在（`cerebro-users` credits=0） | 先建账号再提交任务 | 100s | 无变化 |
| F 完整 `toJson()` 形状 | `id/inputType/status/width/height/usedCreditCount/numFrames/...` 全字段 + 账号存在（docId `UbxzvU6Wxz8niArYG2NI`） | 151s | 无变化（`status` 保持 CREATED，`updateTime` 不动） |

**结论**：Worker 的触发条件不在客户端可见字段内，最可能是「服务端定时/触发器 + 私有白名单或账号订阅态」，
仅靠黑盒写入无法确证。要拿到最后一环必须真机（arm64）动态抓包对比"我们写的文档"与"App 写的文档"
在 **Storage 侧对象**、**用户文档并发写入顺序**、以及是否存在 **未上架集合** 上的差异。

## 6. 复现命令

```bash
python gate_test.py          # 账号+任务门禁探测（credits=0，不伪造）
python probe_full.py         # 完整 toJson 形状探测
python -m vapi.app           # 网关 + demo
python stress_test.py --concurrency 16 --total 160 --no-verify
```

# API 摘要与本地示例（v0.6）

运行后 `/docs` 和 `/openapi.json` 给出当前服务端 schema。仓库中的 `assets/openapi.json` 是历史导出，可能落后于本地候选。以下只使用虚构数据；正式环境需先完成部署与权限验收。

## 登录、会话和 MFA

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"staff","password":"本地演示密码"}'
```

成功返回 `access_token`；后续请求带 `Authorization: Bearer <token>`。已启用 TOTP 时，普通登录返回 401 和 `X-MFA-Required: totp`，改用 `/api/auth/login/totp` 并提交 `totp_code`。连续失败会锁定账号，登录还有账号/IP 限流；开发专用 `/api/auth/dev-login` 在生产关闭。

| 方法 | 接口 | 作用 |
|---|---|---|
| POST | `/api/auth/change-password` | 提交 `current_password`、`new_password`；旧会话失效，返回新令牌 |
| POST | `/api/auth/logout-all` | 全端登出 |
| POST | `/api/auth/mfa/totp` | 交当前密码；若已有因子，再交现有 `totp_code`；返回仅展示一次的待确认密钥与 URI |
| POST | `/api/auth/mfa/totp/confirm` | 交当前密码和新认证器 `totp_code`，确认后才启用并轮换会话 |
| DELETE | `/api/auth/mfa/totp` | 交当前密码和现有 `totp_code`，关闭并轮换会话 |
| GET | `/api/auth/demo-status` | 开发演示提示状态 |
| POST | `/api/community/accounts` | 员工为本社区居民开通账号 |
| POST | `/api/auth/register-family` | 家属自助注册；初始无居民数据权限 |
| POST | `/api/admin/users` | 管理员提交 `username`、`display_name`、`community_id`、`initial_password`，仅创建固定 `staff` 角色 |

## 居民、家属与员工流程

| 角色 | 方法 | 接口 | 作用 |
|---|---|---|---|
| 通用 | GET | `/api/me`、`/api/communities` | 当前账号与社区列表 |
| 家属/居民 | POST | `/api/me/invite-code/rotate` | 轮换本人邀请码 |
| 居民 | GET/POST | `/api/residents/me` | 读取/创建本人档案 |
| 居民 | POST | `/api/residents/claim-requests` | 提交线下档案认领申请，统一 202 响应；未核验前无访问权 |
| 本人 | GET/POST | `/api/residents/{id}/consents` | 查看、授予或撤回社区护理同意 |
| 本人/有权访问者 | GET | `/api/residents/{id}`、`/api/residents/{id}/vitals` | 按字段与对象权限读取 |
| 本人/获准员工 | POST | `/api/residents/{id}/vitals` | 录入手工测量 |
| 本人 | DELETE | `/api/residents/{id}/vitals/{vid}` | 删除本人测量 |
| 本人 | GET/POST | `/api/residents/{id}/reminders` | 端内待办，支持 `limit`、`offset` |
| 本人 | PATCH | `/api/residents/{id}/reminders/{rid}/complete` | 标记完成 |
| 本人 | GET/POST/DELETE | `/api/residents/{id}/family-grants[/{gid}]` | 查询、授予、撤回家属授权；提交家属自己的邀请码 |
| 家属 | GET | `/api/family/shared-residents` | 有效授权档案与可见字段 |
| 家属 | POST | `/api/residents/{id}/family-grants/{gid}/decline` | 拒绝授权 |
| 职工 | GET/POST | `/api/community/residents` | 过滤分页列表与协助建档；纸质同意先待复核 |
| 职工 | GET / POST | `/api/community/claim-requests` / `/api/community/claim-requests/{id}/review` | 待核验申请 / 现场核验结果 |
| 职工 | GET / POST | `/api/community/consent-reviews` / `/api/community/consent-events/{id}/review` | 待复核纸质同意 / 独立审批 |
| 职工 | GET | `/api/community/health-stats?limit=25&offset=0&q=` | 有效同意范围内聚合、日计数和分页居民记录 |
| 职工 | GET/POST/PATCH | `/api/community/visits`、`/api/community/visits/{id}/finish` | 随访任务 |
| 管理员 | GET / GET,POST | `/api/admin/summary` / `/api/admin/users` | 聚合 / 账号列表与固定员工角色创建；无个人健康正文 |
| 管理员 | POST | `/api/admin/users/{id}/deactivate`、`/api/admin/users/{id}/activate` | 停用或启用非管理员账号 |

认领申请体示例：

```json
{"resident_id":5,"full_name":"与档案一致的姓名","phone":"与档案一致的电话","note":"自愿填写的说明"}
```

家属授权体示例：

```json
{"invite_code":"家属账号自己的邀请码","can_profile":true,"can_vitals":true,"expires_in_days":30}
```

问卷接口为 `/api/questionnaires` 和 `/api/residents/{id}/responses`；两套模板是原创非临床示例，原始答案仅档案本人读取。

## 私人报告：上传、逐页提取与人工确认

报告接口仅档案本人或有效社区护理同意下的同社区员工可用。家属即使获测量授权、管理员即使有聚合权限，也不能读取原件、页面、候选或审核内容。

| 方法 | 接口 | 作用 |
|---|---|---|
| POST | `/api/residents/{id}/reports?filename=...` | 原始二进制请求体上传；`Content-Type` 必须与实际 PDF/PNG/JPEG/WebP 一致，最多 10 MiB、5 页 |
| GET | `/api/residents/{id}/reports?limit=20&offset=0` | 私人报告列表及总数 |
| GET | `/api/reports/{id}` | 状态、未核对候选与已确认内容分开返回 |
| GET | `/api/reports/{id}/original` | 下载经 SHA-256 校验的原件 |
| GET | `/api/reports/{id}/pages/{page}` | 渲染指定页 PNG，页码从 1 起 |
| POST | `/api/reports/{id}/extract` | `{"allow_cloud":true}`；仅用户明确允许该报告发送 DeepSeek 后排队，返回 202 |
| POST | `/api/reports/{id}/confirm` | 人工核对身份、版本和字段后保存；可不导入任何健康指标 |

例如上传仓库中的**虚构**样本（先替换本地 `居民ID` 与令牌）：

```bash
curl -X POST 'http://127.0.0.1:8000/api/residents/居民ID/reports?filename=fictional-two-page-report.pdf' \
  -H 'Authorization: Bearer 本地令牌' -H 'Content-Type: application/pdf' \
  --data-binary @assets/report-samples/fictional-two-page-report.pdf
```

上传本身不访问 DeepSeek。提取时，原生 PDF 若有足够文字且无图像，则逐页传文字；其他页传渲染图，**其中可能包含报告上的姓名和全部检验结果**。队列在数据库中，内嵌 worker 或独立 `python -m app.report_worker` 处理；每页发送前重查授权。处理中断且 7 分钟租约失效后，状态为失败，须用户主动再点重试，最多 3 次。草稿中的 `quote` 是转录片段；图像页引用本身仍由模型生成，不是可验证 OCR 坐标或准确率。

`confirm` 使用 GET 详情返回的 `revision` 和候选 `items`，由人对照原件核对字段并设置 `identity_confirmed:true`。`items` 的 `id`、`page`、`quote` 不可修改；可修正名称、值、单位、参考范围、标记。`imports` 可为空；只有明确选中的血压（mmHg）、血糖（mmol/L）、体重（kg），在名称/值/单位严格吻合且填写**实际测量时间与时区**时才能入库。其他实验室项目只留在确认报告中。两个页面与 11 条 golden 的虚构样本仅供功能回归，不代表真实材料识别准确率。

## DeepSeek 助手

`GET /api/assistant/status` 需登录，返回是否配置、供应商、模型名及功能说明，不返回密钥。`POST /api/assistant/chat` 同样需登录：

```json
{"message":"如何授权家人查看？","history":[],"allow_health_data":false}
```

默认仅回答网站帮助并给出 `/help` 来源和导航。设 `allow_health_data=true` 后，模型可调用当前身份的只读数量/日期工具；工具不接受任意居民 ID，不发送姓名、电话、提醒标题、原始测量或问卷答案，也不执行写操作。**用户输入的 `message` 和 `history` 会发送给 DeepSeek**。本地验证模型为 `deepseek-flash`；供应商正式使用仍需审查。

已确认报告的问答走独立接口 `POST /api/assistant/report-chat`。从报告详情点“询问这份报告”后，针对这一份报告单独勾选许可；每次请求仍须提交 `allow_report_data:true`：

```json
{"report_id":42,"message":"这份报告列出了哪些项目？","history":[],"allow_report_data":true}
```

仅报告本人或获有效护理同意的同社区员工可用；家属和管理员无权调用。响应包含 `answer`、`evidence_status`、`sources` 和当前 `report` 元数据。每个来源由服务端构造，含 `id`、`report_id`、`revision`、`filename`、`title`、`page`、`quote`、`confirmed_fields`、`edited_fields`、`source_kind`；其中 `quote`/`page` 来自不可更改的候选原文定位，`confirmed_fields` 来自人工审核值，`source_kind=model_transcription` 表示图片页原文只是模型转录，须打开授权页面核对。证据不足时返回 `evidence_status=insufficient_evidence`，医疗问题返回 `medical_guidance_limited`；不得将回答当作诊断或用药意见。

DeepSeek 接收问题、历史和当前报告的已确认字段上下文，**不接收本次问答的原始 PDF/图片、其他报告或未核对草稿**；模型仅选择服务端允许的证据编号和意图，引用文字及页码不由模型临时生成。服务端在模型调用前后复核对象权限、会话版本、报告版本、内容哈希和原件 SHA-256。每账号限 6 次/分钟，上下文最多 100 项/40000 字符，回答最多 8 个来源；摘要若只列出部分项目会显示已列与总数。问答不写健康记录，也不保存对话正文。

## 常见错误

- `401`：未登录、令牌失效、TOTP 要求或验证码错误；`403`：角色禁止/停用；`404`：对象不存在或无权访问；`409`：已建档、已复核等冲突；`422`：数据不合法；`423`：暂时锁定；`429`：触发限流。
- `503`：微信或助手服务未配置/暂不可用；`504`：助手回答超时。自动化测试与本地预览不能代替正式服务可用性验证。

报告问答可能在连接或结构错误时自动重试一次，每次重新检查访问权；路由总时限40秒。无效引用会返回503，授权或版本变化分别返回401/404/409；错误均不带报告正文。

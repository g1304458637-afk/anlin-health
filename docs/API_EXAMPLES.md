# API 接口摘要与调用示例

所有核心接口均在 `http://127.0.0.1:8000/docs` 提供 OpenAPI 文档，机器可读 JSON 在 `assets/openapi.json`（自动导出）或运行中的 `/openapi.json`。以下仅展示开发演示示例，不包含任何真实居民数据。

## 1. 开发登录（生产禁止）

```bash
curl -X POST http://127.0.0.1:8000/api/auth/dev-login \
  -H 'Content-Type: application/json' \
  -d '{"username":"resident","password":"DemoPass2026!"}'
```

成功返回 `access_token` 和 `user`，此令牌通常有效 8 小时。以 `Authorization: Bearer <token>` 访问其他接口。**不要将密码/令牌写入日志或提交到 Git。**

## 2. 对象与流程索引

| 端 | 方法 | 接口 | 作用 |
|---|---|---|---|
| 通用 | POST | `/api/auth/wechat` | 微信 code 换登录态（需要真实配置） |
| 通用 | GET | `/api/me` | 当前角色和邀请码 |
| 通用 | GET | `/api/communities` | 社区选项 |
| 居民 | POST | `/api/residents/me` | 自主建档和同意 |
| 居民 | GET | `/api/residents/me` | 当前档案 |
| 授权 | POST | `/api/residents/{id}/consents` | 更新社区服务同意/撤回 |
| 本人/职工/家属 | GET | `/api/residents/{id}` | 按对象级权限读基本档案 |
| 本人/职工/家属 | GET | `/api/residents/{id}/vitals` | 读取已授权测量 |
| 本人/职工 | POST | `/api/residents/{id}/vitals` | 添加血压/血糖/体重 |
| 本人 | DELETE | `/api/residents/{id}/vitals/{vid}` | 删除本人测量 |
| 通用 | GET | `/api/questionnaires` | 取得已发布示例问卷 |
| 本人/受限职工 | POST | `/api/residents/{id}/responses` | 提交问卷，职工不得提交情绪类 |
| 本人 | GET | `/api/residents/{id}/responses` | 本人读原始答卷 |
| 本人/职工 | GET,POST | `/api/residents/{id}/reminders` | 待办管理 |
| 本人/职工 | PATCH | `/api/residents/{id}/reminders/{rid}/complete` | 标记完成 |
| 本人 | POST | `/api/residents/{id}/family-grants` | 按邀请码授权家属 |
| 本人 | GET | `/api/residents/{id}/family-grants` | 查看授权历史 |
| 本人 | DELETE | `/api/residents/{id}/family-grants/{gid}` | 撤回授权 |
| 家属 | GET | `/api/family/shared-residents` | 查看有权访问的居民 |
| 职工 | GET/POST | `/api/community/residents` | 同社区居民列表/协助建档 |
| 职工 | GET/POST | `/api/community/visits` | 计划随访与任务清单 |
| 职工 | PATCH | `/api/community/visits/{id}/finish` | 完成随访 |
| 管理员 | GET | `/api/admin/summary` | 只读全局聚合 |

## 3. 血压录入 JSON

```json
{"type":"blood_pressure","systolic":123,"diastolic":78}
```

## 4. 血糖录入 JSON

```json
{"type":"blood_glucose","glucose_mmol_l":5.6}
```

## 5. 授予家属查看权 JSON

```json
{"invite_code":"输入家庭用户自己的邀请码","can_profile":true,"can_vitals":true,"expires_in_days":30}
```

## 6. 日常健康问卷提交示例

```json
{"questionnaire_id":1,"answers":{"activity":"10to30","sleep":"average","help":"yes"}}
```

答案必须覆盖问卷全部题目且完全匹配合法选项。原始答卷只有档案本人可以读取。

## 7. 关键错误语义

- 401：未登录或令牌过期。
- 403：操作在角色上禁止（例如家属试图提交问卷、普通居民转为工作人员）。
- 404：目标不存在或该账号无对象级权限，避免泄露他人档案存在性。
- 409：冲突，例如本人已建档。
- 422：输入不符合数据或同意约束。
- 503：微信接口配置缺失或官方接口临时不可用。

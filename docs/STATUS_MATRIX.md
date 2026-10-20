# 当前完成情况：真实状态表

**截至 2026-10-21 · v0.2 安全加固批次后；本包是开发试点，不是生产系统。**

状态只用三种：**实测完成**（有自动化测试或真实运行证据）/ **代码已写未验收**（缺真实环境或人工验收）/ **未实现**。

| 子模块 | 当前状态 | 实现位置 | 事实与限制 |
|---|---|---|---|
| API / 数据表 | 实测完成 | `backend/app/models.py`, `main.py` | 开发环境 SQLite `create_all`；生产强制 PostgreSQL（启动校验拒绝 SQLite） |
| 数据库迁移 | 实测完成（SQLite 验证）/ PostgreSQL 未演练 | `backend/alembic.ini`, `backend/migrations/versions/ead91a9144f3_initial_schema.py`, `tests/test_ops.py::test_alembic_upgrade_and_downgrade` | 初始迁移可 upgrade/downgrade；**生产 PostgreSQL 上的迁移与备份恢复演练仍需人工完成** |
| 生产工作人员登录 | 实测完成（API 层） | `routes/auth.py /auth/login`, `tests/test_security.py` | 速率限制（10/分钟·账号+IP）、失败 5 次锁 15 分钟、审计、停用检查；**尚未在真实机构账号体系下人工验收** |
| 密码策略 / 改密 / 全端登出 | 实测完成 | `security.py::validate_password_strength`, `/auth/change-password`, `/auth/logout-all` | 改密与登出提升 `token_version`，旧 JWT 立即失效（有测试）；员工初始密码分发流程需机构制度 |
| MFA (TOTP) | 实测完成（API 层）/ 机构启用流程未建 | `app/totp.py`, `/auth/mfa/totp`, `/auth/login/totp` | RFC 6238 标准库实现；认证器绑定、丢失恢复为人工流程，未实际验收 |
| 会话安全 | 实测完成 | `security.py::access_token(tv)`, `deps.py::auth_user` | 令牌携带 token_version，改密/停用/登出即刻失效；默认 8 小时（`SESSION_TTL_MINUTES`） |
| 速率限制 | 实测完成（单进程） | `app/ratelimit.py` | 进程内滑动窗口；**多 worker 部署必须换共享存储（如 Redis），见 DEPLOYMENT.md** |
| 微信 code2session 逻辑 | 代码已写未验收 | `routes/auth.py` | 已加 code 一次性防重放、errcode 40029/40163 显式拒绝、IP 限速；**仍缺真实 AppID/Secret 联调** |
| 角色与社区隔离 | 实测完成（基础+新增边界场景） | `deps.py`, `tests/test_api.py`, `tests/test_security.py` | 跨社区 404、心理答卷仅本人、家属过期/撤回失效均有测试；仍需专业渗透测试 |
| 居民自主建档 | 实测完成（API）/ 小程序未验收 | `routes/residents.py`, `miniapp/pages/profile` | 自填与明确勾选同意；不能认领线下已有档案 |
| 线下档案认领 | 实测完成（API 层） | `models.ClaimRequest`, `routes/residents.py`, `tests/test_security.py` | 姓名+手机号匹配仅开申请（响应恒定防枚举、每小时 5 次限额），**必须**同社区员工线下核验后批准；认领不产生护理同意 |
| 社区协助建档 | 实测完成（API+网页） | `routes/residents.py`, `static/console.html` | 仅登记同意证据编号并进入复核队列；证据真实性仍靠机构制度 |
| 同意证据复核 | 实测完成（API 层） | `routes/residents.py /community/consent-reviews`, `/community/consent-events/{id}/review` | 纸质同意 pending 入队、复核驳回立即切断员工访问（有测试）；**复核制度与双人抽查需机构落实** |
| 敏感字段静态加密 | 实测完成（手机号）/ KMS 未接 | `app/crypto.py`（AES-256-GCM）, `models.Resident.phone` | 生产强制 32 字节 base64 密钥（启动校验）；生产密钥管理与轮换流程未建 |
| 健康数据登记/列表/删除 | 实测完成（API）/ 小程序未验收 | `routes/vitals.py`, `miniapp/pages/vitals` | 有边界校验，无诊断算法 |
| 健康趋势图 | 代码已写未验收 | `miniapp/pages/vitals/index.js` | 原生 canvas 简单趋势；缺真机多尺寸验收 |
| 日常问卷模板与作答 | 实测完成（API）/ 小程序未验收 | `routes/questionnaires.py`, `seed.py` | 两份原创非临床演示问卷，无诊断评分；问卷后台编审/版本管理仍未实现 |
| 心理答案的独立权限 | 实测完成（基础+边界场景） | `routes/questionnaires.py`, `tests/test_api.py` | 家属和工作人员均无心理原始答案读取接口；未做全链路安全审计 |
| 健康待办与完成 | 实测完成（API）/ 小程序未验收 | `routes/reminders.py` | 仅应用内待办列表，不自动推送 |
| 微信订阅消息自动提醒 | **未实现** | — | 需要模板、主动授权、生产服务/规则设计 |
| 家属邀请码+限时授权+撤回 | 实测完成（API）/ 小程序未验收 | `routes/family.py` | v0.2 新增邀请码轮换（旧码立即失效）与家属拒绝授权；小程序 UI 未跟进 |
| 社区走访计划与完成 | 实测完成（API+网页真实 E2E） | `routes/visits.py`, `static/console.html` | 基本任务闭环；没有复杂分派工作流 |
| 管理后台 UI | 实测完成（真实后端浏览器 E2E） | `backend/static/console.html` | 2026-10-21 于真实运行后端验证：登录（新 `/auth/login`）、居民列表、协助建档、血压录入、随访完成；管理台仍无复核/认领/账号管理界面 |
| 管理员账号生命周期 | 实测完成（API 层） | `routes/admin.py`, `tests/test_security.py` | 列表（无凭据/健康字段）、停用（即刻杀会话）、启用；禁止停用自己与管理员 |
| 安全响应头 / Host 限制 | 实测完成 | `main.py`, `config.py` | nosniff/DENY/no-referrer/CSP 恒定，生产 HSTS+no-store；生产 TrustedHost 需要 `ALLOWED_HOSTS` |
| 生产配置防护 | 实测完成 | `config.py::validate_settings`, `tests/test_ops.py` | 生产强制：≥48 位密钥、禁演示登录、PostgreSQL、ALLOWED_HOSTS、FIELD_ENCRYPTION_KEY |
| 真实 AppID 小程序编译/真机 | **未验收** | `miniapp` | 缺微信开发者工具/微信环境证据；v0.2 仅做静态加固（防重复提交、GET 重试、演示登录开关） |
| 医疗信息处理合规审查 | **未完成** | `docs/SECURITY_PRIVACY.md` | 不能由代码自动代替 |
| 测试 | **57 项已通过** | `backend/tests`（test_api 35 + test_security 19 + test_ops 3） | `pytest -q`，覆盖 API 回归+安全边界+生产配置+迁移 |

## 不得暗示已经完成的事情

- 不要称“微信正式登录已联调通过”。目前只是写了真实接口、防重放与限速，未接真实凭据。
- 不要称“自动健康提醒已上线”。它只是应用内待办。
- 不要称“心理健康筛查准确”。模板不是临床量表。
- 不要称“已符合《个人信息保护法》”。合规需要实际主体、告知同意、内部制度、供应商、存储地域等条件审查。
- 不要称“可以直接部署生产”。生产仍需：机构账号开通流程、MFA 启用制度、PostgreSQL 备份恢复演练、HTTPS/反向代理、渗透测试、合规审批。
- 不要称“多实例限速已解决”。当前限速是单进程内存实现，多 worker 需换共享存储。

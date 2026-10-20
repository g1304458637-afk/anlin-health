# 变更记录

## v0.2 · 2026-10-21（安全加固批次，继任 AI 交付）

### P0-A 生产环境安全与认证

- 新增生产可用的工作人员密码登录 `/api/auth/login`：按账号+IP 速率限制（默认 10 次/分钟）、连续失败 5 次锁定 15 分钟（`LOGIN_MAX_ATTEMPTS`/`LOGIN_LOCKOUT_MINUTES` 可调）、登录审计（`password_login`/`account_locked`）。
- 可选 TOTP 两步验证（RFC 6238，纯标准库实现 `app/totp.py`）：`POST /api/auth/mfa/totp` 开通（密钥仅显示一次）、`DELETE` 关闭并轮换会话、`/api/auth/login/totp` 提交验证码。
- 密码策略 `validate_password_strength`：长度 10–128、至少三类字符、拒绝常见弱口令；修改密码 `POST /api/auth/change-password` 校验旧密码并**提升 token_version 使所有旧会话立即失效**；`POST /api/auth/logout-all` 全端登出。
- JWT 载入 `tv`（token_version），`deps.auth_user` 每次请求核对，停用/改密/登出即刻生效；令牌有效期改为 `SESSION_TTL_MINUTES`（默认 480 分钟）。
- 微信登录加固：一次性 code 缓存防重放（重放返回 401）、`errcode` 40029/40163 等显式拒绝、按 IP 速率限制、session_key 仍不落日志不返回。
- 居民线下档案认领流程 `claim_requests`：本人提交姓名+手机号匹配后仅**生成待核验申请**（不匹配时返回完全相同的响应，防枚举；每用户每小时 5 次限额），必须由同社区工作人员线下核验后批准才转移 `owner_user_id`；认领不自动产生社区护理同意。
- 知情同意证据复核链：协助建档/员工登记的纸质同意事件 `review_status=pending` 入复核队列，`GET /community/consent-reviews`、`POST /community/consent-events/{id}/review`；**复核驳回立即切断员工访问**（`consent_active` 排除 rejected）；居民自建同意自动 approved。
- 管理员账号生命周期 `/api/admin/users`：列表（不含凭据/健康数据）、停用（即刻杀会话，禁止停用自己和管理员）、启用、解锁。
- 安全响应头（nosniff/DENY/no-referrer/CSP；生产加 HSTS+no-store）与生产 TrustedHost（`ALLOWED_HOSTS`）；`validate_settings` 生产强制：强密钥、非演示登录、PostgreSQL、ALLOWED_HOSTS、FIELD_ENCRYPTION_KEY。
- 敏感字段静态加密：`EncryptedString`（AES-256-GCM，`app/crypto.py`）应用于居民手机号；生产必须配置 32 字节 base64 密钥，未配置时开发环境回落明文。
- Alembic 迁移体系：`backend/alembic.ini` + `migrations/`（初始迁移可 upgrade/downgrade，测试覆盖）；生产启动不再 `create_all`。
- 网页管理台：改用 `/api/auth/login`（开发与生产同一入口，支持 MFA 提示）、演示账号提示改为运行时探测 `/api/auth/demo-status` 动态显示、表单 label 与输入框显式关联（无障碍）、CSP 放行内联脚本（单文件控制台，提取 JS 后应移除 unsafe-inline）。

### P0-B / P1

- 小程序：`utils/api.js` 增加写操作防重复提交（在途拦截）、GET 弱网自动重试一次、统一 `logout()`；登录页演示入口由 `config.js` `enableDemoLogin` 控制；邀请码可轮换 `POST /api/me/invite-code/rotate`（旧码立即失效）；家属可拒绝授权 `POST .../family-grants/{id}/decline`。
- 修复 `scripts/dev_start.py` 直接运行时 `ModuleNotFoundError`。

### 测试与验证

- 后端测试 **35 → 57 全部通过**（新增 `tests/test_security.py` 21 项、`tests/test_ops.py` 3 项：锁定/限流/密码策略/会话失效/TOTP/管理员边界/认领防枚举/证据复核/邀请码轮换/加密落库/生产配置/迁移回滚）。
- 真实浏览器（ZCode 内置浏览器）对**真实后端**完成管理台 E2E：员工登录、居民列表、协助建档、血压录入、随访完成（v0.1 与 v0.2 各一轮）。
- 未完成（仍为阻塞项）：真实 AppID 小程序编译/真机 E2E、订阅消息、渗透测试、PostgreSQL 备份恢复演练、合规审查、MFA 的机构开通流程。

## v0.1 · 2026-10-21

- 全新编写原生微信小程序（8 页面）：登录、首页、档案、测量、问卷、答题、提醒、家庭。
- 全新编写 FastAPI/SQLAlchemy 后端：JWT 演示登录、真实微信登录代码交换逻辑、居民档案、目的同意、家属范围授权、测量数据、问卷、提醒、随访、操作审计、聚合信息。
- 全新编写社区网页管理台：仪表盘、居民登记、指标录入、走访任务。
- 后端 35 条 API 自动化测试通过；小程序 JavaScript 和 JSON 静态解析通过（不等于微信工具编译）。
- 以系统 Chromium+内存模拟 API 通过社区后台 UI 冒烟，生成静态效果图（不等于真实后端 E2E）。
- 配备工作交接主指令、完整需求/架构/安全/许可/部署/微信联调/QA 文档，CI 配置与试点 Docker 示例。

## 后续

以 `docs/STATUS_MATRIX.md` 和 `HANDOFF_PROMPT.md` 的 P0 / P1 / P2 为准。不要将上述试点当成可直接部署的生产系统。

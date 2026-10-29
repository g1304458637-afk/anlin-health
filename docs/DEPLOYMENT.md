# 运行、构建和部署

本说明对应隔离的本地开发工作树；尚未部署或连接正式数据库。先在临时 SQLite 库验证，再为目标环境制定备份和恢复方案。运行命令时只使用当前工作树的代码与依赖，不改动原始开发包中的 `.venv` 或 `.env`。

## 本地（推荐先验证）

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell 使用 .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
python scripts/dev_start.py --seed
```

`dev_start.py` 会解析 `backend/.env`，启动仅监听 `127.0.0.1:8000` 的 API、居民/家属门户和工作人员工作台。默认演示账号详见 README。如果本机端口占用，可用 `--port 8001`；当前交付入口为网页，小程序已封存。

> **已有 v0.1 数据库**：首个 Alembic 版本是当前结构的建库版本，不是从 v0.1 原表升级的脚本。不要直接对旧库执行 `upgrade head`、`stamp head` 或删除旧库。旧数据迁移需要单独制定映射、加密、备份与恢复流程。

### 测试

```bash
cd backend
python -m pytest -q
python -m compileall -q app
python scripts/check_web_syntax.py  # 需要 Node.js，检查两个 HTML 页面中的内联 JS
```

### 数据库迁移（Alembic）

```bash
cd backend
# 指向目标库（生产用 PostgreSQL URL）
DATABASE_URL="sqlite:////tmp/mig_check.db" .venv/bin/alembic upgrade head     # 建表
DATABASE_URL="sqlite:////tmp/mig_check.db" .venv/bin/alembic downgrade base   # 只在临时库检查
# 新增模型变更后生成迁移脚本：
DATABASE_URL="sqlite:///./_autogen.db" .venv/bin/alembic revision --autogenerate -m "describe change"
```

规则：**任何模型变更必须先出迁移脚本并在临时库验证**，再进入主干；生产执行迁移前必须完成备份并演练过恢复。开发环境仍以 `create_all` 建新库，生产环境禁止。镜像包含 Alembic 配置和全部迁移文件，但容器启动不会自动迁移。

### 私人报告存储与提取 worker（v0.5 本地候选）

`REPORT_STORAGE_DIR` 默认是 `./private_reports`，**必须位于公开 `/static` 目录之外**。容器或正式环境应改为可持久化、只允许服务账号读写的私有位置，例如 `/app/data/reports`，并连同数据库执行备份及恢复演练；不要把报告放进镜像、源码、公开静态目录或临时容器层。报告原件写入后用 SHA-256 校验，配置 `FIELD_ENCRYPTION_KEY` 时原件、模型候选和人工确认文本使用 AES-GCM；正式环境缺密钥会拒绝。开发环境无密钥时可明文，不能放真实报告。

上传仅限 PDF、PNG、JPEG、WebP，原始请求体最大 10 MiB、5 页。上传不触发云端发送；用户对**每份报告**勾选同意并请求提取后，才会逐页发送可提取文字或页面图像到受信任的 DeepSeek 地址。`DEEPSEEK_API_KEY` 只从进程环境或密钥管理注入；本地工作树继承的密钥不要复制进 `.env`、仓库或日志。当前默认 `DEEPSEEK_MODEL=deepseek-flash`，实时模型目录验证不等于正式服务验收。代码使用 `pypdf==6.10.0`、`pypdfium2==5.14.0`、`Pillow==12.3.0`；没有安装 PaddleOCR，OCR 字样不能解释为本地 PaddleOCR 能力。

默认 `REPORT_WORKER_ENABLED=true` 会在 API 进程内启动数据库队列 worker。需要独立进程时先设置 `REPORT_WORKER_ENABLED=false`，然后在相同代码、数据库、私有报告目录和密钥环境下启动：

```bash
cd backend
python -m app.report_worker
```

处理任务使用 7 分钟数据库租约。崩溃/中断后到期的任务转为失败，**不会静默重发**；用户检查后主动重试，累计最多 3 次。每页发送前重新检查申请账号、会话版本、社区同意和任务状态。正式环境还需验证多进程行为、报告留存/删除、备份一致性、密钥轮换、供应商数据处理及人工核对流程。

### 首次初始化（空库）

只针对已通过 `alembic upgrade head` 建立的空库。先显式设置 `DATABASE_URL`；脚本不会使用默认数据库地址，也不会自行建表。命令行不传密码，默认从终端分别读取管理员和员工密码并确认；无人值守时可以分别从密钥管理器注入 `ADMIN_BOOTSTRAP_PASSWORD`、`STAFF_BOOTSTRAP_PASSWORD`。两者都须符合系统密码强度规则，切勿记录到 shell 历史、仓库或 `.env`。例如在隔离的临时 SQLite 库：

```bash
cd backend
export DATABASE_URL="sqlite:////tmp/anlin-first-run.db"
.venv/bin/alembic upgrade head
.venv/bin/python scripts/init_instance.py \
  --community "示例社区" --admin-username first_admin --admin-name "首位管理员" \
  --staff-username first_staff --staff-name "首位员工"
```

首次运行在一个事务里建立社区、管理员、员工和两份非临床问卷模板，不插入居民、健康数据或演示账号。初始化对象匹配的重复运行只报告已初始化，不重置密码或权限；发现不同、缺失或额外的初始化对象则拒绝，交由人工核查。正式环境应在备份与恢复演练完成后，在受控终端执行相同流程；首次登录后分别绑定员工的 MFA。

### API Schema

运行后访问 `/docs` 查看 FastAPI OpenAPI UI；`/openapi.json` 提供机器可读接口说明。

## Docker：仅限本机演示

```bash
cp backend/.env.example backend/.env
# 在根目录执行：
docker compose -f ops/compose.demo.yml build
docker compose -f ops/compose.demo.yml up -d
# compose 默认不灌入演示用户；可按下方方式单独初始化虚构数据。
```

镜像已包含 `app/seed.py`（只生成虚构样例）。需要演示登录账号时，首次启动后执行：

```bash
docker compose -f ops/compose.demo.yml exec api python -m app.seed
```

容器仅在 `127.0.0.1:8000` 开放。**不要修改成 `0.0.0.0:8000` 暴露公网**；这个 compose 文件不会实施 TLS、正式鉴权、审计集中化与备份。Dockerfile 将 `REPORT_STORAGE_DIR` 设为 `/app/data/reports`，演示 compose 的 `/app/data` 为命名卷；正式环境仍须核对目录权限、持久性和数据库/文件一致备份，不能依赖容器可写层保存原件。

## 生产实施路线（仍需人工完成，切勿直接部署）

1. PostgreSQL + Alembic 迁移 + 最小权限账号 + 定期备份与**恢复演练**（迁移脚本已具备，演练未做）。
2. 机构账号开通流程：在已迁移的空库运行上面的首次初始化脚本；后续员工开通需单独审批。按需开通 TOTP（`POST /api/auth/mfa/totp`，认证器绑定走人工流程）；生产自动禁用 `/api/auth/dev-login` 与演示提示。
3. 反向代理/平台提供 HTTPS（应用在生产自动下发 HSTS）；`ALLOWED_HOSTS` 必须配置，否则启动即拒绝。
4. `SECRET_KEY` ≥48 位随机、`FIELD_ENCRYPTION_KEY` 为 32 字节 base64（生产缺一启动即拒绝），均从密钥管理注入。**密钥轮换**（v0.7 起）：新密钥设为 `FIELD_ENCRYPTION_KEY`、旧密钥放入 `FIELD_ENCRYPTION_PREVIOUS_KEYS`（逗号分隔，最旧在前），运行 `python scripts/rotate_field_keys.py --check` 直至 TOTAL 为 0，再从环境移除旧密钥；脚本运行期间应用保持可读。
5. 限流与微信 code 一次性防重放（v0.7 起）保存在数据库表 `rate_events`/`single_use_tokens`，多 worker/多实例部署下依然有效，无需额外共享存储；高并发场景可再演进为 Redis。
6. 微信生产凭据仅在服务端配置；code 已做跨进程一次性防重放，仍需真实联调验收。
7. 医疗健康敏感个人信息：报告已提供本人隐私删除（`DELETE /api/reports/{id}`，删原件与全部派生数据）与测量软删除审计快照；跨备份/从库的删除同步、留存期、日志脱敏、供应商数据传输审查、合规评审、渗透测试及真实医院样本人工验收，全部仍是阻塞项。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `APP_ENV` | development | production/test/development |
| `ENABLE_DEMO_AUTH` | false | 仅 dev/test 可显式开启演示登录与控制台演示提示 |
| `SECRET_KEY` | 空 | JWT 服务端签名密钥，生产强制 ≥48 位 |
| `DATABASE_URL` | sqlite:///./community_health.db | SQLAlchemy URL（生产强制 PostgreSQL） |
| `ADMIN_BOOTSTRAP_PASSWORD`、`STAFF_BOOTSTRAP_PASSWORD` | 无 | 可选，仅首次初始化命令读取；不设置则安全交互输入 |
| `WECHAT_APPID` | 空 | 小程序 AppID，仅服务端 |
| `WECHAT_APPSECRET` | 空 | 小程序 AppSecret，仅服务端 |
| `CORS_ORIGINS` | 空 | 逗号分隔的跨域来源白名单 |
| `ALLOWED_HOSTS` | 空 | 生产必填；Host 头白名单，缺失启动失败 |
| `SESSION_TTL_MINUTES` | 480 | 访问令牌有效期（分钟，最小 5） |
| `FIELD_ENCRYPTION_KEY` | 空 | 32 字节 base64；生产必填，用于手机号等敏感列 AES-GCM 加密 |
| `REPORT_STORAGE_DIR` | `./private_reports` | 非公开报告原件目录；容器/生产应设为私有持久卷路径 |
| `REPORT_WORKER_ENABLED` | true | API 内嵌报告队列 worker；设 false 时需另起 `python -m app.report_worker` |
| `DEEPSEEK_API_KEY` | 空 | 服务端密钥；每份报告明确允许后才用于云端提取，聊天助手也使用 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 仅允许官方 DeepSeek 地址；非生产可用回环地址进行测试 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 助手与报告转录模型标识，需按当前供应商目录核验 |
| `LOGIN_MAX_ATTEMPTS` | 5 | 连续失败锁定阈值 |
| `LOGIN_LOCKOUT_MINUTES` | 15 | 锁定时长 |
| `LOGIN_RATE_PER_MINUTE` | 10 | 密码/TOTP 登录每分钟每账号+IP 上限 |
| `WECHAT_LOGIN_RATE_PER_MINUTE` | 10 | 微信登录每分钟每 IP 上限 |

## 回滚

- **代码**：Git 标签 `baseline-v0.1` 起全程留痕，可回退任意提交。
- **数据库**：`alembic downgrade` 可回退迁移（SQLite 上已测试）；**生产回滚前必须先备份**，且 downgrade 不恢复被删除的数据。备份/恢复演练本身仍是未完成项。

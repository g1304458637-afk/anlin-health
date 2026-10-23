# 安邻健康 · 社区健康管理系统 V0.2 工程工作包

> **2026-10-21 · 可运行的开发试点工程，绝非可以直接处理真实居民健康信息的生产系统。** 开源许可：MIT（见 `LICENSE`）。

FastAPI + SQLAlchemy 后端、原生微信小程序、单文件网页管理台。居民自记血压/血糖/体重、填写非临床自评问卷、事项提醒；家属经本人限时授权查看；社区工作人员在双重约束（本社区 + 有效知情同意）下协助建档、录入与随访；管理员仅聚合视图。

## 0. 先看这三份文件

1. `docs/STATUS_MATRIX.md` —— 哪些功能真的写好了，哪些没做好。
2. `HANDOFF_PROMPT.md` —— **复制给其他 AI 编程助手的完整交接指令**。
3. `docs/PRD.md` —— 全部需求、角色与验收标准。

## 1. 本包有什么

```
社区健康管理_开发工作包_v0.1/
├── README.md                          本文档，启动入口
├── HANDOFF_PROMPT.md                  交给后续 AI 的开发主指令
├── backend/
│   ├── app/                           完整 FastAPI 业务源码（8 类业务路由模块，含管理员）
│   ├── static/console.html            社区员工网页管理台，无需前端构建
│   ├── static/portal.html             居民/家属网页端（/app），无需前端构建
│   ├── tests/                          57 个后端自动化测试（API 回归 + 安全边界 + 运维配置）
│   ├── requirements.txt               固定版本运行依赖
│   ├── requirements-dev.txt           测试依赖
│   └── .env.example                   开发环境配置模板
├── miniapp/                           原生微信小程序源码（8 个页面）
│   ├── pages/                         登录、首页、档案、健康数据、问卷、填答、提醒、家庭
│   ├── utils/api.js                   全部实际请求后端的网络层
│   ├── config.js                      本地 API URL 配置（必须改）
│   └── project.config.json            微信开发者工具工程
├── docs/
│   ├── PRD.md                          产品需求及验收用例
│   ├── STATUS_MATRIX.md                完成 / 未完成清单及阻塞项
│   ├── ARCHITECTURE.md                 数据模型、访问控制、系统结构
│   ├── SECURITY_PRIVACY.md             具体安全风险、健康数据合规检查
│   ├── WECHAT_INTEGRATION.md           小程序联调操作与订阅消息缺口
│   ├── OPEN_SOURCE_RESEARCH.md         GitHub参考与许可审核
│   ├── DEPLOYMENT.md                   本地启动 / Docker / 发布前检查
│   ├── QA_REPORT.md                    自动化测试方法及已验证证据
│   └── API_EXAMPLES.md                 主要 API 调用路径
├── ops/                               Dockerfile 与 compose 配置
└── assets/                            可选的本地页面截图与 OpenAPI JSON
```

## 2. 运行后端和网页后台

建议 Python 3.11–3.13。先进入 `backend/`：

```bash
cd backend
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
# PowerShell: Copy-Item .env.example .env
```

**注意：项目没有引入 python-dotenv，不会自动加载 `.env` 文件。** 可以通过终端导出变量，或使用下面的便捷启动脚本（自动加载 `backend/.env`）：

```bash
python scripts/dev_start.py --seed
```

默认：
- API: http://127.0.0.1:8000/docs
- 后台: http://127.0.0.1:8000/console
- 健康检测: http://127.0.0.1:8000/healthz
- 账号：`staff`、`staff2`、`resident`、`family`、`admin`
- **仅限本地虚构演示数据**的共同密码：`DemoPass2026!`

管理台登录使用 `/api/auth/login`（与生产同一路径，含锁定与速率限制；演示提示仅在后端显式开启时显示）。**v0.1 升级：模型已加新列/新表，旧 `community_health.db` 需删除后重新 seed，或按 `docs/DEPLOYMENT.md` 用 Alembic 升级。**

`staff` 为春和社区职员；`staff2` 属于另一社区；`resident` 已创建本人档案；`family` 为家属。数据库包含虚构居民及演示测量数据。**禁止将演示账号和密码部署到公网。**

## 3. 居民/家属网页端（当前唯一入口）

启动后端后访问 **http://127.0.0.1:8000/app**：

- **居民**：账号由社区工作人员开通（工作台或 `POST /api/community/accounts`），首次登录后自主建档、记录血压/血糖/体重、填问卷、设提醒、用邀请码授权家人；
- **家属**：登录页"家属注册"自助开通（注册本身零权限，须持长辈邀请码被授权后才可查看）；
- 微信小程序（`miniapp/`）自 v0.3 起封存保留、不再维护，微信资质与真机验收不再是本工程的阻塞项。

## 4. 已写好的业务

居民：自建健康档案、健康指标登记/历史列表/轻量趋势图、填写两份原创演示问卷、事项提醒、社区授权撤回、家属共享授权/撤回。家庭：独立邀请码、接受本人授权、按范围查看健康数据。社区：登录、按社区查看有授权的居民、协助建档、健康指标录入、发起/完成随访任务。管理员：聚合数量，不能默认查看个人健康数据。后端包含角色权限、数据隔离、基本输入校验与不记录原始健康内容的操作审计。

## 5. 必须明确的缺口

- **没有微信订阅消息自动推送**：当前提醒只在小程序内展示。
- **没有生产级工作人员认证/MFA/限速**：演示登录被生产环境强制关闭。
- **没有真实的纸质知情同意证据核验**：当前只登记“证据编号”，不能据此处理真实居民信息。
- **没有数据库迁移、数据备份恢复演练、数据删除全流程、合规评审**。
- **没有通过微信开发者工具与真实 AppID 的小程序端到端验收**。
- **没有临床量表或疾病风险预测算法**：情绪问卷为原创非临床记录，不能当作心理疾病筛查结果。

把这些列为生产发布阻塞项，而不是“后续可选增强”。

## 6. 技术选型说明

用 **FastAPI + SQLAlchemy + SQLite（开发）/ PostgreSQL（正式环境计划）+ 原生微信小程序 + 原生 HTML/CSS/JS 管理台**，原因是当前工程环境已存在 FastAPI、SQLAlchemy、PyJWT、pytest，可以实际运行和执行自动化测试；Django/DRF 不可安装，强行承诺一个无法验证的 Django 成品风险更高。

该项目和以前讨论的 `RuoYi-Vue-Plus`、`Vant Weapp`、`HealthTrack` **不是直接集成**：本包完全重新实现最小业务闭环，参考开源项目的架构和功能模式，没有直接复制或重新分发第三方完整仓库。后续如果团队决定使用 Java/若依，须先完成迁移评估，不要把两个独立后台框架直接拼起来。

## 7. 交接建议

将整个 ZIP 解压后让 Codex / Claude Code 打开根目录，直接发送 `HANDOFF_PROMPT.md` 的全文，让它先验证测试和安全边界，再完成生产阻塞项。**不要把试点工程误称为正式医疗软件，也不要在未经审批的环境中放入真实姓名、联系方式或健康记录。**

## 8. 开源许可与素材说明

- 代码许可：**MIT**（`LICENSE`）。其中附加中文说明：本工程为试点，不构成医疗器械，接入真实居民数据前须自行完成合规与安全评估。
- 管理台引用的人像照片/视频、牡丹图片为设计规格附带的示例 CDN 外链素材，版权不属于本项目；生产环境请替换为自有素材并更新 `ASSET_CSP_ORIGINS`。详见 `docs/OWNERSHIP_AND_LICENSE.md`。

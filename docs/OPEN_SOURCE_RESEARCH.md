# GitHub 开源参考与许可检查（2026-10-21）

**重要：当前交付工程全部核心业务代码为新编写，未直接复制下列仓库的源码。** 不意味着列表中的所有仓库都能无条件整体拿来闭源使用；每次真正引入必须重新确认准确提交版本、许可证、NOTICE、依赖、漏洞及演示素材许可。

| 仓库 | 地址 | 仓库标称许可 | 本工程采用的方式 |
|---|---|---|---|
| RuoYi-Vue-Plus | https://github.com/dromara/RuoYi-Vue-Plus | MIT | 仅参考社区管理、权限/组织管理理念，未复制/集成大型 Java 后台 |
| Vant Weapp | https://github.com/youzan/vant-weapp | MIT | 仅参考原生小程序组件思路，V0.1 UI 自行实现、无此依赖 |
| HealthTrack | https://github.com/EzekielTheMad/healthtrack | MIT | 仅参考家庭授权与健康指标记录的功能模型，未复制源文件 |
| SurveyJS Form Library | https://github.com/surveyjs/survey-library | MIT | 仅参考 JSON 驱动问卷的数据结构；前端自己实现原生表单 |
| Survey Creator | https://github.com/surveyjs/survey-creator | 商业授权 | **未使用**；若需要拖拽问卷编辑器，商业环境先购买符合条款的许可 |
| Community Health Toolkit (CHT) | https://github.com/medic/cht-core | AGPL-3.0 | 仅学习基层医疗随访流程，不复制源代码，避免闭源产品许可不兼容 |

## 官方许可证/说明地址

- 若依 MIT： https://github.com/dromara/RuoYi-Vue-Plus/blob/6.X/LICENSE
- Vant MIT： https://github.com/youzan/vant-weapp/blob/dev/LICENSE
- HealthTrack MIT： https://github.com/EzekielTheMad/healthtrack/blob/main/LICENSE
- CHT AGPL： https://github.com/medic/cht-core/blob/master/LICENSE
- SurveyJS 官方许可： https://surveyjs.io/licensing

## 为什么本包不直接打包第三方巨大源码

1. 当前目标是把一个可测试的健康管理最小闭环做完，而不是同时维护两套 API 或重复登录/角色系统。
2. 直接把若依的 Spring Boot 后台、HealthTrack 的 Next.js、CHT 的 AGPL 系统拼接起来，技术维护和数据合规风险高于收益。
3. 不复制完整第三方代码也可以利用它们的设计思想；原仓库的实际可运行代码应在选定技术路线以后，**按明确的版本/提交哈希单独引入并保留相应许可文件**。
4. 许可必须按最新上游文本和依赖实际情况逐项核查。尤其不要将 AGPL 代码误当 MIT，也不要将公开可见的毕业设计仓库误认为自动获得商业使用权。

本工程所使用的 Python 依赖（FastAPI、SQLAlchemy、PyJWT 等）和微信开发者工具需要分别遵守它们的官方条款；用户应在正式发布前执行依赖软件组成分析（SBOM）和许可合规审计。

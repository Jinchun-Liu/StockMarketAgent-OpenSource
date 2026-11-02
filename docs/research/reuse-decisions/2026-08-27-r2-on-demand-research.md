# R2 指定股票深度研究复用决策

日期：2026-08-27
结论：`adapt + reference + build`。复用仓库已有通用运行时、事件/证据修订表和 Port，只自行实现
StockMarketAgent 特有的 A 股 PIT、研究会话、股票无关全球事件刷新、确定性按需匹配、证据主张和
生命周期规则；未增加第三方依赖或平行事件/图数据库。

## 需求与硬边界

输入是用户明确确认的一只沪深北 A 股，输出是十区块、逐条证据、七日生命周期的完整研究报告。系统
必须支持幂等异步任务、检查点恢复、取消、三十分钟停滞、全球事件库与证券解耦、来源/单位/PIT硬门，
并保持研究与纸面验证边界。R1/R2 候选零外网，只使用确定性合成数据和模型 double。

## 仓库内复用

- `FastAPI + Pydantic`：复用公开 HTTP、RFC 9457 错误、Schema 唯一源码和 OpenAPI 生成链路；
- `PostgreSQL + SQLAlchemy + UnitOfWork`：复用追加迁移、事务边界、审计和 outbox 模式；全球事件继续
  写入现有 `market_events/raw_documents/event_documents/event_entities` 与 revision 链；
- `Celery + Valkey + JobDispatcherPort`：复用有界异步调度和取消边界；
- `SeaweedFS S3 + ObjectStorePort`：复用对象制品存储，使用现有 MinIO-compatible Python client；
- `LLMProviderPort + ContentQualityPort`：复用模型隔离与确定性内容校验边界；
- `Next.js + React + 生成 OpenAPI 类型`：复用 Web shell、同源 API 转发、严格 TypeScript 和无障碍基线。

这些组件的版本、许可证和供应链由现有锁文件与 `THIRD_PARTY.md` 管理，本次没有新增依赖或 SDK。

## 产品工作流参考

- AlphaEdu：只借鉴简洁任务入口、多专业视角、Bull/Bear、未知项、反证条件和来源下钻；不复制代码、
  数据、提示词、自由文本契约或多角色对话，不采用同一供应商伪装双源的做法。
- TIKR Research Hub、FinChat、Koyfin Equity Research：只参考“搜索公司后加载完整研究”的公开工作流；
  不复制代码、内容、品牌、数据或视觉资产。

## 自研缺口

通用组件和公开产品都不能直接满足以下项目不变量，因此只在领域层自行实现最小必要能力：

- 沪深北 A 股规范身份、代码/名称消歧和 PIT 主数据；
- 不含证券参数的启动/每小时全球事件刷新、幂等重放、更正/冲突并列和失败不推进 cursor；
- 四级直接度、十二影响渠道、最多一跳的确定性暴露匹配，以及共现/两跳拒绝；
- 事件库无相关结果与不可用、过期、缺权的强类型状态区分；
- `ResearchSession` 状态机、transition、heartbeat、checkpoint、七日到期和二十四小时清理；
- `fact/system_inference/model_hypothesis` 强类型主张及证据/反证/可证伪关系；
- 对未来 revision、错误单位、模型注入数值、伪双源、投资建议和缺失引用的确定性发布拦截；
- 只允许稳定宏观参考事实进入长期语义记忆的数据库约束。

## 退出与复审

合成适配器只用于 R1/R2 候选，生产开关失败关闭。引入真实来源、真实模型、新 SDK、另一对象存储、
平行事件/图数据库、两跳以上暴露或改变七日生命周期时必须重新评估。正式发布前必须通过来源授权、
shadow trial、live E2E 和模型资格四项门禁。

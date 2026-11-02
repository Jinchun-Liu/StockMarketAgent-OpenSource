# R2：指定股票与全球事件深度研究实施规格

状态：`in_progress`（2026-09-09）。私人真实入口已部署，但原五项及两项定向研究均失败、零报告；新修复本地验证完成，尚未部署。当前需求及恢复顺序见真实资料任务 (upstream private record, excluded)。机器状态以
`governance/delivery.json` (upstream private record, excluded) 为准；生产来源与模型仍被四项正式发布门禁阻断。

## 用户结果

用户搜索一只沪深北 A 股，明确确认规范证券后创建独立 `ResearchSession`。系统回溯五年主窗口、最近
二十季度与上市以来重大事件，并从与用户股票解耦的全球国际官方事件库按需匹配零跳/一跳暴露；期间只展示阶段和最近进展时间；报告通过证据硬门后一次性展示。用户可
查看七日内历史、取消、对可重试失败或停滞会话复用匹配的有效checkpoint继续，也可提前删除。

这条流程不修改 R1 的 `active_target`，不创建 watchlist，也不启动未来监测、通知或多股票工作区。
R1 的盘前任务只作冻结兼容；R2 不继承隔夜六块、08:55/09:05/09:40 调度、个股通知或 SSE。

## 当前真实运行链路

当前入口使用API v2和报告2.1：`确认身份 → 空事实包/真实API → Tavily → 受控网页/pypdf → Flash提取 → Pro独立审核 → 每项最多三次额外补抓 → 研究/评分/核验 → PostgreSQL与私有S3 → Web`。六维缺项不评分、总分为空，部分结果和checkpoint恢复保留；原文与报告分别审核。实际七个研究都尚未到达提取/审核/报告，不能把此处路径定义写成已贯通。

### 冻结合成基线的链路（历史验证）

```text
stock-agnostic global event refresh (startup + every 60 minutes)
  -> MarketEvent PostgreSQL revision chain + source cursor
Web search
  -> GET /api/v1/instruments/search
  -> explicit instrument confirmation
  -> POST /api/v1/research-sessions
  -> PostgreSQL session + transition + transactional job outbox
  -> scheduler relay
  -> Valkey/Celery worker
  -> PIT company evidence packet + global catalog watermark
  -> deterministic zero-hop/one-hop exposure matching
  -> model-led narrative
  -> deterministic publication validator
  -> SeaweedFS S3 artifact + PostgreSQL report/claims/event
  -> Web polling + complete report
```

证券搜索仍使用固定、离线且带DEV_ONLY/UNVERIFIED标识的身份快照；真实公司与主营需进一步对照官方原文核实。原创合成研究证据/事件只用于隔离测试和历史读取，不能填入真实研究。当前真实页面显示DEV_ONLY/UNVERIFIED及真实阶段，历史合成报告保留原合成标识；生产环境仍失败关闭。

## 全球事件库与按需匹配

- 复用 `market_events`、`raw_documents`、`event_documents`、`event_entities`、`event_claims` 和现有修订链；
  `PostgresMarketEventRepository` 实现兼容 1.1 行业查询，1.0 读取保持不变。
- `stock_assistant.policies.refresh.v1` 使用强类型 request/result；请求只有来源时间桶、manifest 和运行上下文，
  不含 `instrument_id`、watchlist 或 active-target revision。
- 合成运行时启动即刷新，之后每 60 分钟刷新原创固定事件集。同来源、小时桶与 manifest 重放幂等；
  更正追加 revision，冲突并列保留，失败事务不推进 source cursor。
- 研究 job 同时读取 PIT 公司业务/行业/地区/已披露暴露与 `as_of` 可见事件 revision。确定性匹配只接受
  官方直接点名、公司披露确认、带证据的一跳边或行业背景；新闻共现、模型常识、匿名关系和两跳路径均拒绝。
- 渠道固定为出口需求、投入、商品、汇率、利率、资本流动、制裁、物流、技术管制、补贴、国产替代、
  通胀政策；方向为 supportive/adverse/mixed/unknown。
- 事件事实发布为 `fact`；对股票的条件化影响发布为 `system_inference`，记录事件 revision、直接度、
  渠道、方向、路径、规则、截点、支持、反证和不确定性。模型只组织叙事。
- “库已刷新但无相关事件”使用 `NO_RELEVANT_GLOBAL_EVENTS`；库未刷新、过期或缺权使用不同 CoverageGap。
  无相关事件仍可发布报告，库不可用时模型不得补全。

## 搜索与消歧

- 接受六位代码、`603986.SH`、`XSHG:603986`、公司简称和全称；规范身份只使用
  `XSHG|XSHE|XBSE:NNNNNN`。
- 精确命中仍由用户确认；模糊或多候选不得自动猜测。响应包含匹配类型、上市状态、PIT 截点和
  `revision_known_at`。
- 搜索只返回 `revision_known_at <= as_of` 的 A 股主数据；无结果、非 A 股、停牌和退市均为显式结果，
  不回退到外网搜索。
- 当前目录版本覆盖 5,884 条沪深北当前/历史退市证券身份。动态停复牌不由静态目录猜测；深交所和北交所
  缺少已核验公司全称时只支持代码/证券简称搜索并提示后续核验，禁止把简称伪装成公司全称。
- 四个历史换码证券作为当前规范身份的代码/名称别名搜索，不重复计入退市身份；目录过期后所有读取入口
  失败关闭。
- `InstrumentMasterDataPort 1.1` 的 `search()` 与旧 1.0 响应保持不变；显式核验状态由
  `InstrumentMasterDataV2Port.search_v1_1()` 和新的认证目录搜索端点提供。

## 会话与 HTTP 契约

| 接口 | 语义 |
|---|---|
| `GET /api/v1/instruments/search?q=&limit=` | 返回规范候选和 PIT 截点 |
| `GET /api/v2/instruments/directory-search?q=&limit=` | 认证 Web 使用的 1.1 候选，增加公司全称核验状态 |
| `POST /api/v1/research-sessions` | 幂等创建，返回 `202 + session_id + job_id` |
| `GET /api/v1/research-sessions/{id}` | 状态、阶段、心跳、检查点、覆盖缺口和终态 |
| `GET /api/v1/research-sessions` | 七日保留期内的游标分页历史 |
| `GET /api/v1/research-sessions/{id}/report` | 只读取完整终态报告；区分 not ready/expired/not found |
| `POST /api/v1/research-sessions/{id}/cancel` | 幂等取消 DB/outbox/Celery 任务 |
| `POST /api/v1/research-sessions/{id}/retry` | 仅恢复 `stalled` 或可重试失败；同键回放同一新 job |
| `DELETE /api/v1/research-sessions/{id}` | 立即终止访问并进入 24 小时清理队列 |

任务名固定为 `stock_assistant.research.generate_report.v1`；结果事件固定为
`com.stockassistant.research.report.ready.v1`。公开 HTTP、任务、事件、Port、Schema、OpenAPI 和前端类型
均登记在 `governance/interfaces.json`，Pydantic 是 Schema 唯一源码。

## 状态机、恢复和生命周期

会话状态为 `queued/running/succeeded/failed/cancelled/stalled/expired/deleted`。阶段只描述已发生工作，
不得换算虚假百分比。每次进展追加 transition 并更新心跳/检查点；连续 30 分钟无进展的批量看门狗
原子进入 `stalled`，取消仍活跃的 job outbox 回执并允许检查点重试。

创建、取消、重试和删除都有事务幂等语义。创建与 job outbox 同一事务提交；报告、逐条 claim、短期
上下文、完成 transition 和 ready event 同一 Unit of Work 提交。重复 worker 只能回放同一报告；
取消或删除状态不得发布报告。

报告从完成时起保留七天。到期或提前删除立即不可访问，并在 24 小时内清除报告正文、会话上下文、
embedding 和对象制品。清理任务提前五分钟进入待处理队列，为 15 秒调度轮询和实际删除预留边界；
清理后只保留最小 tombstone、任务回执、正文 hash 和证据索引。

## 模型主导与确定性发布硬门

模型只负责问题拆解、假设、Bull/Bear 与叙事。程序负责身份、PIT、单位、计算、来源独立性、证据关系和
发布资格。报告必须恰好包含：

1. 标的、截点、覆盖与缺口；
2. 业务模式和关键变化；
3. 五年财务质量与二十季度趋势；
4. 历史估值位置和可复算口径；
5. 上市以来重大事件、国际事件与宏观暴露；
6. Bull 论点；
7. Bear/风险论点；
8. 来源冲突、未知项与不确定性；
9. 可证伪条件和待核验问题；
10. 逐条证据、时间、来源角色、模型及规则版本。

每条 claim 只能是 `fact`、`system_inference` 或 `model_hypothesis`。事实必须有可读 `evidence_id`；系统
推断必须记录输入、公式、规则版本和截点；模型假设必须同时记录支持、反证、可证伪条件与不确定性。
模型不得引入未引用数值。未来 revision、未验证单位、同一 provider/独立来源组伪装双源、无引用事实、
Buy/Sell、目标价和收益承诺全部阻止发布。

最低事实不足、模型不可用和报告验证失败分别返回 `INSUFFICIENT_EVIDENCE`、`MODEL_UNAVAILABLE`、
`REPORT_VALIDATION_FAILED`，不得展示半成品。

## 数据和长期记忆

迁移 `20260827_0014` 只追加表和列；前向修复迁移 `20260827_0015` 为会话 tombstone
追加清理完成时间，防止无报告会话反复占据有界清理批次：

- `research_sessions`、`research_session_transitions`、`research_job_outbox`、
  `research_session_mutation_receipts`；
- `research_reports`、`research_report_claims`、`research_session_contexts`；
- `macro_reference_facts`。

`macro_reference_facts` 是唯一数据库长期语义记忆表，只接受 macro/institutional/taxonomy/
historical-reference 范围、stable/historical-final 稳定性和 deterministic-reference 来源。个股、当前宏观
数值、预测、模型结论与会话 embedding 在数据库约束层失败。`EpisodicMemoryPort` 保留兼容声明，但 R2
运行时不绑定它。

原始证据、事件 revision、rights snapshot 与审计记录按权利/PIT政策保留，它们不是模型长期记忆。

## 复用决策与指标

复用 FastAPI/Pydantic、PostgreSQL/SQLAlchemy、现有事务 UoW/outbox、Celery/Valkey、SeaweedFS S3、
`JobDispatcherPort`、`ObjectStorePort`、`LLMProviderPort` 和 Next.js。AlphaEdu 只借鉴简洁任务入口、
多专业视角、Bull/Bear、未知项、反证和来源下钻；不复制其代码、数据或自由文本契约。TIKR、FinChat、
Koyfin 只用于核对“搜索公司后加载完整研究”的公开产品流程。

本阶段没有新增跨产品统一量化指标，因此 `governance/metrics.json` 无改动；阶段与心跳是状态，不是完成率。

## 验收与降级

- 代码、简称、全称、空格/大小写、歧义、无结果、非 A 股、停牌和退市搜索测试；
- OpenAPI/Schema/前端类型一致，金额不用 float，时间全部带时区；
- PIT、未来数据、数值、单位、伪双源、引用与投资建议注入测试；
- 创建/变更幂等、重复 worker、取消、停滞、重试、outbox 重放和崩溃恢复测试；
- 七日到期、提前删除、对象失败补偿、正文不可恢复和 tombstone 可审计测试；
- R1 `XSHG:603986` 与 `R1_TARGET_LOCKED` 回归；
- 全球事件请求无证券字段、每小时调度、100 次重放、更正/冲突/PIT/cursor 失败关闭测试；
- 四级直接度、十二渠道、两跳/共现拒绝、行业背景降级、支持/反证与未来 revision 拒绝测试；
- Docker 全球事件刷新→PostgreSQL→Web 搜索→ResearchSession→worker→事件匹配→证据硬门→对象存储/数据库→Web 合成 E2E；
- 最终唯一完整门禁 `make integration-check`。

生产来源、真实模型或对象存储故障时失败关闭，不切换到未授权网络、旧报告或模型补全。

## 生产发布阻断

以下四项不阻断合成开发，但任何一项未通过都不得移除“非生产数据”标识：

- `PRODUCTION_SOURCE_AUTHORIZATION_NOT_VERIFIED`
- `PRODUCTION_SHADOW_TRIAL_NOT_COMPLETED`
- `PRODUCTION_LIVE_E2E_NOT_VERIFIED`
- `PRODUCTION_MODEL_RUNTIME_NOT_QUALIFIED`

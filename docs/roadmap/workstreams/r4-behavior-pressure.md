# R4：研究时 PIT 市场结构快照实施规格

状态：后续规划，尚未进入实现。R4 只在用户发起 `ResearchSession` 时计算一个 PIT 市场结构快照；
不持续进行十分钟个股监控、不生成盘中档位变化提醒、不实现 09:40 反馈、通知或 SSE。
机器状态与阻塞码以 `governance/delivery.json` 为准。
产品事实源：`docs/product/behavior-pressure-product-spec.md`
方法事实源：`docs/data/behavior-pressure-methodology.md`
指标事实源：`docs/data/behavior-indicator-catalog.md`
工程事实源：`docs/architecture/behavior-pressure-framework.md`

## 用户结果

用户在完整研究报告中读取一个与会话 `as_of` 绑定的市场结构快照：价格/成交覆盖、可复算特征、历史
位置、状态解释、反证和缺口。该快照是报告输入，不是实时仪表盘、概率预测、买卖信号或后台监测状态。

## 当前 R4 边界

- 每个研究会话最多计算一次；重试复用同一 PIT 数据 manifest，后来行情不得改写已发布快照。
- 计算只消费截至 `as_of` 可见且单位验证的行情修订，缺口导致 abstain，不以模型常识补值。
- 不读取 `active_target/watchlist`，不调度未来证券任务，不向 R3 发送 inbox/SSE/通知。
- R5/R6 只从 ResearchSession 和离线研究集消费快照，不恢复 watchlist。
- 正式数据激活仍受来源授权、shadow trial、live E2E 和模型资格四项生产门禁阻断。
- 验收覆盖 PIT、复算、幂等、删除生命周期、停牌/退市/缺口、无交易建议及一次性 E2E。

以下内容是重定界前的持续行为压力方案，仅作为历史设计档案。

<details>
<summary>历史 R4a/R4b/R4c 持续监测方案（不再活动）</summary>

## 历史用户结果

用户在既有“今日”和“关注”中看到事件与基本面、交易行为两个独立轴。R4A 先用简单基线和影子候选
验证交易结构状态；只有候选晋级后，交易行为轴才用正式 0–100 指数说明拥挤与回撤脆弱性。六维盘面结构把 K 线、MA、成交柱、分时和相对环境翻译成
非数值状态。系统列出驱动、反证、覆盖和数据缺口，但不输出方向概率、预计跌幅或交易建议。

## 非目标

- 不接入真实下单、仓位优化或买卖信号；
- 不把分数解释为概率；
- 不从日线/10 分钟数据声称主力、散户或订单身份；
- 不用 LLM 或训练权重参与基线/候选公式；
- 不在覆盖、稳定性和反例验证前冻结五分面、十簇、等权或五档；
- 不创建 TradingView 式 RSI/MACD/KDJ/MA 买卖投票或第二个技术总分；
- 不建立逐笔、Level-1 或 Level-2 数据库；
- 不绕过 PIT、outbox、SSE、基础设施和正式生产来源门禁；
- 不为路线图完整预建没有调用方的接口或指标。
- 不把隔夜海外市场、地缘事件或跨境影响加入压力公式或 `BehaviorPressureRepositoryPort`；
- 不为多股票、全市场逐股新闻或最多 500 只快照优化前期产品链路。

## 开始 R4a 编码的进入闸门

只有以下条件全部满足才开始 R4a：

1. R1 已完成固定兆易创新、原创合成五类覆盖、多源冲突、证据修订和可下钻简报的合成全栈闭环；
2. R2 已完成 A 股搜索、独立研究会话、PIT 证据报告、七日短期上下文、稳定宏观长期记忆约束和
   事务 outbox；
3. R3 已完成不含压力字段的今日/关注聚合读模型、通用 inbox/SSE、数据健康和确定性降级；
4. `InstrumentMasterDataPort`、`SectorCatalogPort`、`TradingCalendarPort`、`RightsPolicyPort`、
   `ObjectStorePort`、`UnitOfWorkPort` 和 `MessagePublisherPort` 有真实适配器及契约测试；
5. `JobDispatcherPort` 与 `SchedulerPort` 有真实 adapter；每次 firing 会创建新的 `JobRequest`、`as_of`
   和幂等键，不复用静态任务模板；
6. 合成 R4 开发可在零网络 fixture 上开始；第一次真实市场数据运行前，四项生产发布门禁必须通过；
7. Valkey/SeaweedFS 目标基础设施和 PostgreSQL 恢复演练通过。

Declared Port、静态页面、SKIPPED task 或合成 demo 均不算满足进入闸门。

## 真实数据激活闸门

上述条件只允许开始用合成 fixture 编写 R4a。第一次真实 FETCH 或压力计算前，还必须满足：

1. `DERIVE` 已在 RightsOperation、来源配置、检查器、runtime policy、manifest 和审计测试中完整落地；
2. `MarketDataRepositoryPort` 及行情、股本、公司行为、交易规则 revision 支持
   `available_at/revision_known_at <= as_of`；
3. 对应 source capability 已明确启用 `FETCH + RETAIN + DERIVE`，且未过期；
4. 零行 rights-blocked manifest、对象保留/删除和数据库/对象存储恢复测试通过；
5. 同一 fixture 已先在纯函数、repository、任务幂等和 API 降级链路通过。

任一项未通过时连接器保持禁用，只能运行脱敏合成数据。

## 复用与接口检查

复用现有 MarketBar、FeatureObservation、OperationContext、CursorPage、ApiResponse、JobRequest、
MessageEnvelope 及来源/权利/主数据/消息/UoW 能力。新增接口严格限于 planned
`MarketDataRepositoryPort` 和 `BehaviorPressureRepositoryPort`；压力计算器为纯函数。

不复用 forecast Ports/tables、EpisodicMemoryPort 或 market-event Notification 契约。具体缺口与未来
方法签名只引用工程框架，不在本文件复制。

<!-- spec_section_id: R4A -->

## R4a：PIT 日线、简单基线与影子候选

### 实验前置步骤

先审计 PIT 数据覆盖、规则/股本/公司行为可用性和 abstain 原因，再建立只含少量成熟量价/流动性信号
的 `simple_baseline_version`。现有五分面、十贡献簇、同行门槛、历史映射和五档只作为
`candidate_full_formula_version` 影子运行。原 20 个等权原始特征方案不再作为实现依据；候选未通过
promotion gate 时不得作为首页正式主指标。

### 两波失败测试

Promotion 前只为研究制品先写失败测试：

1. Amihud、Corwin–Schultz、NCSKEW、EWMA、MA/ADX/ATR 等原语 golden vectors；
2. simple baseline 的低数据要求、覆盖、abstain 和跨市场阶段稳定性测试；
3. candidate 的参照总体、暂定门槛、候选权重、父子/交互不重复计票及缺失不补零测试；
4. 趋势组织、位置与修复、成交参与、波动环境、相对环境五个盘后诊断维度的状态、支持/反证和独立
   abstain 测试；盘中接受在 R4c 才启用；
5. 主板、创业板、科创板、北交所、ST、IPO、停复牌、涨跌停、股本和公司行为困难集；
6. 两类“高换手＋大涨”条件化解释。

只有 Promotion gate 通过后，才为正式产品边界先写第二波失败测试：

7. Pydantic 契约拒绝概率/买卖字段、跨模式比较、未来数据和不完整可用快照；
8. 两个 planned Port 的 Protocol/adapter contract tests；
9. 新迁移从空库升级、唯一键、双时态和前向修复测试；
10. collection API 的 latest/history/cursor/as-of/degraded，以及必需嵌套 `MarketStructureProfile`、六槽
    availability/state/reason 不变量测试；
11. knowledge-as-of 同时约束 assessment as-of/generated/revision-known、延迟生成、跨 mode/篡改 cursor、
   rights-blocked 零行 manifest 和空页语义测试；
12. mode/终态固定映射、首次基线、公式/分档等 comparison semantics 边界、等待水位和 analog 全部
    子状态测试；
13. explanation version/贡献簇阈值物化、稳定排序、最多 3/2 项及无有效反证时不凑数测试；
14. 双轴关系互斥映射、相邻 assessment 事件窗口、多事件/冲突/LLM 离线及含口径切换层的服务端全序
    测试；
15. 390px、360px、键盘、非颜色状态、“不是概率”及诊断/计分区分测试。

### 最小实现顺序

1. 先以失败测试完成 rights `DERIVE` 的契约、配置、检查脚本与审计兼容扩展；在其通过前不获取数据；
2. 数据覆盖/PIT 可用性审计；
3. `simple_baseline_version` 纯函数与研究输出，不生成正式五档；
4. `candidate_full_formula_version` 影子计算与完整 lineage；
5. 两版本 walk-forward、市场阶段、冗余、leave-one-cluster-out 和固定反例比较；
6. 项目所有者审批 promotion；未批准则保留研究输出，不创建正式首页主指标；
7. 只有晋级后才进入正式 contracts/Port、add-only migration、repository、API/Web 和纵向 E2E。

### 退出条件

- simple baseline 覆盖、abstain 和稳定性报告可重放；
- candidate 覆盖率不明显低于基线，且没有重大市场阶段明显退化；
- 1日/3日最大不利波动排序增量在 walk-forward 中稳定；
- 冗余聚类、相关性和 leave-one-cluster-out 证明十簇不是重复计票；
- 固定反例区分高位拥挤与低位有效突破；
- 所有输入满足 PIT，修订不覆盖旧视图；
- 核心缺失时 `score/band=null` 且原因清楚；
- 同一唯一键重放 100 次只有一个 assessment；
- 用户十秒内识别研究状态、驱动、数据质量和“不是概率”；
- 所有者批准后才冻结 promoted formula 和正式 0–100/分档；否则 R4A 只完成研究基线；
- 当前无 LLM 仍完整可用；
- `make verify` 新鲜通过。

<!-- spec_section_id: R4B -->

## R4b：历史相似情景与 1 日/3 日结果审计

### 前置

R4a 已形成所有者批准的 promoted formula、同行总体和快照 schema；若只有 baseline/candidate 研究结果，
R4B 继续 blocked。

### 先写失败测试

1. 候选不得晚于当前 as-of；
2. 候选规则期、交易板块、相近自由流通市值、公式和相似向量版本必须一致；行业语境由向量表达；
3. 1 日/3 日标签满足 `label_available_at <= current_selection_as_of`；不要求结果在候选 assessment
   当日已经可见；
4. 同证券每 20 个交易日最多一个样本；
5. pgvector 结果必须经应用层精确距离和 PIT 复核；
6. 首版候选固定为十簇加五个盘后诊断坐标，不含盘中接受；analog 专用冗余门禁可删任一候选，但五个
   压力分面各保留代表，最终冻结为 8–14 维且新旧版本不混写；
7. `n < 50` 不输出区间；
8. 原始/行业超额收益、MAE、MFE 的中位数和四分位精确；
9. 页面不存在上涨概率、命中率、目标价或预计跌幅。

### 退出条件

- 最多 200 个去相关样本可完整审计；
- 结果标签、manifest 和选择截止时间可重放；
- walk-forward 使用 purge/embargo，重叠窗口使用 block bootstrap 或 Newey–West；
- 离线评价只报告 MAE 排序、lift、稳定性、覆盖和 abstain；
- `make verify` 新鲜通过。

<!-- spec_section_id: R4C -->

## R4c：10 分钟临时指数、分档变化与站内通知

### 前置

R4B 已通过退出闸门；R4A 的盘后慢变量稳定，R3 通用 SSE、outbox 和站内收件箱真实可用。
因此 R4C 不得与 R4B 并行启动，也不得仅凭 R4A 和 R3 已完成而绕过机器依赖。

### 先写失败测试

1. 证券、行业、市场分别使用各自同钟点总体，只使用过去相同钟点至少 120 日数据；
2. regular/cumulative RVOL、session VWAP 偏离/上方时间/穿越、开盘区间保留、日内回撤/修复和同钟点
   价格冲击均有 fixed-clock golden vectors；
3. 未完成窗口、午休、集合竞价、停牌和临时停市不计算；
4. activity、close-path、volatility、breadth、idiosyncratic 五簇按完整窗口重算；extension 两簇、
   liquidity 两簇和 downside-tail 一簇明确继承当前交易日前最近的兼容 confirmed assessment；
5. 继承来源 ID、版本、截至日和 lineage 可重放，缺失、过期、权利或公司行为/规则不可比时盘中总分
   abstain；
6. 临时与确认模式不跨模式比较；
7. 连续两个窗口维持目标档后才形成 transition；
8. transition/outbox/inbox 使用
   `sha256(event_name + instrument + mode + trading_date + target_band)`；新 job/message ID 也不能让同证券、
   交易日、目标档重复提醒；
9. worker 重启后 streak 和抑制状态仍在；
10. 100 次任务重放只有一个 assessment、transition state 和 outbox；
11. SSE 重连、polite live region、不抢焦点和降级刷新；
12. 盘前、首窗等待、盘中、收盘等待和确认到达复用同一摘要 mode-selection 状态机；当前缺失、
    degraded 或 abstained 不回退旧 ready，临时切确认不形成跨 mode transition。
13. 09:40 后引用冻结外部快照生成
    `CONFIRMED/ABSORBED/DIVERGED/NOT_YET_CLASSIFIABLE`，但不修改快照、压力分或声称因果。

### 退出条件

- 每轮同行基线只计算一次；
- 主动关注标的在数据水位完成后 120 秒内结束；同行基线每窗口只计算一次；
- 缓存命中的 latest API 本机 P95 小于 300ms；
- 升温与降温均可读，提醒不包含方向概率或交易指令；
- PostgreSQL 是提醒状态事实源，Valkey 丢失不重复提醒；
- `make verify` 新鲜通过。

## 失败与降级

| 失败 | 结果 |
|---|---|
| FETCH/RETAIN/DERIVE 被拒 | 失败关闭，显示具体 rights 缺口 |
| 核心规则/股本/分面缺失 | abstain，不显示 0 |
| watermark 过期但仍有可审计旧值 | degraded，显示最后有效时间 |
| 数据库不可用 | API 503，不把缓存伪装成当前事实 |
| analog 样本少于 50 | 主体分数保留，analog 子状态为样本不足 |
| analog 计算/物化失败 | 主体分数保留，显示稳定原因码、失败时间和可重试动作 |
| 部分盘面维度缺失 | profile degraded；仍显示可用维度，不补写缺失 state |
| 所有适用盘面维度不可分类 | profile abstained；压力若完整仍保留 |
| LLM 离线 | 确定性分数、驱动和 lineage 保持可用 |
| SSE 断开 | 显示断开，提供有退避的刷新 |

## 指标与治理

证券级指数和五分面属于领域数据，不作为 Prometheus 证券标签指标。只有真实生产者存在时才登记计算
耗时、结果状态、覆盖/abstain/stale、transition/suppression 和离线 MAE lift 等聚合指标。

每个子阶段按“产品/方法 → 失败测试 → 契约/迁移 → 实现 → 注册表 → 指标生产者 → 复用/锁文件 →
路线图/runbook → 集中蓝图 → make verify”同步。当前规划轮不修改 interfaces/metrics registry。

## 本轮产物

本轮只交付上述规格、指标调研/目录及其产品、方法、架构文档。R4a、R4b、R4c 规格已批准、交付均为
`blocked`，没有运行时能力。

</details>

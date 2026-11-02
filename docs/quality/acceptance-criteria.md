# 工程验收与质量闸门

<!-- r1-product-scope:v2 -->

R1 唯一定义：**固定标的兆易创新（`XSHG:603986` / `603986.SH`）＋原创固定合成数据＋零外网＋
五类覆盖、多源冲突和可下钻简报的完整产品链路**。真实来源授权不阻断 R1/R2 合成开发，而由四项
正式发布门禁单独阻断生产声明。

## 1. 仓库与构建

- [ ] `make doctor` 在五分钟内给出必要工具、锁文件和硬件路径的通过/警告/失败及修复动作；
- [ ] `make demo` 在无网络、无真实数据和无 LLM 时输出可重复的合成盘前更正简报；
- [ ] Python 3.12、Node 22、Docker Compose 在干净机器可重建；
- [ ] Python 和 npm 依赖有锁定文件，容器镜像在发布时使用不可变 digest；
- [ ] `ruff`、`mypy --strict`、ESLint、TypeScript、pytest 全通过；
- [ ] 核心领域行覆盖 ≥ 80%，分支覆盖按模块报告；
- [ ] migration 从空库成功，旧版本备份升级与恢复成功；
- [ ] schema backward compatibility 检查通过；
- [ ] secret scan、依赖漏洞和许可证清单无阻断项。

### 1.1 分布式并行交付

- [ ] `governance/delivery.json` 的 stage DAG 无环、能力所有权不重复、阻塞项和实际状态一致；
- [ ] 所有前后端包共享 contract SHA；远端发布状态不阻断本地开发；
- [ ] package 写范围无父子/同路径重叠，contracts、迁移、Web、runtime/依赖和发布状态均为单写；
- [ ] `registered_stub` 不被当作可运行任务，`declared` Port 不被当作 adapter 已实现；
- [ ] adapter/consumer 只有在共同契约测试存在后才登记成熟度；
- [ ] 生产事件有稳定名称、已发布 payload schema、producer 和 consumer binding；
- [ ] Alembic 图无环且只有一个 head；空库、上一发布版和恢复/前向修复在集成阶段真实验证；
- [ ] 五个 Git worktree 的写槽策略正确：三个后端、一个 FEX、一个 QIR integration；
- [ ] 合并顺序为契约 → 迁移/存储 → provider/领域 → runtime/消费者 → integration/release；
- [ ] 每个包记录 base、contract SHA、精确范围、依赖和聚焦测试；只有 QIR 可以把工作包以
      `--ff` 串行纳入 integration，并在候选通过后以
      `--ff-only` 推进本地 `main`；不得在 `main` 生成第二个 merge commit；
- [ ] 工作包审查后停止写入；普通包不创建 package tag 或要求远端分支；legacy refs
      不改写；
- [x] 远端集成推送的分支、提交和祖先关系具有可回读证据；没有该证据时
      `REMOTE_GIT_HISTORY_NOT_VERIFIED` 必须保持 `pending`；
- [ ] 最终 integration lane 提供跨进程集成/E2E、恢复证据和新鲜 `make verify`，模块单测不能代替。

### 1.2 六部门与轻量 Agent 调用治理

- [ ] 固定六部门 PAE/DMI/BAE/FEX/QMR/QIR，只有产生具体制品的部门建包；
- [ ] PAE、DMI、FEX、QIR 单写边界无双重所有权；
- [ ] 新包具有 base/contract SHA、精确范围、聚焦测试和绑定最终 HEAD 的独立审查；
- [ ] executor 拒绝错误 package/base/contract/identity/worktree；Reviewer 不读取实现日志；
- [ ] Review 目标必须等于最终 branch HEAD，审查后追加提交使结论失效；
- [ ] 同一 package 输入生成相同 capability 和 bundle hash；
- [ ] Reviewer 严格只读、禁网；高风险包异构复核，迁移/授权/破坏性契约/正式发布有人类批准；
- [ ] 消费其他部门 artifact 时存在唯一提供者和显式依赖，不能靠扩大当前 lane 范围替代跨部门拆包；
- [ ] 活动可写 worktree 不超过五个，第六槽负例失败关闭；
- [ ] 工作包不运行 Docker/完整 E2E，QIR integration 才运行 `make integration-check`；
- [ ] FEX 不能修改生成类型或后端；后端部门不能修改 `apps/web` 手写产品代码。

### 1.3 R0P 并行交付硬闸门

状态以 `governance/delivery.json` 为准；下面的复选框只是可读验收，不得脱离机器证据手工宣称完成。

- [x] 主动标的、`PortfolioRepositoryPort 1.1`、两个 R1 task 和四个 task Schema 冻结，并具有共同
      base SHA 与契约 SHA-256；
- [x] 两个 R1 task 拒绝零参数调用并返回 typed `SKIPPED`；四个延期 task 禁止调度并失败关闭；
- [x] 迁移保持单 head、旧迁移字节不变，稳定 event revision、八态 Job ledger、Outbox/技术 Inbox
      结构在真实 PostgreSQL 完成旧 head→新 head 验证；
- [x] Job 从 ledger 恢复、事务 Outbox relay、技术 Inbox consumer、四崩溃点与并发/顺序 100 次重放
      在真实 PostgreSQL 完成；
- [x] 四个真实 worktree 同时启动；销毁任意一个不影响其余三个，并验证 PostgreSQL、Valkey、
      SeaweedFS、任务状态、网络、卷和动态端口隔离；
- [x] CI 已拆分治理、Python、Web、迁移、目标服务集成、runtime smoke、四槽隔离及最终汇总；
      运行 `30066840311` 验证失败传播，运行 `30067750506` 验证正向通过；
- [x] `REMOTE_GIT_HISTORY_NOT_VERIFIED` 已由一次可审计的远端集成推送及提交祖先关系回读转为
      `verified`；
- [x] 最终新鲜 `make verify` 通过；验证提交 `2244a92`，时间
      `2026-07-23T07:09:14Z`。

R0P runtime smoke 只验证 Web/API 健康、worker/scheduler 进程和目标基础设施；R1 的原创零外网
Web→API→scheduler/dispatcher→worker→PostgreSQL/Valkey/对象存储→Web 合成产品 E2E 才是退出证据。

### 1.4 R1 固定目标、研究来源与五类覆盖验收

R1 唯一定义是固定兆易创新（`XSHG:603986` / `603986.SH`）、五类独立研究来源能力、多源冲突和
可下钻简报。以下行为必须有可执行测试与真实证据；规格文本本身不代表产品能力。

| 场景 | Given | When | Then |
|---|---|---|---|
| 首次加载固定目标 | 新实例不存在目标 revision | 启动 R1 | 创建兆易创新 revision 1，只读 GET 可见 |
| 重复加载与 100 次重放 | revision 1 已存在 | 重复启动或重放同一请求 | revision、`activated_at`、任务、通知和 outbox 均不增加 |
| 非固定目标 | 配置、任务或内部调用携带其他证券 | 尝试执行 | 网络 I/O 前返回 `R1_TARGET_LOCKED`，零来源访问和副作用 |
| R1 交互边界 | 固定目标页面已加载 | 检查路由、DOM 与键盘焦点 | 无搜索框、代码输入、添加/激活/删除/切换控件，无 active-target PUT handler |
| 真实来源防线 | 任一真实 connector 被配置或调用 | R1 候选运行 | 权利预检后失败关闭，零真实网络 I/O，合成链路继续 |
| 五类常驻 | 任一类别没有新增事实 | 生成简报 | 法定公告、财务变化、行情快照、公司与行业新闻、相关宏观快讯均存在；该类为 `no_change` |
| 多源同事实 | 两个来源支持同一事实槽位和值 | 归并事件 | 一条稳定事实保留两组 `evidence_id` |
| 多源冲突 | 同槽同期间有不同有效值 | 运行变化识别 | 产生 `CONTRADICTION` 并列证据，不平均、不覆盖、不静默选边 |
| 四类 change | 首次、实质更新、来源更正、同槽冲突 fixture | 与各来源上次成功 cursor 后的 revision 比较 | 只得到 `NEW / UPDATE / CORRECTION / CONTRADICTION`；重复转载不产生第五种变化 |
| 证据与 revision | 已持久化事实和一次后续更正 | 用户下钻 | 到达全部 `evidence_id`、rights snapshot 和不可变 revision；旧值未被覆盖 |
| PIT | 更正晚于历史 `decision_time` 可见 | 查询历史 `decision_time/as_of` | 更正不可见；当前查询仍可审计，不使用未来知识 |
| 双 worker 与 100 次重放 | 两个 worker 读取同一来源 cursor/version | 并发或顺序重放 | 最多一个推进；一份对象、一组事实 revision 和一个逻辑 outbox |
| 三个崩溃窗口 | staging 前、对象写后 DB 前、DB 后 ack 前分别崩溃 | redelivery | 从对应原子边界恢复，不重复证据、revision、outbox 或 cursor |
| 来源故障 | 任一来源返回 403、429、timeout、5xx 或 schema drift | 重试预算耗尽 | 仅该来源失败关闭，保留最后成功和 cursor；不得由另一来源、金十或网页补位 |
| LLM 离线 | 证据与 revision 已持久化 | 模型不可用 | 五类覆盖、事实、四类 change、来源健康和证据下钻仍可用 |
| cursor sentinel | 假 cursor 含唯一 sentinel | 执行成功/失败路径 | HTTP、UI、log、metric、error、trace 与 accessibility tree 均不泄漏 |
| 自动化 UX | 390px、键盘和读屏环境 | 查看固定目标与证据往返 | 无横向滚动，状态不只靠颜色，关闭后焦点返回触发条目 |
| 人工 10 秒理解 | 稳定内容 ready | 用户回答核心内容 | 10 秒内指出固定标的、主要变化、五类缺口、来源状态和研究/回放属性 |

- [x] `FULL_STACK_SYNTHETIC_E2E_VERIFIED`、
      `PIT_CORRECTION_CONFLICT_IDEMPOTENCY_RECOVERY_VERIFIED` 和 `EVIDENCE_DRILLDOWN_VERIFIED`
      已有新治理基线证据。
- [x] `FRESH_MAKE_VERIFY_VERIFIED` 已在候选
      `a040a096aa42ee880da059288b167fa3c8810079` 的新鲜完整门禁中关闭；R1 状态为
      `completed_in_synthetic_environment`。
- [x] 五类合成来源有独立 source/cursor/watermark，页面显著标记“合成测试数据”。
- [ ] 四项 `PRODUCTION_*` 门禁单独绑定真实来源 rights snapshot、shadow trial、live E2E 和模型资格；
      不影响 R1/R2 合成状态，也不得由合成证据关闭。
- [ ] 自动化 E2E 不以墙钟 10 秒判定理解；人工记录至少包含 participant、version、ready time、
      answers、elapsed 和 result。

### 1.5 R1_DEV_DEMO 开发里程碑

- [x] `DEMO_REPLAY` 五类数据完整连通 scheduler、worker、PostgreSQL、briefing、三个只读 GET、Web
      与 evidence/revision 下钻，且相同请求重放不增加有效状态；
- [x] `DEV_LIVE` 仅能在非 production、显式开关和固定 `603986.SH` 下执行一次性 smoke；
- [x] provenance 明确显示 runtime mode、provider、access channel、endpoint、`DEV_ONLY` 与
      `UNVERIFIED`，前端不把 replay URL 渲染成外链；
- [x] 第二次隔离 smoke 至少一个真实开发 capability 成功：法定公告 5 条、公司与行业新闻 8 条；
- [x] 财务/行情上游失败与宏观不具备资格均形成稳定缺口，失败 cursor 保持 v0，不回退 replay；
- [x] 运行时、API、Web 与 evidence 均指向固定 `XSHG:603986 / 603986.SH`，Compose 资源完整清理；
- [ ] `R1-VERTICAL` 正式退出审查；
- [ ] 五类研究来源业务 smoke、最近成功水位与稳定降级均完成。

完成上述前六项只允许标记 `R1_DEV_DEMO=completed`。它不能把 `R1`、`R1-VERTICAL`、
`R7_AUTHORIZED_COVERAGE` 或 `AUTHORIZED_CONNECTORS` 标为完成。

### 1.6 R1_FUNCTIONAL、免费覆盖与公网演示

- [x] 五个固定交易会话依次生成五类终态、独立 cursor、5 份简报、8 个 revision，并在 Day 5 保留
      单来源失败和 `degraded`；
- [x] 相同 Day 5 请求重放后 briefing ID、evidence、revision、cursor 和数据库有效计数不增加；
- [x] `CORRECTION`、`CONTRADICTION`、历史 PIT、失败 cursor 不推进及无 replay fallback 通过；
- [x] provenance 1.2、旧 1.0/1.1 兼容边界、单 head `20260802_0010` 和前端生成类型通过；
- [x] `PUBLIC_STATIC` 快照只含脱敏 replay；7 个静态页面、4 个 evidence 路由、静态 404 与 8/8
      桌面/390px Playwright 验收通过，浏览器没有运行时 API 或第三方请求；
- [x] 新鲜 `make verify` 在产品提交 `1888365ce07f2ab9b8587c360aff46d3a0ad22f9` 通过：
      827 passed / 29 skipped、83.33% coverage、16 项迁移、真实 PostgreSQL、运行时 E2E、Web 和
      四工作区隔离均通过；
- [x] 显式免费 live smoke 产生 10 个可审计终态、至少一个真实成功、0 个 replay run；
- [x] AWS STS 身份可用；完整账户号在部署会话回读，Git 中仅保留脱敏标识 `1328...3855`；
- [x] CloudFormation Change Set 只含 8 个预期 `Add`，无 Modify、Remove 或 Replacement；
- [x] 私有 S3、CloudFront OAC、HTTPS 深层路由、404、安全头和 S3 直连 403 公网 smoke 通过。

公网演示已发布到 <https://dtnor7sll2utv.cloudfront.net>，状态固定为：

```text
R1 = completed_functionally
R1_FUNCTIONAL = completed
R1_FREE_COVERAGE = completed
R1_PUBLIC_DEMO = published
R1_DATA_HARDENING = deferred
R1_AUTHORIZED_COVERAGE = not_started
R1_AUTHORIZED_SHADOW = not_started
COMMERCIAL_READINESS = false
```

研究 live 与 PUBLIC_STATIC 可以关闭 R1–R6 的研究来源功能门，但不能关闭
`R7_AUTHORIZED_COVERAGE`。`R1_FUNCTIONAL=completed` 不代表正式授权、数据硬化或商业就绪。部署证据见
`docs/quality/verification/2026-08-03-r1-public-demo.md`。

### 1.7 R2 指定股票与全球事件深度研究

| 场景 | 必须满足 |
|---|---|
| 搜索 | 代码、简称、全称、大小写/空格、歧义、无结果、非 A 股、停牌和退市均有确定性结果；不自动猜测 |
| 会话创建 | 规范 `instrument_id` + 幂等键返回同一 `session_id/job_id`；不修改 active target/watchlist |
| 事件刷新 | 请求无证券/watchlist/active-target 字段；启动与每 60 分钟刷新；同小时桶与 manifest 重放 100 次不重复，失败不推进 cursor |
| 事件 PIT | 更正追加 revision、冲突并列、未来 revision 不可见；MarketEvent 1.0 读取与 1.1 行业查询兼容 |
| 影响匹配 | 四级直接度、十二渠道、最多一跳；新闻共现、模型常识、匿名图和两跳路径全部拒绝 |
| 进度 | 只显示真实阶段和心跳；无虚假百分比、无半成品报告；三十分钟无进展进入 `stalled` |
| 报告 | 十个固定区块齐全；第五区块含重大/国际事件与宏观暴露；Bull/Bear、未知项、反证、可证伪条件和逐条证据关系完整 |
| 主张 | `fact` 有可读 evidence；`system_inference` 有输入/公式/版本/PIT；`model_hypothesis` 有依据/反证/可证伪条件 |
| 发布硬门 | 无引用事实、模型注入数字、错误单位、未来 revision、同 provider/来源组伪双源、目标价与 Buy/Sell 均阻止发布 |
| 失败 | 事实不足、模型不可用、验证失败分别返回稳定错误；报告接口在终态前返回 `REPORT_NOT_READY` |
| 任务恢复 | 重复 worker、崩溃恢复、取消、检查点重试、outbox 重放和 watchdog 均幂等 |
| 生命周期 | 七日到期、提前删除、重复删除、对象失败补偿；清理后正文不可恢复但 tombstone/hash/证据索引可审计 |
| 事件状态 | “库已刷新但无相关事件”与未刷新/过期/缺权分离；前者可发布，后者形成 CoverageGap 且模型不得补全 |
| 长期记忆 | 个股、当前宏观值、预测和模型结论写入 `macro_reference_facts` 必须在数据库层失败 |
| Web | 390px、键盘、读屏、状态非颜色唯一、合成/非生产标识与报告一次性展示通过 |
| R1 回归 | `XSHG:603986` 与 `R1_TARGET_LOCKED` 不变 |

公共 HTTP、任务、事件、Schema、Port 和前端类型必须与 `governance/interfaces.json` 一致。金额不用
float，时间全部带时区。最终候选只运行一次完整 `make integration-check`。

- [x] 最终独立审查已在功能分支 HEAD
      `0b380d9b29b875779d27894d0634bb69e9aa73fa` 获得 `APPROVED`。
- [x] 唯一新鲜完整 `make integration-check` 已在 integration candidate
      `d33d8852110eb9dc0bc13731a374e6dccd949836` 通过；R2 状态为
      `completed_in_synthetic_environment`，且候选 Docker 资源已清理。
- [ ] 四项 `PRODUCTION_*` 门禁继续阻断生产数据、真实时效、模型资格和商业就绪声明。

## 2. 正式数据连接器

- [ ] 每个启用源有结构完整、有效期可解释的阶段来源记录；R7 再要求正式有效 rights record；
- [ ] R1–R6 实际行为不越过白名单字段、30 天留存、无全文/embedding/训练/再分发；R7 对齐合同能力；
- [ ] 增量游标、条件请求、限流、20% 配额余量和熔断通过测试；
- [ ] 403/429/验证码触发停机，不存在绕过逻辑；
- [ ] 同一 payload 重放不产生重复文档；
- [ ] published/first_seen/fetched/event time 齐全且为 aware timestamp；
- [ ] 数据延迟、缺口、最后成功时间可见。

## 3. 事件、记忆与通知

- [ ] 每个事实有有效 evidence ID 和原文位置；
- [ ] 去重金标至少 1,000 对，precision ≥ 99%；
- [ ] 1% 与 10%、拟与完成、原公告与更正不会误合并；
- [ ] 同事件转载不重复通知，实质更新能通知；
- [ ] 同 idempotency key 重试 100 次只产生一条通知；
- [ ] 3–7 天热窗口结束不删除预测/建议/审计；
- [ ] 历史查询不读取 first_seen 之后或 valid_from 之外的数据；
- [ ] 来源冲突保留并可视化，不被模型静默平均。

## 4. LLM 与聊天

- [ ] JSON Schema 合法率 ≥ 99.5%；
- [ ] 引用 ID 存在率 100%；
- [ ] 数字与证据一致率 ≥ 99%；
- [ ] 证券主体一致率 ≥ 99%；
- [ ] 应拒答样本召回率 ≥ 95%；
- [ ] prompt injection 测试不能改变 system/tool 权限；
- [ ] 本地模型失败时降级为证据列表；
- [ ] 外部模型请求不含账户、数量、成本和个人笔记；
- [ ] 每次生成可按模型/prompt/rule/evidence/hash 重放审计。

## 5. 盘前、政策和主动标的

- [ ] 09:05 简报按上次成功游标只显示变化；
- [ ] R1 只加载兆易创新 revision 1；重复加载无副作用，其他证券在网络 I/O 前返回
      `R1_TARGET_LOCKED`；
- [ ] 搜索、输入、添加、用户激活、删除和切换保持关闭；通用选择契约仅作为后续兼容边界；
- [ ] 公告和政策不因情绪分被删除；
- [ ] 政策事实与个股影响推断分开；
- [ ] 参照证券只参与数值基线，不处理其公司新闻、LLM 分析或通知；
- [ ] 多股票、全市场逐股新闻和板块轮换在前期保持关闭；
- [ ] 所有事实条目显示来源、发布、首次看到和延迟；只有发生事实抽取时显示带限定名的抽取置信度，
      只有系统推断时显示推断不确定性与 AI 标识，禁止通用“置信度”；
- [ ] 数据缺口不被 LLM 填补。

## 6. 统一 R2 全球事件库与按需匹配

- [ ] 全局刷新请求和任务 Schema 不包含 `instrument_id`、watchlist 或 active-target revision；
- [ ] 合成环境启动时刷新一次、此后每 60 分钟刷新，100 次同桶同 manifest 重放不产生重复 revision；
- [ ] 更正追加 revision、冲突并列保留，写入失败时 source cursor 和成功回执均不推进；
- [ ] 会话只读取 `revision_known_at <= as_of` 的事件；未来修订被拒绝；
- [ ] 四级直接度和十二影响渠道契约冻结；两跳以上、新闻共现和模型常识均不能生成暴露边；
- [ ] 无公司暴露时只能降级为行业背景；事件事实为 `fact`，个股条件化影响为带规则输入的
      `system_inference`；Bull/Bear 保持 `model_hypothesis`；
- [ ] `ready_no_matches` 与 `unavailable/stale/rights_blocked` 是不同状态；后者生成明确 CoverageGap，
      模型不得补全；
- [ ] 页面显示事件时间、revision、直接度、渠道、支持/反证、来源和缺口，不显示隔夜六块、08:55、
      09:05、09:40、关注、通知或 SSE；
- [ ] 会话删除后影响匹配、模型上下文和报告不可恢复；全球事件事实、revision、证据索引与最小审计仍可验证；
- [ ] `macro_reference_facts` 拒绝当前事件、个股暴露和影响结论；生产网络路径继续由四项发布门禁失败关闭。

<details>
<summary>历史隔夜外部环境验收方案（不再活动）</summary>

- [ ] 窗口固定为上一 A 股完整交易日 15:00 至当前交易日 08:55，09:05 快照冻结；
- [ ] 夏令时、半日市、临时休市和不同交易所假期由版本化会话计算，不写死北京时间；
- [ ] 海外休市显示 `STALE_MARKET`，不能把前值构造成零变化；A 股长假使用独立基线并记录海外交易日；
- [ ] 周末重大事件在相关市场交易前显示 `UNPRICED_EVENT`；
- [ ] 每序列使用过去 504 个可比同会话窗口，少于 252 个时不计算异常位置；
- [ ] 股票、利率、汇率、商品和物流序列不做统一横截面排名，缺失核心块时为 `INSUFFICIENT`；
- [ ] 顶层只有 `NORMAL/LOCALIZED_SHOCK/BROAD_REPRICING/MIXED_REPRICING/INSUFFICIENT`，不生成外部总分；
- [ ] 官方事实、目标影响推断和 09:40 开盘反馈分别 revision；08:55 后修订不能改变原 09:05 快照；
- [ ] 只允许官方直接点名、公司披露、一跳证据或行业背景；两跳和新闻共现关系被拒绝；
- [ ] 俄乌能源样例能区分生产者、净采购者、航空、化肥和钯下游；原油冲击来源不同保留不同路径；
- [ ] 半导体管制同时保留设备限制、国产替代、下游扩产、补贴和许可等支持/反证；
- [ ] 关键材料与贵金属分组不同；没有公司暴露时只显示行业背景；
- [ ] 来源权威度、抽取质量、暴露直接度、推断不确定性和市场反馈分开，禁止单一总 `confidence`；
- [ ] 09:40 后只输出 `CONFIRMED/ABSORBED/DIVERGED/NOT_YET_CLASSIFIABLE`，不重算盘前快照或证明因果；
- [ ] walk-forward 分别报告正常、贸易冲突、疫情、战争、长假、流动性压力和 DST，并与无外部信息及
      仅美国宽基两个基准比较；
- [ ] 评价开盘绝对缺口、首30分钟波幅和最大不利波动的排序增量、覆盖、abstain、稳定性和提醒负担；
      不使用方向准确率、胜率、Brier 或概率校准；
- [ ] R1–R6 来源未进入明确 owner allowlist 或尝试越过白名单字段时失败关闭；R7 再对
      `FETCH/RETAIN/DERIVE` 及展示/模型用途正式授权逐项失败关闭；
- [ ] `Briefing 1.0` 保留；必需外部对象未来使用 `Briefing 2.0`，`MarketEvent 1.0` 仍只承载事实；
- [ ] LLM 离线仍能生成确定性状态、事实列表、传导路径、支持/反证和缺口。

</details>

本节当前事实源为 [`r2-multi-source-memory.md`](../roadmap/workstreams/r2-multi-source-memory.md) 与
[`external-context-framework.md`](../architecture/external-context-framework.md)。

## 7. 统一 R4 研究时 PIT 市场结构快照（planned）

- [ ] 每个 `ResearchSession` 最多计算一次并绑定同一 `as_of`、数据 manifest、公式和规则版本；
- [ ] 只读取当时可见的行情/规则/股本 revision，核心输入缺失时 abstain，不补零或由模型补全；
- [ ] 不读取 `active_target/watchlist`，不启动未来证券任务、十分钟监控、档位提醒、09:40 反馈、通知或 SSE；
- [ ] 快照是报告内的强类型系统推断，不是实时仪表盘、概率预测、目标价或 Buy/Sell；
- [ ] 会话删除后快照正文和短期制品按七日生命周期清除，最小审计与证据索引仍可验证。

<details>
<summary>历史持续交易结构压力验收方案（不再活动）</summary>

- [ ] 分数只描述相对结构压力，contract 和页面不存在概率、预计收益或买卖字段；
- [ ] 先完成 PIT 数据覆盖与 abstain 审计；
- [ ] `simple_baseline_version` 只用少量成熟信号，产生研究分但不产生正式五档；
- [ ] `candidate_full_formula_version` 作为影子候选，不在晋级前进入首页主指标；
- [ ] Amihud、Corwin–Schultz、NCSKEW、EWMA 及 TA-Lib 技术原语有论文/官方复核、golden vectors 和性质测试；
- [ ] 证券/行业/市场的日线与同钟点盘中六类连续总体及离散规则总体固定；自身 120 日、证券同行 30 只、
      跨行业 20 个、参考状态 1,000 个 instrument-day 门槛通过覆盖分析；
- [ ] 证券同行排除自身，按同板块/规则、有效权利/观测和对数自由流通市值最近 100 只确定；30–100 只
      全用，盘中要求同钟点完整窗口，少于 30 abstain，不再使用板块∩行业∩市值五分位；
- [ ] 候选五分面/十贡献簇暂定等权，一个底层指标只属于一个核心簇，父子/交互不重复投票；
- [ ] 候选覆盖不明显低于基线、抖动不劣于基线、walk-forward MAE 排序增量稳定、重大阶段无明显退化；
- [ ] 相关性、聚类和 leave-one-cluster-out 证明十簇无重复计票，固定反例区分高位拥挤和低位突破；
- [ ] 项目所有者批准后才冻结 promoted formula、0–100 与正式分档；
- [ ] 任一核心簇缺失时 `score/band=null`，不补零、重分配或用诊断指标替代；
- [ ] 指标父子图、滚动 Spearman、聚类、VIF/条件数和 leave-one-cluster-out 审查通过，角色随
      `feature_set_version` 冻结；
- [ ] `available_at/revision_known_at > as_of` 的行情、股本、公司行为、规则和行业数据全部被拒绝；
- [ ] assessment 满足 `window_end <= as_of <= generated_at <= revision_known_at`，knowledge-as-of 同时过滤
      三个 assessment 时间；延迟计算和后来更正不会提前出现在历史查询；
- [ ] 主板、创业板、科创板、北交所、ST、IPO、停复牌、涨跌停、股本和除权困难集通过；
- [ ] 两类“高换手＋大涨”样例得到不同驱动/反证，且均不推断交易者身份；
- [ ] 盘中与盘后公式、序列、比较和视觉完全分离；午休、停牌和未完成窗口不生成临时值；
- [ ] `post_close/CONFIRMED` 与 `intraday_10m/PROVISIONAL` 是唯一合法映射；API、唯一键和比较均使用
      `PressureMode` 值，不使用 `CONFIRMED/PROVISIONAL` 作为模式键；
- [ ] 首次基线、公式/规则/分档/同行口径版本变化时 delta 为空、图表断线且不触发分档事件；分档阈值
      变化提升 `band_semantics_version`、comparison hash 和 umbrella formula version；
- [ ] 双轴关系 contract、相邻 assessment 对齐的事件边际窗口、依据引用和互斥映射覆盖多事件、冲突、
      无变化及 LLM 离线；累计事件列表不得被误作同窗因果；
- [ ] 历史相似候选通过精确距离和 PIT 复核，同证券 20 交易日去相关，样本少于 50 不输出区间；
- [ ] R4b 相似候选只含十簇与五个盘后诊断坐标；冗余门禁后冻结为 8–14 维并记录
      `analog_feature_set_version`，盘中接受不得混入首版，且不得预建或混写旧 20 维；
- [ ] 1 日/3 日原始收益、行业超额、MAE、MFE 的分布精确，不展示上涨概率、命中率或目标价；
- [ ] 同一任务重放 100 次只有一个 assessment、一个目标档状态和一条 outbox；
- [ ] worker 重启后连续窗口与提醒抑制状态可恢复；
- [ ] 全市场同行基线每窗口只计算一次，主动关注标的在水位完成后 120 秒内结束；
- [ ] 最新快照缓存命中接口本机 P95 小于 300ms，Prometheus 标签不含证券代码；
- [ ] 390px/360px、键盘、非颜色状态、LLM 离线和 SSE 断开路径通过；
- [ ] 每个 snapshot 必带六维 profile；每槽具有 `ready/degraded/abstained/not_applicable`、可空 state、
      稳定 reason、支持、反证、quality 和截至时间，盘后盘中接受为 not_applicable；诊断变化不改变压力
      分或触发分档事件；
- [ ] 盘中固定重算 activity、close-path、volatility、breadth、idiosyncratic 五簇，固定继承 extension 两簇、
      liquidity 两簇和 downside-tail 一簇；继承 lineage 缺失或不可比时 abstain；
- [ ] 同钟点 regular/cumulative RVOL、VWAP 偏离/上方时间/穿越、开盘区间保留、日内回撤/修复和价格
      冲击使用完整窗口，有 fixed-clock golden vectors；
- [ ] 首屏只有一个数值总分，不展示技术 Buy/Sell；K 线/分时最多 2–4 个相关批注且前端不重算指标；
- [ ] 今日/关注摘要包含口径切换/不可比较状态；盘前、首窗等待、盘中、收盘等待和确认到达
      使用同一服务端摘要状态机，缺失/degraded/abstained 不被旧 ready 值掩盖，SSE 不另选 mode；
- [ ] 尚无评估、等待水位、首次基线、口径切换、相似结果 pending/不足/不可比/失败及退市状态均有
      确定文案；analog 失败保留主体分数并显示稳定原因码与重试；
- [ ] 驱动/缓解因素由领域层按贡献簇和版本化阈值排序，最多 3/2 项；底层原语不伪装成多项独立理由；
- [ ] assessment 保存 comparison semantics hash、explanation version/threshold 和已选驱动/缓解项，历史
      解释不随当前代码漂移；
- [ ] 开始 R4a 合成编码的进入闸门以 `docs/roadmap/workstreams/r4-behavior-pressure.md` 为唯一清单：
      R1–R3 真实双时态主数据、RightsPolicy/ObjectStore/UoW/MessagePublisher adapter、事务 outbox/relay、
      通用 inbox/SSE、JobDispatcher/Scheduler、目标基础设施/恢复及外部合同评审均通过；declared Port、
      SKIPPED task 或合成 demo 不能代替；
- [ ] 第一次真实获取/计算的激活闸门单独验收：`DERIVE` 全链、行情/股本/公司行为/交易规则 revision、
      PIT repository、启用的 source capability、零行阻断 manifest 和恢复测试全部通过。
- [ ] 与原始 K 线/分时/MA/成交柱的成对任务记录正确率、完成时间和十秒复述率；同时报告压力概率误读、
      诊断计分误读和 stale/abstained 有效性误读，不预设改善比例。
- [ ] 海外市场和跨境事件没有进入压力五分面、十贡献簇、比较总体或分档语义；只可作为经过验证的
      支持/反证和历史情景筛选条件。

</details>

本节是统一 R4 的未来验收清单；当前没有实现或指标生产者。

## 8. 量化与融合

- [ ] 训练/验证/校准/测试按时间分离并有 5 日 embargo；
- [ ] 同事件簇、重叠标签和修订不跨 split；
- [ ] 成分、退市、行业、财报、复权和规则均 point-in-time；
- [ ] 文本特征和两个基础模型的融合输入是 OOF；
- [ ] Brier、log loss、ECE、coverage、Rank IC、费用后表现和回撤齐全；
- [ ] 融合相对单模的改善有 block bootstrap 证据；
- [ ] ECE ≤ 0.05，q10–q90 coverage 75%–85%；
- [ ] 最新 6 个月保留集只开启一次；
- [ ] 60 个交易日冻结纸面运行完成；
- [ ] 未过闸门时预测 feature flag 保持关闭。
- [ ] 概率评价与交易结构压力评价严格分区；Brier/ECE 不用于评价压力指数。

## 9. 安全与隐私

- [ ] 持仓数量和成本在数据库/备份中加密；
- [ ] 密钥来自系统密钥链/secret store，不进 Git、日志、前端或 prompt；
- [ ] 所有日志经过字段级脱敏；
- [ ] HTML/PDF/JSON 作为不可信输入处理；
- [ ] SSRF、路径穿越、恶意 PDF/压缩包、超大文档和 DoS 测试通过；
- [ ] Web 有 session、CSRF、CORS、CSP、速率限制和安全 header；
- [ ] 备份加密且完成恢复演练；
- [ ] 真实下单能力在配置和代码两层不存在。

## 10. 运维 SLO

- [ ] API 月度本地可用性目标 99.5%；
- [ ] 任务成功率、队列延迟、源延迟、GPU OOM、引用失败有告警；
- [ ] 公告与公司资讯目标延迟 60 秒；各 capability 独立展示实际水位和上游延迟；
- [ ] RPO 24 小时、RTO 4 小时，并以恢复演练验证；
- [ ] PostgreSQL/SeaweedFS 70% 容量告警；
- [ ] runbook 覆盖源熔断、队列堆积、DB/SeaweedFS/Valkey/GPU 故障和 adapter 回滚。

## 11. 当前交付轮验收

R0P 已完成。WP01–WP09 旧执行历史只读兼容；未启动包使用六部门轻量协议。远端同步只在阶段里程碑
执行，不阻断本地 `locally_verified`。

历史 replay、live smoke 与 PUBLIC_STATIC 只作只读记录。当前 R1/R2 候选使用原创合成数据并以新治理
基线上的全栈 E2E、生命周期/恢复测试和新鲜 `make verify` 验收。真实来源授权、shadow trial、live E2E
与模型运行资格仍为正式发布阻断，不能由历史 smoke、合成报告或示例页面关闭。

六部门责任域已用于 R1 第一波，后续波次继续以
[`implementation-plan.md`](../roadmap/implementation-plan.md) 和机器 package 状态为准；只有产生真实
制品的部门才创建短包，不恢复九部门矩阵或为纯说明建立工作包。

统一 R2 的搜索、全球事件刷新/匹配、研究会话和报告能力只以 `governance/interfaces.json` 与运行测试
为准；不得由文档中的历史计数推断成熟度。R3 会话问答和 R4 单次 PIT 市场结构快照仍属于规划；
今日页、inbox、SSE、通知、持续个股监控和旧阶段拆分只作历史说明。阶段/心跳是状态，没有新增统一
量化指标，因此 `governance/metrics.json` 不变。

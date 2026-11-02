# R1：固定标的五类研究来源盘前闭环实施规格

状态：`pending_fresh_synthetic_reverification`。机器状态、阻塞码和哈希以
`governance/delivery.json` 为准；历史 live/public demo 记录不再作为 R1 出口证据。

## 1. 唯一产品范围

<!-- r1-product-scope:v2 -->

R1 唯一定义：**固定标的兆易创新（`XSHG:603986` / `603986.SH`）＋原创固定合成数据＋零外网＋
五类覆盖、多源冲突和可下钻简报的完整产品链路**。

唯一流程：

```text
加载固定目标 XSHG:603986
→ original synthetic source profile
→ 来源独立游标、不可变证据和 PIT 时间
→ 事实槽位、去重与 revision
→ NEW / UPDATE / CORRECTION / CONTRADICTION
→ 五类独立覆盖状态
→ 盘前差异简报
→ evidence_id 与历史 revision 下钻
```

R1 不提供证券搜索、代码输入、添加、用户激活、删除或切换。长期 `ActiveTarget` / `Watchlist`
选择能力及现有通用读写 Schema 保留为后续兼容边界，但不属于 `R1-VERTICAL` 或
`R1_RESEARCH_SOURCE_COVERAGE`。

## 2. R1 出口门禁

旧 manifest、seal、review、历史集成结果和公网演示只保留为历史记录；控制哈希改变后不能直接作为新
出口证据。R1 候选必须在新治理基线上重新生成受影响工作包，并一次性证明：

- 原创固定合成数据，全程零外网；
- Web → API → dispatcher → worker → PostgreSQL/Valkey/对象存储 → Web 完整贯通；
- PIT、更正、冲突、幂等、失败恢复和证据下钻；
- 页面显著显示“合成测试数据”；
- 最终候选 SHA 上新鲜 `make verify` 成功。

当前机器状态为：

```text
R1 = pending_fresh_synthetic_reverification
R1_FIXED_TARGET = XSHG:603986
R1_PUBLIC_DEMO = historical_non_production
PRODUCTION_SOURCE_AUTHORIZATION = blocked
PRODUCTION_SHADOW_TRIAL = blocked
PRODUCTION_LIVE_E2E = blocked
PRODUCTION_MODEL_RUNTIME = blocked
COMMERCIAL_READINESS = false
```

合成出口和正式发布必须分开表达：

| 闸门 | 必须证明 | 明确不证明 |
|---|---|---|
| `FULL_STACK_SYNTHETIC_E2E_VERIFIED` | 固定目标合成全栈链路 | 真实来源、真实时效或生产就绪 |
| `PIT_CORRECTION_CONFLICT_IDEMPOTENCY_RECOVERY_VERIFIED` | 关键领域与恢复行为 | 供应商可用性 |
| `EVIDENCE_DRILLDOWN_VERIFIED` | 合成事实可读取证据和 revision | 真实来源授权 |
| 四项 `PRODUCTION_*` 门禁 | 授权、shadow trial、live E2E 和模型资格 | 不参与 R1 出口 |

真实来源权利预检和失败关闭代码继续保留，但任何未授权网络调用都被禁止。

## 3. 执行控制面

R1 历史 execution `r1-premarket-20260725` 与 `r1-managed-wave1-20260727` 只读保留。当前新包统一使用
六部门轻量协议：`agent-run → package-check → agent-review → QIR 优先快进集成`。当前候选已把
固定目标、五会话 replay、受控免费 live、PIT/revision、简报持久化、三个只读 GET、动态 Web 与
PUBLIC_STATIC 下钻连成一条历史本地链路；当前出口重新绑定原创合成全栈候选。

当前 lane 拓扑保持不变，但产品职责收敛如下：

| Lane | 单一结果 | 直接依赖 |
|---|---|---|
| `wp01-product-spec` | 本规格、体验、来源和验收事实源 | 无 |
| `wp02-contract-freeze` | 只读 active-target、briefing、evidence 契约及多来源 fixture | WP01 |
| `wp03-migration-schema` | 追加式迁移、PIT、Job/Outbox 一致性；Active Target 子集已落地 `0007` | WP02 |
| `wp04-persistence-storage` | Active Target、来源 capability、证据/revision、简报与 provenance 存储 | WP02、WP03 |
| `wp05-source-rights` | 合成零网络出口与正式生产 rights 失败关闭边界 | WP02 |
| `wp05b-source-dependency-registry` | 合成 fixture 和正式来源候选的复用登记 | WP01、WP05 |
| `wp05a-provider-adapters` | 原创确定性五类 fixture；真实 adapter 保持禁用 | WP02、WP05、WP05b |
| `wp06-event-revision` | 事实槽位、去重及四种变化 | WP02、WP05、WP05a |
| `wp07-active-target` | 固定目标初始化与只读策略 | WP02 |
| `wp08-briefing-flow` | 确定性五区块 assembler、持久化与 latest/evidence 查询已落地 | WP02、WP07 |
| `wp09-api-web-today` | 三个只读 HTTP 资源、provenance 协商与固定目标今日/证据页已落地 | WP01、WP02 |
| `wp10-runtime-composition` | 合成 scheduler/worker 链路；真实连接器失败关闭 | WP01–WP09 必要产物 |
| `wp11-platform-e2e` | 零外网合成全栈产品 E2E、PIT、恢复与证据下钻 | WP01–WP10 |
| `wp12-integration-release` | 串行 candidate、CI、owner-approved main/tag | 已验证包 |

## 4. 固定目标不变量

- 固定身份为 `instrument_id=XSHG:603986`、`symbol=603986.SH`、`name=兆易创新`。
- 首次启动建立 revision 1；重复加载是幂等 no-op，revision、`activated_at` 和副作用均不增加。
- 配置、任务 payload 或内部调用出现其他证券时，在网络 I/O 前返回 `R1_TARGET_LOCKED`。
- WP07 不实现切换 revision、旧任务停止或用户选择流程。
- R1 公共 HTTP 只包括：
  - `GET /api/v1/active-target`
  - `GET /api/v1/briefings/premarket/latest`
  - `GET /api/v1/events/{event_id}/evidence`
- `PUT /api/v1/active-target` 不登记、不实现；选择与 mutation Schema 仅作为未来兼容契约保留。
- 已冻结 bundle 中历史遗留的 `R1_HTTP_CONTRACT_ROUTES` PUT 候选只读保留，不是接口注册、handler
  授权或 R1 验收入口。执行范围只认 `governance/interfaces.json` 的成熟度。
- AST、UX 与 RTP 的 R1 工作包必须把搜索、输入、添加、用户激活、删除和切换列为 `non_goals`
  与禁止能力。

## 5. 多来源与五类覆盖

R1–R6 当前能力来源恰好拆为：

1. `research_preview_announcements`；
2. `research_preview_financials`；
3. `research_preview_market`；
4. `research_preview_company_news`；
5. `research_preview_macro`。

五项不互为 fallback。每个 capability 分别维护 cursor、lease、最后成功时间、业务 watermark、批次审计
和故障状态。凭据缺失、接口权限不足、过期、403、429、timeout、schema drift 或部分失败时，只降级
对应类别；不得调用未登记网页、搜索缓存或其他供应商静默补位。

五类区块始终存在：

1. 法定公告；
2. 财务变化；
3. 行情快照；
4. 公司与行业新闻；
5. 相关宏观快讯。

每类独立返回 `covered`、`no_change`、`degraded` 或 `rights_blocked` 等稳定覆盖状态。“未发现新增”
不是失败，“来源成功但无该 capability”也不能冒充已覆盖。

## 6. 证据、变化与 PIT

- 用户可见变化恰好为 `NEW / UPDATE / CORRECTION / CONTRADICTION`；重复 payload 或转载只计抑制数。
- 同一事实的多源证据合并为稳定逻辑事件并保留全部 `evidence_id`。
- 同槽同期间的不同有效值生成 `CONTRADICTION`，并列展示证据，不平均、不覆盖、不静默选边。
- 来源明确 replacement/retraction 才生成 `CORRECTION`；未声明替换的同源冲突仍是
  `CONTRADICTION`。
- 每个 revision 保存 `published_at`、`first_seen_at`、`fetched_at`、`revision_known_at`、
  checksum、rights snapshot 和来源记录身份。
- 历史查询同时要求 `first_seen_at <= decision_time` 与
  `revision_known_at <= decision_time`，不能看见未来修订。
- 来源 cursor 是 adapter 的不透明状态；只有整批对象、证据、revision、cursor 与 outbox 原子成功
  才推进。UI、HTTP、日志、指标、错误、trace 与无障碍树均不得泄漏 cursor。
- 对象 staging 前、对象写后 DB commit 前、DB commit 后 ack 前三个崩溃窗口都必须幂等恢复。

## 7. Given / When / Then 验收

| 场景 | Given | When | Then |
|---|---|---|---|
| 首次加载固定目标 | 新实例无目标 revision | 启动 R1 | 创建兆易创新 revision 1，只读 GET 可见 |
| 重复加载 | revision 1 已存在 | 启动或任务重放 100 次 | revision、任务、通知和 outbox 均不增加 |
| 非固定目标 | 任意入口携带其他证券 | 尝试执行 | 网络 I/O 前返回 `R1_TARGET_LOCKED`，零来源访问 |
| 分项来源失败 | 任一研究 capability 凭据缺失、权限不足或不可用 | 运行盘前摄取 | 只降级该类别；不调用隐式 fallback，不推进该游标 |
| 五类常驻 | 某类无新增内容 | 生成简报 | 区块仍存在并显示 `no_change`，不显示为空白或失败 |
| 多源同事实 | 两个来源支持同一事实槽位和值 | 归并事件 | 一条稳定事实，保留两组 `evidence_id` |
| 多源冲突 | 同槽同期间存在不同有效值 | 识别变化 | 产生 `CONTRADICTION`，并列证据，不平均或覆盖 |
| 更正 | 来源明确撤回/替换旧事实 | 摄取新 revision | 产生 `CORRECTION`，旧证据和旧简报仍可审计 |
| PIT | 更正在历史 `decision_time` 后可见 | 查询历史简报 | 历史视图看不到未来 revision |
| 来源重放 | 两个 worker 竞争同一 cursor/version | 同批提交 | 最多一个推进，100 次重放只有一个业务结果 |
| LLM 离线 | 证据已持久化 | 模型不可用 | 五类覆盖、事实、变化和证据下钻仍可用 |
| 页面边界 | 390px/键盘/读屏环境 | 查看固定目标及证据 | 无搜索/输入/切换控件；状态不只靠颜色；焦点可返回 |

## 8. 非目标与失败行为

- 不做证券搜索、代码输入、添加、用户激活、删除、切换、多主动标的或持仓录入。
- 不做隐式来源补位、来源排名投票、LLM 事实补写、LoRA、盘中板块轮动或方向预测。
- 不要求用户填写持仓数量或成本；参照证券不抓取公司新闻或产生通知。
- R1 候选只允许原创合成数据和零外网；正式 `AUTHORIZED` 模式必须取得逐 capability 批准并通过
  四项生产门禁。所有阶段都不得绕过 robots、登录、验证码、限流、私有签名或访问控制。
- R1 本地开发可达到 `locally_verified`；候选通过完整本地门禁且项目所有者明确授权时，本地 `main`
  可 `--ff-only` 前进为未发布里程碑。远端 CI/验证 ref 未通过前不得推送远端 `main` 或创建正式阶段标签。

## 9. TDD 实施顺序

1. 固定目标 revision 1、重复 no-op、非固定目标 I/O 前失败测试；
2. 五类研究 capability 的凭据缺失、权限不足、过期、403、429、timeout 与独立 cursor 测试；
3. 五类覆盖的常驻、`no_change`、`degraded`、`rights_blocked` 测试；
4. 多源事实合并、冲突、更正、重复抑制和 PIT 测试；
5. 三个崩溃窗口、lease/CAS 与 100 次重放测试；
6. 确定性简报、三个只读 HTTP 资源及无 PUT 路由测试；
7. 固定目标今日页、键盘、390px、非颜色状态与证据下钻测试；
8. Web → API → scheduler/dispatcher → worker → PostgreSQL/Valkey/对象存储 → Web 合成产品 E2E；
   正式发布另跑授权来源 live E2E。

## 10. 标准同步

R1_FUNCTIONAL 没有新增 Port、HTTP、任务、事件或指标；兼容契约新增 provenance 1.2，追加迁移已推进到
单 head `20260802_0010`，只扩展现有 JSONB provenance 校验并保持全部已发布迁移不改。当前机器注册为
6 个 implemented HTTP、2 个 implemented task、4 个延期 stub、25 个 published Schema、59 个 declared
Port、0 个生产领域事件和 0 个统一指标。任何后续接口变更都必须先查注册表并同步生产者、消费者、
生成 Schema、迁移、文档和测试。
R1 的交付机器事实源是 `governance/delivery.json.current_stage` 与 `product_checkpoints`；公共接口仍只认
`governance/interfaces.json`。集中蓝图只提供导航和快照，不成为第二事实源。

# 数据源、授权与合规策略

核验日期：2026-08-31

<!-- r1-product-scope:v2 -->

R1 唯一定义：**固定标的兆易创新（`XSHG:603986` / `603986.SH`）＋原创固定合成数据＋零外网＋
五类覆盖、多源冲突和可下钻简报**。统一 R2 的研究事实仍使用原创合成证据与全球事件集；证券身份搜索
改用固定、离线、`DEV_ONLY/UNVERIFIED` 的交易所目录快照。该快照不构成真实研究数据或生产来源完成。

## 1. 当前数据主线与生产边界

R1/R2 候选的唯一数据主线是：

**运行时零网络的固定 A 股身份目录，加上仓库自有、确定性、可重放的合成研究证据和
stock-agnostic 全球事件集。**

A 股身份目录 `ashare-directory.dev.v1.1.20260831` 只在人工显式命令中通过已锁定的 AKShare 交易所
adapter 刷新，保存 5,884 条规范身份及最小来源回执。生成器先校验三家交易所官方法律声明对应的私有
非商业 `FETCH + RETAIN` 记录，并固化权利快照哈希、真实抓取时间和保留截止时间。普通搜索、测试、API
和 worker 只读仓库快照，且每次入口调用都检查保留截止时间，过期后以
`INSTRUMENT_CATALOG_EXPIRED` 失败关闭；
上交所公司全称可搜索，深交所/北交所缺少已核验全称时只显示证券简称并提示研究阶段核验。目录不得
进入 embedding、训练或长期语义记忆，也不得被解释为实时停牌状态或生产授权。

5,884 是三家交易所当前与沪深终止上市目录去重后的规范身份数，不是搜索词数量。旧开发快照曾把
`000022`、`000043`、`300114`、`600849` 四个换码存续证券误算为独立退市身份；当前目录依据交易所
公告把它们分别作为 `001872`、`001914`、`302132`、`601607` 的历史代码和名称别名，因此旧代码仍可
搜索，但不会重复创建研究对象。四份公告分别绑定深交所/上交所独立来源登记，生成器在任何请求前校验
公告域名、`FETCH + RETAIN`、非商业用途和保留期；快照只保存确定性 `evidence_id`、真实抓取时间、
公告内容哈希、权利快照哈希和 URL，不保存公告正文。

五类能力拆成稳定 `source_id`、revision、PIT 时间、成功水位与失败状态。所有页面、报告和制品必须
标记“合成测试数据/非生产数据”。测试与候选运行不得读取凭据、真实账户、网页或供应商网络。

除上述开发目录快照和已登记的受控模型辅助网页补全外，CNINFO、Choice、Tushare-compatible 服务、
其他交易所/政府数据能力和其他真实来源只保留为正式发布候选。
运行时代码必须先做权利预检并失败关闭，不得以搜索、网页缓存、回放或另一来源静默补位。

正式发布同时受四项机器阻断：`PRODUCTION_SOURCE_AUTHORIZATION_NOT_VERIFIED`、
`PRODUCTION_SHADOW_TRIAL_NOT_COMPLETED`、`PRODUCTION_LIVE_E2E_NOT_VERIFIED` 和
`PRODUCTION_MODEL_RUNTIME_NOT_QUALIFIED`。任何一项未通过时不得移除非生产标识、启用真实来源或
宣称及时真实分析。

隔夜外部环境不建立第二条隐式主线。未进入白名单的供应商、私有网页接口和搜索缓存不得静默补位。

### 1.1 R1 合成五类来源能力冻结

R1 的五类合成能力冻结如下：

| 类别 | capability | 合成来源角色 | R1 用途 |
|---|---|---|---|
| 公告 | `statutory.announcements` | `synthetic_disclosure_primary` | 标题、日期、证券和证据链接 |
| 财务 | `financial.changes` | `synthetic_financial_primary` | 合成财务摘要变化 |
| 行情 | `market.snapshot` | `synthetic_market_primary` | 固定标的合成 OHLCV |
| 公司与行业 | `company_industry.news` | `synthetic_company_news` | 合成标题与冲突 fixture |
| 宏观 | `macro.flashes` | `synthetic_macro_reference` | 合成宏观暴露 fixture |

- 三个证券端点在网络访问前固定校验 `603986.SH`；资讯端点只保留与“兆易创新 / 603986 /
  GigaDevice”相关的标题；政策端点固定科技类别。
- 任一 capability 失败只降级对应类别，不推进其 cursor，不用 replay、另一类别或旧水位冒充成功。
- 两个账户均按项目自设 30 次/分钟上限实行跨进程共享限流，并使用有界超时、一次额外重试和 2 MB/2,000 行
  响应上限。403、429、凭据失败、接口权限不足和 schema drift 都映射为稳定原因码。
- 正式模式必须对 `FETCH / RETAIN / DERIVE / DISPLAY / SUMMARY / AI_USE / REDISTRIBUTE` 逐项持有
  有效 rights snapshot，并完成独立来源 shadow trial 和 live E2E。
- 所有阶段都禁止绕过登录、验证码、robots、限流、私有签名或访问控制；密钥只经本机安全存储或
  Secrets Manager 注入，不进入 Git、浏览器、日志或公开快照。
- ProMax 教程列出的 `stk_auction_o`、`stk_auction_c`、`stk_auction` 只登记为候选能力；当前算法明确
  排除集合竞价时段，未升版前不得把这些数据混入 10 分钟压力指数。

### 1.2 历史研究预览记录（非当前出口策略）

下表只解释历史制品，不授权当前候选启用真实网络：

| 模式 | 数据与用途 | 发布层级 / rights | 能否关闭正式闸门 |
|---|---|---|---|
| `DEMO_REPLAY` | 脱敏、确定性五会话 replay；不访问网络 | `DEV_ONLY / NOT_APPLICABLE` | 否 |
| `DEV_LIVE` | 历史固定 `603986.SH` 预览 | `DEV_ONLY / UNVERIFIED` | 否；只作历史诊断 |
| `PUBLIC_STATIC` | 从已验证 replay 导出的只读脱敏 JSON；浏览器无 API/第三方请求 | `DEV_ONLY / NOT_APPLICABLE` | 否 |
| `AUTHORIZED` | 正式核验来源 | `AUTHORIZED / APPROVED` 才可运行 | 四项生产门禁全部通过后才可 |

`runtime_mode + source_id + capability` 共同隔离 cursor。`AUTHORIZED` 不回退 `DEV_LIVE`，`DEV_LIVE` 不回退
`DEMO_REPLAY`。所有新 evidence、coverage 和 briefing 都保存 provider、access channel、adapter、
endpoint、source identifier、runtime mode、release tier 与 rights status；旧 1.0 表示继续可读，新输出
使用 provenance 1.2，并额外保存 `quality_tier` 与 `evidence_role`。旧 1.1 表示只有在新增语义可无损
投影时才返回，否则对显式 1.1 协商失败关闭。

2026-08-01 的最终隔离 DEV_LIVE smoke（`sma-r1-dev-live-20260801-04`）只证明开发链路行为：法定公告
成功 5 条，公司与行业新闻成功 8 条；财务变化与行情快照均以 `AKSHARE_UPSTREAM_UNAVAILABLE`
失败，宏观快讯以 `DEV_LIVE_MACRO_NOT_QUALIFIED` 失败。两个成功 capability 的 cursor 为 v1，三个
失败 capability 保持 v0；数据库没有 DEMO_REPLAY source run，证明没有静默补位。13 条 DEV_LIVE
文档的最早 `first_seen_at` 为实际系统获知时间 `2026-07-31T22:48:16Z`，在 08:55 截止视图中可见数量
为 0。诊断项目 03 不计成功；项目 03/04 的容器、卷和网络均已完整清理。

2026-08-03 的 `R1_FUNCTIONAL` 候选新增独立免费源矩阵，保持 `DEV_ONLY/UNVERIFIED`：公告以
CNINFO/交易所公开 metadata 为权威/交叉核验候选，财务使用公告 metadata 与东方财富白名单字段，行情
使用东方财富并以 BaoStock `0.9.3` 作交叉核验，公司新闻只作 discovery，宏观只允许 gov.cn/MIIT
固定官方页面。所有 SDK/HTTP 访问均在可终止子进程中执行 10 秒硬超时、最多两次、有界体积和熔断；
rights gate 先于 login/network。显式固定标的 smoke 已证明 10 个能力终态、至少一个真实成功且 replay
run 为 0，但该结果不代表任何上游数据具备缓存、训练、AI_USE、导出或再分发授权。

2026-08-04 的持续预览仅临时启用 `gov_cn_public_macro` 与 `miit_public_macro` 两个配置驱动来源，
固定访问国务院政策文件库和工信部政务公开的 HTTPS allowlist。每条记录只保留标题、发布时间、
发布方、分类和 canonical URL；不保留网页 HTML、正文、摘要、对象或 embedding。输出固定标记为
`DEV_LIVE / DEV_ONLY / UNVERIFIED / DISCOVERY`，只形成 `macro.flash.discovery` 线索和
`publication.headline` 槽位，不能作为政策事实、R7 正式授权覆盖或商业数据就绪的证明。无新增
内容属于成功刷新，会推进业务 watermark 并独立记录刷新成功时间；403、429、验证码、跨域跳转、
超时或 schema drift 均失败关闭且不推进 cursor。上交所页面仍只作人工回链，未因此启用自动摄取。
这两条 HTML 发现链路在 2026-08-12 因持续抓取失败改为 `enabled: false`；它们继续保留在 catalog
作为历史兼容与诊断能力，但不再进入调度、重试或页面的当前来源统计。宏观当前运行来源统一改为
五类研究服务中的 `research_preview_macro`。

同一候选的五会话 replay、provenance 1.2、静态快照和浏览器验收已通过。`PUBLIC_STATIC` 只发布脱敏
replay，不发布 live 响应、第三方正文、私有 URL、provider cursor 或账户信息。该快照已通过私有、
版本化、AES256 加密的 S3 与 CloudFront OAC 发布到
<https://dtnor7sll2utv.cloudfront.net>；S3 直连返回 403。公网发布不改变免费来源
`DEV_ONLY/UNVERIFIED` 或 R7 正式授权尚未开始的事实。

### 1.3 每来源证据、revision、PIT 与 cursor 原子边界

R1 每个来源/能力批次只有在以下结果作为一个原子成功单元持久化后才能提交该来源新的不透明 cursor：
权利快照仍有效，
全部允许留存的 source record/对象写入成功，事实均有 `evidence_id`，稳定逻辑事件及不可变 revision
写入成功，并保存 `published_at`、`first_seen_at`、`fetched_at` 与 `revision_known_at`。cursor 由
adapter 拥有，上层不得解析、拼接或根据页码猜测。五类 `source_id` 的 cursor、lease、批次审计和
成功水位彼此独立，不得以“任一成功”合并成总体成功。

同一来源的摄取状态必须包含单调 `cursor_version`。两个 worker 从相同不透明 cursor/version V 启动时，
以有期限且可审计的 lease 取得处理权，并在提交时再次 compare-and-swap `V -> V+1`；最多一个 worker
提交对象、数据库 revision、cursor 与 outbox，失败方零提交并从最新版本重新读取。禁止 cursor 回退、
跨页提交、跳页或由超时 worker 覆盖较新状态；lease 过期只允许新 owner 从最后成功版本安全重放。

恢复语义冻结为三个崩溃窗口：

| 崩溃窗口 | 可观察状态 | 重放结果 |
|---|---|---|
| 对象 staging 前 | 无对象、无 DB 记录、cursor/version 不变、未 ack | 从同一页和同一业务幂等键重新开始 |
| 对象写入后、DB commit 前 | 对象仅为 staging/orphan，不能标成功；cursor/version 不变、未 ack | 以 content hash 复用或安全清理对象，再原子重放，不产生重复 evidence/revision |
| DB commit 后、ack 前 | 对象、evidence/revision、cursor V+1 与 outbox 已一起提交 | redelivery 以 batch audit ID/幂等键识别已提交批次，只补 ack，不再次写入或推进 cursor |

历史 `decision_time/as_of` 查询同时过滤 `first_seen_at` 与 `revision_known_at`；回补、更正或延迟发现
的数据不能进入当时尚不可见的简报。`NEW / UPDATE / CORRECTION / CONTRADICTION` 只描述已持久化
revision 相对上次成功 cursor 的实质变化；重复 payload 不形成第五种用户可见变化。

`CONTRADICTION` 比较同一事实槽位/期间中仍有效的 evidence claim/revision，不以 `source_id` 是否不同
作为条件：同一来源的两个未声明替换关系且无法同时成立的 claim 是冲突 fixture。来源显式声明对旧
claim 的 replacement/retraction 才归为方向性 `CORRECTION`；不能因为同源就覆盖旧 revision。

| 故障 | 本批次 | cursor | 用户可见状态 | 恢复边界 |
|---|---|---|---|---|
| 权利缺失/过期、401/403 | 不调用或立即停止，不保留越权内容 | 不变 | `SOURCE_UNAUTHORIZED` / `SOURCE_FORBIDDEN` | 新 rights snapshot 经治理回读后人工恢复 |
| 429 | 停止批次，遵守可验证的 `Retry-After` 或有界退避 | 不变 | `SOURCE_RATE_LIMITED` | 配额窗口后以相同业务幂等键重放 |
| timeout/5xx | 仅做有界重试，预算耗尽后失败 | 不变 | `SOURCE_UNAVAILABLE` | 健康恢复后从最后成功 cursor 重放 |
| schema drift/部分批失败/对象写失败 | 隔离本批次，不提交部分成功 | 不变 | `SOURCE_SCHEMA_CHANGED` / `SOURCE_PARTIAL_FAILURE` | 新契约或完整批次验证通过后重放 |

失败审计只保存安全原因码、批次/追踪标识和允许的 metadata，不保存密钥、授权原文或越权 payload。
原始 provider cursor 也属于 adapter 内部状态，用户界面只显示最后成功时间、业务 watermark、batch
audit ID 和“未推进”。测试向 cursor 注入 sentinel，并断言 HTTP、UI、log、metric、error、trace 与
accessibility tree 均无该 sentinel；错误消息和遥测不得把 cursor 当诊断字段输出。

## 2. 正式发布候选来源与优先级

| 优先级 | 内容 | 主来源 | 权重 | 目标时效 | 事实资格 |
|---:|---|---|---:|---:|---|
| 1 | 公告 | 研究服务 `anns_d`；交易所人工回链 | 0.45 | 60 秒 | `UNVERIFIED` 结构化披露候选 |
| 2 | 财务数据 | 研究服务 `income` | 0.45 | 按披露刷新 | 白名单利润表数值，保留 PIT 时间 |
| 3 | 日行情 | 研究服务 `daily` | 0.45 | 盘后/调度窗口 | 固定标的市场观测 |
| 4 | 公司与行业资讯 | 研究服务 `news` | 0.25 | 60 秒 | 标题 discovery，不能独立确认事实 |
| 5 | 国家政策 | 研究服务 `npr` + gov.cn/MIIT 线索 | 0.25 | 15–60 分钟 | 标题 discovery，官方回链后再确认 |
| 候选，默认禁用 | 国际官方事实 | 国际组织、央行、制裁/出口管制主管机构 | 不设权重 | 依来源 | 仅确认其正式发布；仍需逐站权利登记 |
| 候选，默认禁用 | 海外行情、汇率、利率、商品、物流 | 现有授权供应商的合同覆盖能力 | — | 依合同 | 数值/指数数据，获取与派生用途分别受限 |

权重只是先验。最终质量分还包含主动标的相关性、时效、交叉印证、内容完整性和冲突惩罚。公告和政策永不因“多空分低”丢弃；股吧/舆情永远不能独立触发重大利好或利空。

## 3. 官方核验结果

### 巨潮与交易所

- [CNINFO Data Service](https://webapi.cninfo.com.cn/) 提供正式数据服务；[巨潮资讯网](https://www.cninfo.com.cn/) 是深交所法定信息披露平台。
- 重大内容回链到 [上交所公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/)、[深交所公告](https://www.szse.cn/disclosure/listed/notice/index.html) 或 [北交所公告](https://www.bse.cn/disclosure/announcement.html)。
- 交易所网站可供公众浏览不等于允许批量复制、衍生分发或训练。启用全文缓存前仍需检查 [上交所法律声明](https://www.sse.com.cn/home/legal/)、[深交所法律声明](https://www.szse.cn/application/laws/) 和北交所行情授权要求。

### 东方财富

- [Choice 使用指南](https://choice.eastmoney.com/choicewebfile/UserGuide.pdf) 说明正式量化接口覆盖基本面、财报、历史/实时行情和资讯推送。
- [Choice Python 接口文档](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) 要求账户、令牌和相应流量权限；它不是匿名公共 API。
- [Choice 接口更新页](https://quantapi.eastmoney.com/About/Message?from=web) 仍在持续更新，并提供 CFN 类资讯能力。
- 使用前必须审阅 [东方财富服务协议](https://about.eastmoney.com/home/protocol) 与 [Choice 产品许可协议](https://choice.eastmoney.com/html/userprotocol/userprotocol.html)。

### 同花顺

iFinD/Quant API 是正式接口，但不作为本项目主线。其[部署和鉴权说明](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/deploy.html)、[权限说明](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/permission.html)和 [FAQ](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/faq.html)显示账户配额和 QPS 限制；FAQ 同时说明公告、新闻、研报通常夜间入库，不适合作为盘中突发资讯主源。

### 政策

- 中央政策使用 [国务院政策文件库](https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary)。
- 地方政策只抓取 `configs/local-government-domains.yaml` 中完成单站权利复核的政府域名。
- 行业新闻发布者按网信办最新互联网新闻信息稿源名单筛选；名单更新时必须生成规则新版本。

### 国际官方事实与外部市场

- 制裁和名单事实优先核验 OFAC、联合国安理会等原始发布；出口管制优先核验主管机构正式规则；央行
  决定优先核验各央行原始公告；
- 公司海外经营暴露只由公司公告、年报、正式问询回复、官方直接点名或有双方证据的一跳关系确认；
- GDELT 只允许离线发现候选，不能确认公司事实；OpenSanctions 聚合数据在独立许可通过前不能进入
  生产；ACLED 不作为默认生产数据源；
- `exchange_calendars` 未来只用于交易会话生成/校验，交易所官方日历仍是事实权威；
- 研究、候选和禁用边界见
  [`global-market-and-cross-border-events.md`](../research/global-market-and-cross-border-events.md)。

## 4. 分阶段数据来源登记

每个连接器必须在 `configs/sources.yaml` 登记：

```text
owner
official_url
access_method
license_id
expiry
quota
cache_allowed
fulltext_allowed
embedding_allowed
training_allowed
redistribution_allowed
retention_days
```

CI 运行 `scripts/check_data_rights.py`。R1/R2 合成运行不得触发任何真实连接器。正式发布必须拒绝
pending、过期或用途不完整的 rights record，并把策略快照哈希写入 manifest，保证以后能还原当时依据。

所有来源记录均须保留完整字段、合法日期格式和布尔型 `enabled`。仅明确 `enabled: false` 的休眠
来源可保留原过期日期作为历史记录，不据此取得运行权限；启用时必须重新通过有效期与运行时权限
检查。禁用来源的过期日期不阻断仓库校验，日期格式或时区错误仍会阻断。

## 5. 存储与加工边界

### 行情派生与交易结构压力

“可以查看或下载行情”不自动等于“可以长期保留、生成特征、保存指数、建立相似向量或展示派生统计”。
交易结构能力未来必须分别检查：

- `FETCH`：取得日线/10 分钟行情、股本、公司行为和交易规则；
- `RETAIN`：保存原始响应、Parquet、revision 和历史快照；
- `DERIVE`：计算并保存指标观测、十个贡献簇、盘面结构状态、压力 assessment、版本冻结的相似向量和
  结果分布。

`DERIVE` 与用途维度已经进入 RightsOperation 契约和确定性 gate。生产配置必须拥有逐 capability
grant；任何 `DEV_ONLY/UNVERIFIED` 历史制品不得被展示成正式授权或进入 R1/R2 候选运行。

### 外部市场与模型用途

隔夜外部环境还必须核验合同是否允许非展示型计算、派生结果展示和 LLM 输入。`NON_DISPLAY` 是合同限制
维度，不应直接冒充一个操作；`AI_USE` 也不能与现有 `EMBED/TRAIN` 模糊合并。真实合同出现后，先定义
准确的用途语义（例如 `DISPLAY_DERIVED` 或 `LLM_INPUT`），再将 contracts、YAML、检查器、runtime
policy、manifest、测试和文档原子更新。此前真实境外连接器保持禁用。

### 合同明确允许全文、摘要和 embedding

保存不可变原文到 SeaweedFS，PostgreSQL 保存哈希、对象键、来源、发布时间、首次看到时间和许可快照；
可做 3–7 天热缓存、事件抽取和向量检索，但不对外分发。

### 只允许元数据

只保留标题、发布者、发布时间、原文链接、内容哈希和系统自行抽取的事实字段。`object_key` 为空，不把新闻/研报全文、摘要或 embedding 偷换成“技术缓存”。

### 训练

- 新闻、研报和股吧全文不得进入 LoRA 数据集，除非合同明确写明机器学习训练权。
- 向量化也是复制与加工，不能用“只存 embedding”规避授权。
- 训练优先使用人工模板、自有标注、合成困难负例、明确授权数据和可依法使用的政府正式文件。
- 公告采取结构化事实与短证据位置，不默认把整个 PDF 批量复制进训练集。
- 每个训练数据集必须有 datasheet、来源 manifest、rights hash、清理脚本版本和人工抽检记录。

## 6. 自动访问硬规则

1. R1/R2 候选禁止真实网络；正式发布连接器只允许启用已完成逐用途授权、固定 allowlist、shadow trial
   和 live E2E 的正式 API。
2. 政府官网默认每域名 1 并发、每 5 秒最多 1 请求；使用 `ETag`、`If-Modified-Since` 和增量游标。
3. 授权 API 用令牌桶限流，至少保留 20% 配额余量。
4. 收到 403、429、验证码或风控页立即停止；不得更换 IP、伪造签名、绕过验证码或模拟私有 App 协议。
5. 重试时间固定为 1、5、30 分钟；第三次失败关闭连接器并告警。
6. `robots.txt` 只是技术偏好，不替代合同、版权和数据库权利判断。
7. User-Agent 使用真实项目名、版本和联系信息，所有访问带审计 ID。

## 7. 研究会话与个人信息

- 研究会话只保存规范证券 ID；R2 不接收持仓数量、成本价或券商账户信息。
- API 和模型日志只写必要的会话/证券 ID，不写 token、正文或中间模型输出。
- 外部模型请求默认删除所有可识别个人金融信息。
- 舆情不保存用户名、头像、主页、IP 或原帖全文。
- PostgreSQL/SeaweedFS 备份加密，第三方遥测默认关闭。

## 8. 面向公众前的停止条件

现架构仅面向个人本机研究。只要准备开放注册、给他人推送、收费、引入广告/返佣、公开具体买卖建议或连接真实交易，必须停止发布并重新进行数据许可、证券投资咨询、个人信息和生成式 AI 专项法律审查。免责声明不能代替授权或牌照。

界面固定显示：

> 本系统仅供个人信息整理与研究，不构成证券投资建议、收益承诺或交易指令。AI 生成内容可能存在错误、遗漏或延迟，请以证券交易所、上市公司及政府官方披露为准。投资有风险，用户应独立判断并自行承担决策后果。

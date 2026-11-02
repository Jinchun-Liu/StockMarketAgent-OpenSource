# R1–R6 五类研究来源能力评估

<!-- r1-product-scope:v2 -->

状态：`adopted_for_dev_live`
评估日期：2026-08-25
适用范围：R1 `wp01-product-spec` / `wp05-source-rights` / `wp05a-provider-adapters`；记录开发预览
连接器的已实现边界，不代表真实账户业务 smoke 已通过。

## 2026-08-12 替代结论

本节替代下方 2026-07-27 的历史 CNINFO/Choice 退出门定义；旧内容只保留为候选调研记录，不能再用来
阻断 R1–R6。

R1–R6 采用项目所有者明确提供的两项 Tushare-compatible 研究服务，并复用现有 vendor-neutral
source boundary、`httpx`、证据 revision、PIT、cursor 与来源健康契约，不引入 Tushare SDK 运行依赖。
QuantData 只承担行情，ProMax 承担其余四类；两项服务不得在运行时互相静默补位：

| 类别 | 研究服务 | endpoint | 项目处理 |
|---|---|---|---|
| 公告 | ProMax | `anns_d` | 标题、日期、证券、PDF 回链 |
| 财务 | ProMax | `income` | 白名单利润表摘要 |
| 行情 | QuantData | `daily` | 兆易创新日线 OHLCV |
| 公司与行业 | ProMax | `news` | 只取相关标题和频道 |
| 宏观 | ProMax | `npr` | 科技政策标题、机构、回链 |

QuantData 使用固定 HTTPS 根地址和标准 Tushare POST 信封；ProMax 使用固定 HTTPS GET 路径及
`X-API-Key`，能力说明以项目所有者提供的
[ProMax 接口教程](https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71?singleDoc) 为准。
两者均为独立的第三方兼容服务，不能据此声称为官方 Tushare 服务；因此 R1–R6 固定标记为
`DEV_LIVE / DEV_ONLY / UNVERIFIED`。各 endpoint 可能具有独立账户权限，业务 smoke 必须分别记录成功、
无变化或稳定降级。R7 再执行服务主体、合同、字段、留存、派生、展示、模型用途与再分发的正式核验。

实现不保存新闻/政策正文，不把 token、API key、请求 payload、上游原始错误或原始响应写入日志；
证券端点在网络前锁定 `603986.SH`。无论供应商宣传配额为何，每个账户均采用项目侧最多 30 次/分钟
的保守限流。CNINFO 与 Choice 保留为 R7 候选和交叉核验来源。

2026-08-26 东京运行环境逐接口复测显示：`fina_indicator` 的上游持续不可用，而 `income` 在同一
固定标的、同一时间边界和字段投影下可返回完整结构，因此财务能力改用 `income`。`news` 改为最近
6 小时、每小时一段的有界抓取，避免大结果导致中继超时；`npr` 的 `fields=[] / items=[]` 被视为
“本窗口无数据”，但字段为空且存在数据行仍按 schema 变化失败关闭。`anns_d` 上游故障时继续只降级
公告类别，不用财务日程或新闻冒充法定公告。

ProMax 教程还列出 `stk_auction_o`、`stk_auction_c` 与 `stk_auction` 集合竞价候选接口。它们只登记为
后续能力发现，不进入当前双指数、盘后 14 维相似向量或完整 10 分钟窗口；若要改变这些算法边界，
必须另行升级公式/契约、补 point-in-time 规则并完成对应回归测试。

## 2026-07-27 历史评估（已被上节替代）

## 结论

R1 唯一定义：**固定标的兆易创新（`XSHG:603986` / `603986.SH`）＋CNINFO 与 Choice 两个独立授权
来源＋五类覆盖＋多源冲突＋可下钻简报**。

R1 必需来源恰好为 `cninfo_data_service` 与 `choice_quant_api`，两者均为**条件候选**，当前都不得
启用。CNINFO 负责法定公告及合同允许的披露/F10；Choice 负责合同允许的财务变化、行情、公司与行业
新闻和相关宏观快讯。两者拥有独立 capability rights、cursor、水位、故障和证据，不能互相补位。

官方公开页面与产品文档只能证明平台身份和能力候选，不能证明当前账户拥有认证、配额、增量游标、
历史 revision、缓存、派生、展示、AI 使用或再分发权。任一必需 capability 没有项目所有者可回读
的合同/账户证据时，该覆盖必须独立失败关闭，只允许固定 synthetic fixture。交易所公告页、金十、
AKShare、搜索缓存和其他聚合/包装器均不能成为 R1 fallback；金十只保留为 R2A/R2B 待授权发现源。

## 已核验事实

- [巨潮资讯网](https://www.cninfo.com.cn/) 自述为深圳证券交易所法定信息披露平台，并由深交所
  全资子公司深圳证券信息有限公司运营。
- [巨潮最新公告页](https://www.cninfo.com.cn/new/commonUrl?url=disclosure%2Flist%2Fnotice)
  提供证券代码、简称、公告标题、公告时间与全文入口，并提示栏目内容由上市公司提供。
- [巨潮数据服务入口](https://webapi.cninfo.com.cn/) 是仓库现有来源登记指向的商业/账户型入口；
  匿名公开访问未能核验本项目所需的 API 合同细节。
- [Choice 使用指南](https://choice.eastmoney.com/choicewebfile/UserGuide.pdf) 与
  [Choice Python 接口文档](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html) 表明其为
  账户、令牌和权限控制的专业数据服务，覆盖基本面、财报、历史/实时行情及资讯能力；具体字段、
  配额和用途仍以实际合同为准。
- [上交所公告页](https://www.sse.com.cn/disclosure/listedinfo/announcement/) 与
  [深交所公告页](https://www.szse.cn/disclosure/listed/notice/index.html) 是现有登记的官方回链核验页。
- 仓库已声明 vendor-neutral ingestion contracts、`RightsPolicyPort`、`SourceRegistryPort` 等边界，
  但 `governance/interfaces.json` 中 `RightsPolicyPort` 仍为 `declared`；当前没有 production adapter、
  组合根或已实现的 runtime rights gate，也没有已授权的 CNINFO/Choice adapter。

以上只证明平台身份、公开产品能力候选与现有内部边界，不证明机器批量获取、留存、全文处理、派生
或展示权。

## 事实、推断与未证实项

| 类型 | 内容 | 产品处理 |
|---|---|---|
| 事实 | 巨潮是法定信息披露平台，公开页存在公告与全文入口 | 可作为来源身份和用户回链依据 |
| 事实 | Choice 文档描述账户型量化接口及基本面、行情和资讯能力 | 只用于能力发现，不替代账户/合同授权 |
| 事实 | 仓库将 CNINFO/Choice 均登记为 `enabled: false` | 保持禁用，不能由文档推翻 |
| 推断 | 两个专业接口可共同覆盖 R1 五类信息 | 仅列候选，逐 capability 等待合同/API 证据 |
| 未证实 | 两个来源的认证、endpoint、字段、分页、quota、SLA | 不实现真实调用 |
| 未证实 | 各自增量 cursor、回补窗口、撤回/更正 revision 语义 | 不推进任一生产游标 |
| 未证实 | 原文缓存、全文抽取、embedding、派生、展示、训练、再分发权 | 未明确允许的操作全部拒绝 |

## 权利与能力矩阵

`VERIFIED` 表示已有可回读证据；`PENDING` 表示必须由后续 QRL Dependency Steward 绑定合同或账户
证据；`DENIED` 表示 R1 不申请该能力。

| 来源/能力 | 状态 | 启用条件 | 缺失时行为 |
|---|---|---|---|
| CNINFO 平台身份与公告回链 | `VERIFIED` | 使用官方 URL 与来源标识 | 回链不可用时显示来源故障 |
| CNINFO API FETCH/RETAIN/DERIVE | `PENDING` | 账户、合同、endpoint、用途、认证和留存均可回读 | CNINFO 对应覆盖 `rights_blocked` |
| Choice 财务/行情/新闻/宏观 FETCH | `PENDING` | 账户、合同、字段权限、行情展示和资讯用途均可回读 | Choice 对应覆盖 `rights_blocked` |
| 两来源独立增量/历史回补 | `PENDING` | 各自 cursor、时间窗、分页、修订和撤回语义已冻结 | 不读写该来源成功游标 |
| 两来源配额与并发 | `PENDING` | 各自 quota、429 策略、并发和最小间隔已登记 | 不向该来源发送请求 |
| metadata/原文留存 | `PENDING` | 逐来源、逐字段明确期限、删除和审计要求 | 仅 synthetic fixture |
| 变化识别与冲突派生 | `PENDING` | 两份合同均允许字段级比较和内部研究派生 | 不生成真实变化/冲突 |
| embedding/训练 | `DENIED` | R1 不申请 | 永不发送或训练 |
| 再分发 | `DENIED` | R1 不申请 | 只显示合同允许字段、官方回链与项目自有 metadata |

“公开可浏览”不等于上述数据权利。授权证据必须包含主体、来源、操作、目的、有效期、配额、允许留存
范围与撤销路径；截图、推测、未登录页面或第三方博客均不能解锁。

## R1 双来源最小契约要求

后续 WP02/WP05 只能把下列要求转为 vendor-neutral contract，不得在本工作包发明 API：

1. 每条记录必须保留 `source_id`、稳定来源记录 ID、证券标识、官方 URL、来源发布时间、首次可见时间、
   获取时间、内容/metadata checksum 与授权快照引用。
2. 决策窗口使用 knowledge time；`published_at` 不能替代 `first_seen_at`，回补内容不得反向进入历史决策。
3. CNINFO 与 Choice 分别持有不透明 cursor；各自只在该来源整个批次持久化成功后推进。403、429、
   timeout、schema drift、权利过期或部分批失败时保持该来源原 cursor 并写入可审计失败码。
4. NEW、UPDATE、CORRECTION、CONTRADICTION 必须基于稳定逻辑事件与不可变 revision；删除或撤回不能
   物理覆盖历史证据。
5. 公告事实、系统识别的变化和任何概率研究必须分区展示；系统不得把标题分类或模型输出写成公司事实。
6. 同一事实的跨来源证据合并时保留全部 `evidence_id`；值冲突生成 `CONTRADICTION`，禁止平均、覆盖
   或用“高权威来源”静默删除另一值。

## 失败关闭与恢复

| 事件 | 立即动作 | 用户可见状态 | 恢复条件 |
|---|---|---|---|
| 任一来源无账户/合同或授权过期 | 只停止该来源，不推进其 cursor | `SOURCE_UNAUTHORIZED` / `rights_blocked` | 新授权快照通过治理回读 |
| 401/403 | 停止该来源，不换 UA、不绕登录 | `SOURCE_FORBIDDEN` | 人工修复账户/合同后重新验权 |
| 429 | 停止本批次，按已登记 `Retry-After` 或有界退避 | `SOURCE_RATE_LIMITED` | 窗口结束后受控重试 |
| timeout/5xx | 有界重试；超过预算后失败 | `SOURCE_UNAVAILABLE` | 健康检查恢复且重放成功 |
| schema drift | 隔离原响应，不让裸 payload 穿越 Port | `SOURCE_SCHEMA_CHANGED` | 新契约/fixture/adapter 经独立发布 |
| 部分批成功 | 整批不提交 cursor | `SOURCE_PARTIAL_FAILURE` | 幂等重放完整成功 |
| 权利不含全文/派生 | 丢弃不允许字段，不做替代抓取 | `RIGHTS_SCOPE_BLOCKED` | 合同明确扩权并重新登记 |

不得绕过 robots、验证码、登录、限流或许可；不得把交易所网页、搜索缓存、AKShare 或其他第三方接口
作为静默 fallback。恢复必须从最后一次成功 cursor 幂等重放，并保留失败审计。

## 供应商替换与退出

CNINFO 或 Choice 未授权、长期不可用或不能满足 PIT/revision 要求时，R1 不自动选择替代来源，也不把
另一必需来源当作补位。对应覆盖保持失败关闭；若后续阶段确需替换，QRL 必须先建立来源权利工作包，
完成复用/许可/contract 评审并冻结新 `source_id`。切换不得改写历史 `source_id`、checksum 或
evidence 引用。

## 进入实现前的证据清单

- [ ] CNINFO 与 Choice 各自可回读的账户/合同与有效期；
- [ ] 两来源逐 capability 的 endpoint、操作、用途、证券范围和环境；
- [ ] 两来源各自认证方式、secret 托管、quota、并发、429 与 SLA；
- [ ] 两来源 metadata、原文、全文、派生、展示、AI 使用、embedding、训练、再分发逐项权利；
- [ ] 两来源独立 cursor、分页、回补、撤回、更正和历史 revision 语义；
- [ ] 两来源允许的留存期、删除、备份、审计与退出要求；
- [ ] 固定 fixture、403/429/timeout/schema-drift/部分失败回归测试；
- [ ] `configs/sources.yaml`、reuse decision、`THIRD_PARTY.md` 与运行时 rights registry 由 QRL 单写同步。

在全部适用项完成前，验收结论固定为：**CNINFO/Choice 真实能力未全部获授权，R1 产品规格可冻结，
但对应真实 connector、R1-COVERAGE 与正式产品 E2E 闸门仍关闭。**

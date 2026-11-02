# 复用决策：隔夜外部环境与跨境事件

<!-- reuse-decision-id:external-context-2026-07-22 -->

日期：2026-07-22
状态：accepted planning baseline
- 最终决定：`adapt`

## 问题与验收标准

为一个主动关注 A 股提供跨时区外部市场状态、国际官方事件、一跳公司暴露和开盘反馈；保持权利失败
关闭、PIT、修订和非概率语义。

验收要求包括：相同会话/截止窗口、DST/长假/休市/未定价语义、一跳证据、支持/反证、权利失败关闭、
事实/推断/反馈独立 revision、无第二总分、无交易信号，以及可在本机重放。

## 检索范围

2026-07-22 核验交易日历、SDMX、事件发现、制裁聚合、冲突数据、海外行情、宏观 API、provider 平台和
关系存储候选的官方仓库、文档、许可证或服务条款。研究引用与官方来源入口见
[`../global-market-and-cross-border-events.md`](../global-market-and-cross-border-events.md)。

## 硬门槛

| 候选 | 初始快照 | 许可/条款 | 结果 |
|---|---|---|---|
| exchange_calendars | 官方仓库/release reviewed 2026-07-22；未锁版 | Apache-2.0 | `adapt planned`；需官方日历差异测试 |
| sdmx1 | 官方文档/仓库/PyPI reviewed 2026-07-22；未锁版 | Apache-2.0 | `reference/adapt planned`；需端点权利与 payload contract |
| PostgreSQL | 当前项目基础设施 | PostgreSQL License | `adopt existing`；承载一跳双时态关系 |
| GDELT | 官方数据文档 reviewed 2026-07-22 | 数据条件逐用途复核 | `reference offline`；不能确认公司事实 |
| OpenSanctions | 官方文档/许可说明 reviewed 2026-07-22 | 代码与聚合数据权利分离 | `reference`；聚合数据默认禁用 |
| ACLED | 官方条款 reviewed 2026-07-22 | 专有/商业条款 | `reject default` |
| yfinance | 官方仓库 reviewed 2026-07-22 | Apache-2.0代码；上游数据权利分离 | `reject production` |
| FRED API | 官方条款 reviewed 2026-07-22 | 服务条款 | `reject default` |
| OpenBB | 官方仓库 reviewed 2026-07-22 | AGPL-3.0 | `reject default` |

## 决定与边界

复用现有项目契约边界、PostgreSQL、Parquet、SeaweedFS、Valkey 和任务名称；未来按需要适配一个交易
日历实现和一个 SDMX 客户端。事实确认依赖官方机构与公司披露。跨时区比较、公司暴露、传导解释、
外部状态和开盘反馈由项目领域层维护。不引入图数据库、第二套 provider 平台或网页抓取兜底。

候选的详细理由如下：

| 候选 | 许可/条款初检 | 决定 | 理由与激活条件 |
|---|---|---|---|
| exchange_calendars | Apache-2.0 | `adapt planned` | 成熟交易会话候选；固定版本/hash并与官方交易所日历校验后才引入 |
| sdmx1 | Apache-2.0 | `adapt planned` | 可减少 SDMX 协议重复实现；数据端点权利逐项核验，当前不安装 |
| PostgreSQL 双时态关系 | PostgreSQL License | `adopt existing` | 一跳事件传导规模足够，事务和审计能力已在项目边界内 |
| GDELT | 数据使用条件需逐项复核 | `reference offline` | 只做候选发现和背景研究，不能确认公司事实 |
| OpenSanctions | 代码与聚合数据权利分离 | `reference` | 参考数据模型；企业使用聚合数据前需单独许可 |
| OFAC、UN、BIS、央行、METI 官方发布 | 各机构条款 | `source candidate` | 确认事实的首选；只有来源登记和操作权利通过后才启用 |
| ACLED | 专有/商业条款 | `reject default` | 许可、成本和当前单股票范围不满足默认采用条件 |
| yfinance | Apache-2.0 代码；上游数据仅个人用途等限制 | `reject production` | 代码许可不授予行情数据权利，不作为失败兜底 |
| FRED API | 官方使用条款 | `reject default` | 当前软件/AI开发及缓存限制与计划不匹配，除非取得明确授权 |
| OpenBB | AGPL-3.0 | `reject default` | 重复项目 provider 边界，不能解决上游数据权利 |
| Neo4j/独立图数据库 | 需额外许可与运维 | `reject current` | 一跳关系无需新数据库，PostgreSQL 已能表达双时态关系 |

## 必须自建的领域缺口

1. 15:00–08:55 跨时区相同会话、相同截止窗口语义；
2. 夏令时、半日市、临时休市、A 股长假和未定价事件处理；
3. 六个外部观察块和不生成第二总分的分类状态；
4. 事实事件、公司暴露推断和市场反馈的独立 revision；
5. 只允许官方点名、公司披露、一跳证据和行业背景的直接度规则；
6. 主动关注标的筛选、支持/反证和无暴露时 abstain；
7. 数据权利 lineage、PIT manifest 和冻结简报；
8. 09:40 后确认、吸收、背离或无法判断的非因果反馈。

## 未采用内容

- 不复制第三方的全球风险分数、产业链图谱、证券映射或买卖标签；
- 不通过 Yahoo、Investing 等网页抓取弥补授权缺口；
- 不把 GDELT、新闻共现或 LLM 猜测写成公司供应关系；
- 不安装依赖、不修改锁文件、不创建 adapter 或连接器；
- 不预登记 Port、schema、任务、HTTP 或指标。

## 验证

本轮只完成官方资料核验、内部接口/指标检索、候选职责比较、机器登记和文档同步。没有下载/运行候选
代码、访问真实端点、安装依赖或执行性能基准。未来验证必须覆盖官方日历差异、SDMX contract、权利
到期、PIT、100次重放、故障降级和退出演练。

## 供应链与归属

本轮未复制代码、数据、公式、截图或文案，未修改依赖锁或 SBOM。实际采用时必须固定 release/commit、
制品 SHA-256、LICENSE/NOTICE、传递依赖、CVE 和来源数据合同，并同步 `THIRD_PARTY.md` 与机器登记。

## 退出与复审触发条件

实际引入任何候选前，必须固定 release/commit 和制品 hash，核验许可证、NOTICE、传递依赖、CVE、
数据条款、缓存/派生/模型用途、官方日历差异、故障降级和卸载方案。条款、发行渠道、维护状态或目标
数据供应商变化时重新评审。

研究证据见 [`../global-market-and-cross-border-events.md`](../global-market-and-cross-border-events.md)，
机器可读索引见 [`../../../governance/reuse-decisions.json`](../../../governance/reuse-decisions.json)。

# 盘面结构指标与图表压缩复用决策

<!-- reuse-decision-id:chart-structure-indicators-2026-07-22 -->

- 日期：2026-07-22
- 状态：accepted planning baseline
- 最终决定：`adapt`
- 机器登记：`governance/reuse-decisions.json`
- 运行时状态：未实现；没有新增依赖、制品、接口、迁移或复制代码

## 问题与验收标准

目标不是复刻复杂炒股软件，而是将 K 线、MA、成交柱、分时和相对环境压缩为用户十秒可读、可下钻、
可重放的状态。方案必须满足：一个压力总分、诊断不重复计票、PIT、A 股规则、缺失拒绝、非概率表达、
前端不重算、许可清晰且真实数据权利独立审核。

## 检索范围

产品侧核验 TradingView Technical Ratings/RVOL-at-Time/VWAP/Volume Profile、TC2000 Volume Buzz、
StockCharts SCTR/RRG/宽度、ChartMill Technical/Setup、TrendSpider 多因子提醒、MarketSurge 以及国内公开
诊断表达。研究侧核验图形识别、价量关系、数据窥探、Yang–Zhang、RiskMetrics EWMA 等原始论文或官方
资料。代码侧核验 TA-Lib、Polars、Qlib、scikit-learn、statsmodels、STUMPY、ruptures、tsfresh、
bukosabino/ta、Lightweight Charts、ECharts 和 KLineChart。

详细证据见 `docs/research/technical-indicator-landscape.md`。

## 硬门槛

| 候选类别 | 许可/来源 | 数据与安全 | PIT/语义 | 当前结果 |
|---|---|---|---|---|
| 标准技术原语 | 必须为许可清晰、可锁版的官方实现 | 不携带行情权利；不得静默填补缺失 | 结果须经 golden vector 与 A 股规则对照 | 仅 TA-Lib 进入 planned adopt |
| 金融图表 | 许可允许本地产品使用并满足署名/NOTICE | 前端不得获取未授权数据或计算业务规则 | 只渲染服务端版本化结果 | 仅 Lightweight Charts 进入 planned adopt |
| 研究算法 | 许可清晰且可隔离在离线环境 | 不得自动生成用户信号 | 前缀输入、purge/embargo、冗余与多重检验通过 | 当前全部保持 reference |
| 产品/专有指标 | 只用公开一手说明作产品研究 | 不抓取、复制专有数据或权重 | 不把 Buy/Sell、主力或筹码标签改名复刻 | 只参考信息压缩方式 |

任何候选不能替代 `FETCH + RETAIN + DERIVE` 权利；真实数据授权、A 股规则、缺失拒绝和历史可重放是
一票否决项。

## 决定与边界

1. 压力指数从 20 个原始特征直接投票修订为五分面、十个等权贡献簇；
2. 新增必需六槽 profile，承载趋势组织、位置与修复、成交参与、波动环境、盘中接受、相对环境六维
   诊断及独立 availability，不生成第二个总分；
3. TA-Lib 仍是标准技术原语唯一目标，Polars 承担批量/横截面，Qlib/scikit-learn 只做离线 PIT 与冗余
   验证；
4. STUMPY、ruptures、tsfresh、statsmodels 只登记为离线参考，本轮不加入依赖；
5. 金融 K 线/分时唯一使用 Lightweight Charts，ECharts 只负责非金融通用图，前端不计算指标；
6. 项目自行拥有指标角色、父子关系、七类参照总体、十贡献簇、六维状态、A 股/PIT/abstain 和受控
   文案；
7. 不采用 TradingView 式 Buy/Sell 多数投票、命名蜡烛预测、bar 数据主力/资金/筹码或自动数百特征。

## 候选结论

| 候选 | 许可 | 决定 | 原因 |
|---|---|---|---|
| TA-Lib Python | BSD-2-Clause | `adopt planned` | 成熟标准原语；实施前固定制品/hash 和 golden vectors |
| Polars | MIT | `adopt existing` | 全市场滚动、同钟点、横截面和 Parquet |
| Qlib | MIT | `adapt offline` | PIT/walk-forward 研究，不进入在线关键路径 |
| scikit-learn | BSD-3-Clause | `adopt offline` | 相关聚类、基线和精确距离 |
| statsmodels | BSD-3-Clause | `reference` | Rolling OLS/HAC 核验；当前无需新依赖 |
| STUMPY | BSD-3-Clause | `reference` | motif/discord 候选；先过 PIT 和增量门禁 |
| ruptures | BSD-2-Clause | `reference` | 变化点候选；需要前缀输入和修订语义 |
| tsfresh | MIT | `reference` | 宽候选发现；多重检验后不能自动发布 |
| Lightweight Charts | Apache-2.0 | `adopt planned` | 唯一金融图渲染层；保留归因 |
| ECharts | Apache-2.0 | `adopt planned` | 只做宽度、分布和质量等通用图 |
| bukosabino/ta | MIT | `reject` | 与 TA-Lib 重复，默认填 NaN 工作流不符 abstain |
| KLineChart | Apache-2.0 | `reject` | 前端指标计算会形成第二规则源 |

## 自研缺口

成熟库只提供数学或渲染原语，不能替代：A 股交易规则 revision、PIT 行业/股本、每动作数据权利、
证券/行业/市场各自的日线/同钟点总体与离散规则总体、父子依赖和重复计票治理、状态支持/反证、缺失拒绝、历史快照
语义，以及“不是概率/建议”的产品边界。

## 验证

当前只完成官方资料、许可和职责初审，没有下载包、运行第三方代码或真实性能基准。未来采用必须通过：
固定 release/hash、SBOM/CVE、golden vectors、A 股困难集、PIT、冗余和增量测试、前端归因、390px/键盘
验收及退出演练。当前验证结论只支持规划，不支持安装依赖或宣称功能已实现。

## 供应链与归属

本轮没有复制第三方代码、模型、数据或制品，也没有修改依赖锁。`THIRD_PARTY.md` 只登记 planned、
reference 和 rejected 状态；未来引入前必须固定 release/commit、制品 hash、许可证/NOTICE、SBOM/CVE，
并单独核验行情数据合同。Lightweight Charts 页面归因要求必须在实际 UI 验收中确认。

## 退出与复审触发条件

TA-Lib 或 Lightweight Charts 无法通过供应链/正确性/性能门禁时，必须重新创建复用决策；不得自动启用
第二套技术指标库或前端业务计算。STUMPY、ruptures、tsfresh 未证明稳定增量时保持 reference，不进入
依赖或用户界面。许可证、维护状态、关键 CVE、数据粒度、性能目标、PIT 规则、用户任务或产品边界发生
变化时必须复审；移除任何已采用组件时，先保留项目稳定契约和历史快照的可读性。

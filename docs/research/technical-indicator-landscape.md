# K 线、分时与深层量价指标调研

状态：`planning baseline`
核验日期：2026-07-22
适用阶段：R4 交易行为轴
指标事实源：[`behavior-indicator-catalog.md`](../data/behavior-indicator-catalog.md)
产品事实源：[`behavior-pressure-product-spec.md`](../product/behavior-pressure-product-spec.md)

> 本文件记录产品、论文和开源实现的复用调研，不表示任何指标、依赖、页面或数据源已经实现。
> 2026-07-27 治理 v2 后，下述五分面/十簇路线仅保留为
> `candidate_full_formula_version` 的影子候选；R4A 必须先运行少量成熟信号的
> `simple_baseline_version`，不得把本调研中的权重、阈值和样本门槛当作已冻结产品事实。

## 1. 执行结论

用户真正需要的不是在本产品里重新看到一套同花顺式 K 线、MA、MACD、KDJ、成交柱和“主力资金”面板，
而是让系统替用户完成读图中的重复劳动：识别趋势是否有序、价格处于什么位置、成交是否异常且持续、
盘中价格是否被成交接受、波动处于压缩还是扩张、个股变化是否得到行业和市场确认。

唯一采用路线是：

1. 保留一个数值主指标——**交易结构压力指数**，不再创建技术买卖总分；
2. 将原计划的 20 个等权原始特征整理为影子候选的 **五分面、十个等权贡献簇**，同一量价现象只能
   获得一票；是否优于简单基线必须由覆盖、稳定性、冗余和反例实验决定；
3. 新增不计分的 **盘面结构解读**，固定六维状态：趋势组织、位置与修复、成交参与、波动环境、
   盘中接受、相对环境；
4. K 线和分时图作为证据下钻，默认首屏显示状态、变化、原因、反证和数据质量；
5. 所有状态由确定性、版本化规则产生；LLM 只改写已经物化的解释，不能决定状态或补齐数据；
6. 逐笔、盘口或账户身份不可观测时明确显示数据上限，不用 bar 数据伪造“主力”“真实资金流”或
   “筹码成本”。

海外市场、国际事件、商品、汇率和利率不是技术指标，也不构成第二个总分；它们属于事件与基本面轴，
只可在有目标暴露证据时作为六维“相对环境”的支持/反证。专题研究见
[`global-market-and-cross-border-events.md`](global-market-and-cross-border-events.md)。

## 2. 为什么不能继续堆传统指标

[TradingView Technical Ratings](https://www.tradingview.com/support/solutions/43000614331-technical-ratings/)
将 15 个均线/滤波条件和 11 个振荡器投票压成单一评级，证明用户确实需要降低认知负担；但 MA10、
MA20、EMA10、EMA20、MACD、RSI、Stochastic、Williams %R 等大量输入都来自同一价格路径，简单平均会
让“近期价格处于高位”被重复计算多次。本项目不采用其 Buy/Sell 语义和多数投票方式。

学术证据支持“部分技术形态可被客观识别并包含增量信息”，但不支持任何图形跨市场、跨时期稳定预测。
[Lo、Mamaysky 与 Wang](https://www.nber.org/papers/w7613)说明形态可以系统化；
[Sullivan、Timmermann 与 White](https://ideas.repec.org/a/bla/jfinan/v54y1999i5p1647-1691.html)
则说明在大量技术规则中挑选历史最佳规则会遭遇严重数据窥探。价量研究同样表明，高成交后的延续或
反转取决于风险分担、信息交易和市场状态，不能把“高换手上涨”写成必跌规则
（[Campbell、Grossman 与 Wang](https://www.nber.org/papers/w4193)、
[Llorente 等](https://www.nber.org/papers/w8312)）。

因此，指标数量不是质量目标；正交含义、PIT、可观察性、增量价值和用户是否正确理解才是准入条件。

## 3. 成熟产品如何降低读图负担

| 产品/能力 | 已验证的产品价值 | 本项目采用 | 明确拒绝 |
|---|---|---|---|
| [TC2000 Volume Buzz](https://help.tc2000.com/m/69401/l/862838-how-to-use-today-s-volume-buzz) | 当前成交与历史相同交易进度比较，比“今日最活跃”更能识别异常 | 同钟点成交活跃分位，区分窗口值与累计值 | 产品名、专有体验及把放量直接解释为方向 |
| [TradingView Relative Volume at Time](https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/) | 明确使用相同 session offset，支持 regular/cumulative 两种口径 | R4c 的同钟点 10 分钟基线 | 对未完成 bar 或缺失历史 bar 的静默填补 |
| [TradingView VWAP](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/) | 把分时价格与当日成交加权参考价放在同一上下文 | VWAP 偏离、在其上方时间、穿越和拒绝/修复 | 把 VWAP 当预测器；它是滞后参考线 |
| [ChartMill Technical Rating / Setup Quality](https://www.chartmill.com/documentation/technical-analysis/indicators/87-Chartmill-Technical-Rating-and-Setup-Quality-scores) | 把“趋势健康”与“当前位置是否过度延伸”分开 | 趋势组织和位置状态分开 | 专有评分、阈值和交易标签 |
| [StockCharts SCTR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stockcharts-technical-rank) | 用相对同类排名替代概率 | 历史/同行分位语义 | 名称、固定权重和专有公式 |
| [StockCharts RRG](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-types/relative-rotation-graphs-rrg-charts) | 用相对趋势和相对动量区分领先、减弱、改善、落后 | 透明的行业残差趋势与变化状态 | RRG、RS-Ratio、RS-Momentum 名称和专有标准化 |
| [StockCharts 市场宽度](https://chartschool.stockcharts.com/table-of-contents/market-indicators/percent-above-moving-average) | 显示指数表面涨跌背后的参与广度 | 上涨家数、站上 MA20/60、新高新低等压成参与度 | 忽略成分历史和停牌的现时成分回填 |
| [TrendSpider 多因子提醒](https://help.trendspider.com/kb/alerts/multi-factor-alerts-overview) | 多条件、多周期及 confirmed-bar 语义降低噪声 | 已完成窗口、跨周期状态和确认标签 | 未完成窗口触发及把条件组合称为预测 |
| [TradingView Volume Profile](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/) | 按价格区间看成交密集度 | 只作为未来更细粒度数据的研究参考 | 用日线/10 分钟 OHLCV 重建真实成交价分布或筹码 |
| 同花顺/东方财富公开诊断 | 证明国内用户需要“一眼看懂”和异常聚合 | 借鉴低操作负担 | 黑箱“主力控盘/资金流向/成功率”文案、页面抓取和专有数据 |

国内产品公开页面通常没有足够公式、数据时间语义和版本信息，不能因用户熟悉某个标签就把它视为成熟
可复用算法。竞品只作为需求和交互证据，不作为公式来源。

## 4. 指标族调研结论

### 4.1 从复杂图表到用户状态的统一映射

“市场常用”只证明用户熟悉，不证明具有稳定预测能力。本项目把成熟度拆成三层：`A` 表示公式和计算
原语成熟，可直接进入确定性派生候选；`B` 表示产品实践广泛，但阈值、组合与 A 股适用性仍需本项目
验证；`C` 表示只适合离线研究。成熟度不等于买卖有效性。

| 用户原本查看的图表 | 量化提取 | 成熟度依据 | 压缩后的用户结果 | 规划决定 |
|---|---|---|---|---|
| K 线实体、上下影、收盘位置 | 实体/全幅、上下影比例、收盘位置、突破保留、冲高回吐 | `A`：OHLC 确定性几何；形态命名的预测解释仅为 `C` | 位置与修复、收盘/路径拒绝、支持与反证 | 使用连续数值；不输出“锤头线看涨”等命名结论 |
| 跳空、前高前低、回撤路径 | 隔夜/盘中收益分解、20/60/252 日回撤深度与持续、修复比例、前序区间突破接受 | `A/B`：收益和回撤公式成熟，窗口和状态阈值需 A 股验证 | 高位延伸、突破被接受、突破未确认、回撤中、低位修复 | 进入不计分状态；除权、复牌和涨跌停由规则护栏隔离 |
| MA5/10/20/60/120、MACD 柱 | MA 排列、ATR 标准化斜率、排列持续期、反复穿越、log-price OLS 斜率/R²、路径效率 | `A`：MA/EMA/MACD 为成熟原语；`B`：状态合成需验证 | 上行有序、下行有序、震荡拉扯、结构切换 | 采用趋势组织；MACD/PPO 与 MA 不再各自投票 |
| BOLL、ATR、K 线振幅 | ATR%、带宽、20 日实现波动、短/长 EWMA 比、下行半方差；Yang–Zhang 只作核验 | `A`：波动原语成熟；`B`：压缩/扩张阈值需冻结 | 压缩、常态、扩张、下行波动偏重、跳变待核 | 一个波动语义只计一次；区间估计不得与实现波动重复加权 |
| 成交量/成交额柱、换手率 | 自由流通换手、成交额相对 20 日中位数、同钟点 regular/cumulative RVOL、连续活跃窗口 | `A`：换手和比率；`B`：同钟点产品模式及持续性状态 | 清淡、正常、持续活跃、一次性脉冲、放量分歧 | 盘中优先使用同钟点基线；不把放量直接转成方向 |
| 分时价线、均价线、开盘区间 | session VWAP 偏离、VWAP 上方时间、完整窗口穿越、开盘区间保留、日内回吐/修复、路径效率 | `A`：VWAP；`B`：10 分钟接受状态组合 | VWAP 上方/下方接受、冲高回落、下探修复、双向拉锯 | R4c 诊断重点；必须标记 10 分钟粒度，不能声称窗内成交顺序 |
| 个股、行业指数和涨跌家数 | PIT 行业残差、beta/相关、特异波动、上涨宽度、站上 MA 比例、新高新低、横截面离散 | `A`：回归、宽度和离散统计；`B`：合成状态 | 行业共振、个股领跑、孤立上涨、个股独弱、系统共振 | 进入相对环境；市场、行业、证券分别使用正确参照总体 |
| “筹码峰”、成本分布、主力资金 | 10 分钟 OHLCV 无法还原逐价成交、持有人身份、订单方向和换手后的所有权 | 当前数据下不可观测 | 明确显示数据缺口；最多展示已标注的参考成本代理 | 拒绝真实筹码/主力命名，不用图形插值制造数据 |

固定处理链为：`PIT 原始观测 → 版本化原语 → 正确参照总体分位 → 状态规则 → 支持/反证 → 摘要与图表批注`。
前端只渲染已物化结果，不自行重算指标；LLM 只能解释最后一步，不能修改前述任一状态。

### 4.2 进入确定性状态层

| 指标族 | 代表原语 | 用户问题 | 规划结论 |
|---|---|---|---|
| 趋势组织 | MA20/60/120 斜率与排列、log-price OLS 斜率/R²、路径效率、ADX | 当前是有序推进还是来回拉扯 | 状态层；MA 是滞后确认，不给金叉买入结论 |
| 位置与修复 | 回撤深度/持续/修复、20/60 日区间位置、Donchian 突破接受、跳空吸收、K 线连续几何 | 高位延伸、回撤还是低位修复 | 状态层；不用命名蜡烛图 |
| 成交参与 | 自由流通换手、同钟点 RVOL、连续高活跃窗口、成交集中度、零成交窗口 | 是一次脉冲还是持续参与 | 日线与盘中状态；不称主动买入或主力流入 |
| 波动环境 | ATR%、Bollinger BandWidth、短长 EWMA 波动比、下行半方差 | 压缩、常态还是扩张 | 状态层；不同波动原语不重复计票 |
| 盘中接受 | session VWAP 偏离、在 VWAP 上方时间、穿越、开盘区间保留、日内回撤/修复、路径效率 | 涨跌是否被当日成交持续接受 | R4c 第一优先级；只用完整 10 分钟窗口 |
| 相对环境 | 行业残差 20/60/120、beta/相关/特异波动、宽度、横截面离散、领导集中度 | 个股独立变化还是行业/市场共振 | 状态层；成分和权重必须 PIT |
| A 股规则 | 涨跌停触及/收盘、ST、IPO 特殊期、停复牌 | 普通状态是否仍可比较 | 护栏状态；不进入普通技术百分位 |

### 4.3 只作交叉核验或离线研究

- [Yang–Zhang OHLC 波动](https://ideas.repec.org/a/ucp/jnlbus/v73y2000i3p477-91.html)：充分使用开高低收并
  处理隔夜跳空，作为现有波动口径的未来替换候选，不能与实现波动、ATR、BBW 同时独立计分；
- [RiskMetrics EWMA](https://www.msci.com/documents/10199/5915b101-4206-4ba0-aee2-3449d5c7e95a)：
  适合描述波动升温/降温；衰减因子和窗口必须版本化，不能照搬跨资产阈值；
- [STUMPY](https://github.com/stumpy-dev/stumpy)：BSD-3-Clause、维护活跃，适合离线 motif/discord 和矩阵
  轮廓研究；当前不进入在线关键路径；
- [ruptures](https://github.com/deepcharles/ruptures)：BSD-2-Clause，适合离线变化点研究；每次只能读取
  `as_of` 前缀，且后来数据到达可改变断点，产品化前必须有修订语义；
- [tsfresh](https://github.com/blue-yonder/tsfresh)：MIT，可用于宽候选发现；自动提取数百特征会放大
  多重检验，不能直接进入用户公式；
- 方差比、Hurst、分形、熵、频谱和小波只保留研究记录，不进入当前用户产品。

### 4.4 当前明确拒绝

- RSI、KDJ、Stochastic、Williams %R、CCI、MACD、PPO 与多条 MA 各自投票；
- 命名蜡烛形态、头肩顶、双顶、波浪、斐波那契和主观支撑/阻力买卖信号；
- OBV、MFI、CMF、A/D 等被命名为“真实资金流”；若未来兼容展示，只能称价格方向成交量代理；
- 日线或 10 分钟线生成的真实筹码峰、订单失衡、CVD、VPIN、盘口深度、大单和主力身份；
- 未经 walk-forward 和多重检验控制，从数百候选中挑历史最好者；
- 在线自学习、静默调权，以及把技术状态转换为上涨/下跌概率。

## 5. 可商用开源实现复用决策

| 项目 | 许可初审 | 决定 | 边界 |
|---|---|---|---|
| [TA-Lib Python](https://github.com/TA-Lib/ta-lib-python) | BSD-2-Clause | `adopt planned` | 只计算/对照 MA、ADX、ATR、BBANDS 等标准原语；项目拥有状态语义、PIT 和缺失规则 |
| [Polars](https://github.com/pola-rs/polars) | MIT | `adopt existing` | 全市场滚动、同钟点和横截面、Parquet；不拥有领域公式 |
| [Microsoft Qlib](https://github.com/microsoft/qlib) | MIT | `adapt offline` | PIT、walk-forward、候选特征核验；不进入 API/worker 在线关键路径 |
| [scikit-learn](https://github.com/scikit-learn/scikit-learn) | BSD-3-Clause | `adopt offline` | 冗余聚类、基线和精确距离；不学习在线压力权重 |
| [statsmodels](https://github.com/statsmodels/statsmodels) | BSD-3-Clause | `reference` | Rolling OLS、HAC/Newey–West 的独立核验候选；本轮不新增依赖 |
| [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) | Apache-2.0 | `adopt planned` | 唯一金融 K 线/分时渲染层，保留 NOTICE/署名；浏览器不计算业务指标 |
| [Apache ECharts](https://github.com/apache/echarts) | Apache-2.0 | `adopt planned, non-financial` | 只做宽度、分布和质量等通用图；不重复 K 线渲染职责 |
| [bukosabino/ta](https://github.com/bukosabino/ta) | MIT | `reject default` | 与 TA-Lib 角色重复，且文档仍提示用户预先填补 NaN，不符合项目 abstain 语义 |
| KLineChart | Apache-2.0 | `reject default` | 前端自带指标计算会形成第二套规则；不与 Lightweight Charts 并行 |

库的开源许可不授予任何行情、研报或图表数据权利。真实数据仍需逐动作允许 `FETCH + RETAIN + DERIVE`。

## 6. 指标准入的唯一流程

任何新指标按以下顺序进入，不能因“市场常用”跳步：

1. 登记经济/市场结构含义、观察等级、公式、时间粒度、最少样本、父子指标和不可观测边界；
2. 完成来源权利、PIT、A 股公司行为和交易规则困难集；
3. 通过固定样本、论文/TA-Lib 对照、golden vectors 和性质测试；
4. 对候选做滚动 Spearman、层次聚类、VIF/条件数和父子依赖图审查；
5. 同一语义簇只允许一个固定权重，相关候选降为诊断或交叉核验；
6. 用 purged walk-forward、embargo 和多重检验控制验证未来 1/3 日 MAE 排序增量；
7. 分板块、规则期、规模、年份和市场状态核验覆盖、稳定性、abstain、闪烁和提醒负担；
8. 冻结公式、角色、版本、显示文案和回滚方案，旧快照不重算；
9. 只有真实生产者存在时才登记接口或统一指标。

## 7. 用户价值验证

不预先声称“比同花顺快多少”。实施后使用同一组任务做成对测试：用户分别阅读原始 K 线/分时/MA/量柱
和本项目状态卡，回答“当前路径、压力、主要原因、反证和数据是否可信”。

- 主结果：任务正确率、完成时间；
- 驱动结果：10 秒内复述状态和一个理由、两次操作内找到原始输入；
- 护栏：把压力误读为概率/建议的比例、把诊断误认成计分项的比例、把过期或 abstained 当成当前值的
  比例，以及每个状态是否可完全重放。

该验证只评价认知压缩和可解释性，不评价买卖收益。

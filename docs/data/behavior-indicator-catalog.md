# 交易行为指标目录与盘面状态规范

状态：`planned`
目录版本：`behavior-indicator-catalog-v2-experimental`
产品语义：[`behavior-pressure-product-spec.md`](../product/behavior-pressure-product-spec.md)
计分方法：[`behavior-pressure-methodology.md`](behavior-pressure-methodology.md)
调研依据：[`technical-indicator-landscape.md`](../research/technical-indicator-landscape.md)

> 本目录是未来公式冻结前的单一指标事实源。本轮不新增契约、schema、迁移、依赖或运行时计算。
> 目录中的五分面、十簇、权重、最小样本与盘中继承规则属于
> `candidate_full_formula_version`；R4A 必须先以 `simple_baseline_version` 测量可用性，候选未晋级前
> 不得作为正式产品分数。

## 1. 目录目的

本目录解决三个容易混淆的问题：

1. 哪些数据真正进入 0–100 压力分，哪些只帮助用户读懂图表；
2. 每个指标应该和证券自身、规模同行、其他行业还是市场历史比较；
3. 如何确保同一成交、收益或波动现象不会以多个名称重复投票。

未来每个指标定义必须完整登记以下字段：

```text
indicator_id
owner_concept
role: CORE_CLUSTER_INPUT | DIAGNOSTIC | GUARDRAIL | RESEARCH | REJECTED
observation_class: DIRECT | DETERMINISTIC_DERIVED | EMPIRICAL_PROXY | UNOBSERVABLE
parent_indicator_ids
time_grain / window / supported_modes
comparison_population_kind
formula_definition_version
minimum_observations
PIT_and_rule_requirements
missing_behavior
state_semantics_version
source_or_research_reference
```

## 2. 七类参照总体

统一套用“自身 50% + 同行 50%”会让市场宽度等组级指标退化。本目录按指标粒度固定唯一参照方式。

| `comparison_population_kind` | 使用对象 | 标准化 | 最少样本 |
|---|---|---|---:|
| `SECURITY_DAILY` | 个股日线指标 | 50% 自身过去 252 个有效日 mid-rank + 50% 当日规模/规则同行 mid-rank | 自身 120 日；同行 30 只 |
| `INDUSTRY_DAILY` | 申万一级行业指标 | 50% 该行业过去 252 日 mid-rank + 50% 当日跨行业 mid-rank | 自身 120 日；20 个有效行业 |
| `MARKET_DAILY` | 全市场唯一状态 | 100% 全市场自身过去 252 日 mid-rank | 120 日 |
| `SECURITY_INTRADAY_CLOCK` | 个股完整 10 分钟窗口 | 50% 自身过去相同钟点 120 日 mid-rank + 50% 当前同钟点规模/规则同行 mid-rank | 自身 120 日；同行 30 只 |
| `INDUSTRY_INTRADAY_CLOCK` | 行业完整 10 分钟窗口 | 50% 该行业过去相同钟点 120 日 mid-rank + 50% 当前同钟点跨行业 mid-rank | 自身 120 日；20 个有效行业 |
| `MARKET_INTRADAY_CLOCK` | 全市场完整 10 分钟窗口 | 100% 全市场过去相同钟点 120 日 mid-rank | 120 日 |
| `DISCRETE_RULE` | ST、停牌、IPO、涨跌停规则等 | 不做百分位；直接阻断、降级或显示状态 | 必要规则 revision 完整 |

证券同行唯一规则：排除目标证券；在 `as_of` 时有效、同交易板块、交易规则可比，且对当前指标具有
有效观测、权利和自由流通市值的证券中，按
`abs(log(free_float_market_cap_peer) - log(free_float_market_cap_target))` 排序，取最近 100 只；同距离按
`instrument_id` 升序。有效数为 30–100 时全部使用，少于 30 时对应证券级核心输入 abstain；盘中同行还
必须具有相同 `session_offset` 的完整窗口。成员、距离、PIT 市值 revision 和样本数进入 manifest。行业
不再进入证券同行交集，行业影响由行业残差和宽度单独表达。自身历史总体只含当前观测之前的有效值。

最终原始总分的历史参考状态使用同公式、同交易板块、同规则期的证券日，从中按上述对数自由流通市值
距离取最近的最多 10,000 个 `instrument-day`；少于 1,000 个时总分 abstain。参考总体不再使用会随边界
跳变的固定市值五分位。

## 3. 候选完整公式：五分面、十个贡献簇

候选版本暂令每个分面 20%，每个分面两个 10% 核心贡献簇。簇内可有多个原语，但整个簇只获得一次
候选权重；父指标和交互项不能另获一票。等权是否保留由冗余、反例和 walk-forward 决定。

| 分面 | 贡献簇 ID | 核心输入与规划口径 | 粒度 | 不再独立计分的相关项 |
|---|---|---|---|---|
| 交易热度与分歧 | `activity_anomaly` | 自由流通换手分位与成交额/前 20 日中位数分位的簇内均值 | SECURITY_DAILY / SECURITY_INTRADAY_CLOCK | `abs(return) × volume`、换手周期、成交持续性 |
| 交易热度与分歧 | `close_path_rejection` | 上影线比例与收盘位置压力的单一几何组合；零振幅由规则护栏处理 | SECURITY_DAILY / SECURITY_INTRADAY_CLOCK | 命名蜡烛、失败突破标签、日内回吐 |
| 延伸与拥挤 | `reference_cost_extension` | 收盘价高于 20 日成交额加权参考价的距离，以 ATR 标准化；负值截为 0 | SECURITY_DAILY | CGO、价格距 MA、锚定 VWAP |
| 延伸与拥挤 | `short_path_extension` | 正向 5 日路径收益和 60 日区间位置形成一个固定簇；窗口、缩放和截尾待 golden vector 冻结 | SECURITY_DAILY | MAX5、RSI/KDJ 高位、Donchian 位置 |
| 流动性脆弱 | `price_impact` | 20 日 Amihud `mean(abs(return)/amount)`，单位固定 | SECURITY_DAILY | 盘中同钟点价格冲击、Roll 价差 |
| 流动性脆弱 | `spread_proxy` | 20 日 Corwin–Schultz 高低价差代理；负估计按论文规则处理而非填 0 | SECURITY_DAILY | Roll、零变动窗口、一字板频率 |
| 波动与尾部 | `volatility_expansion` | 20 日实现波动水平与短/长 EWMA 波动比形成固定簇 | SECURITY_DAILY / SECURITY_INTRADAY_CLOCK | ATR%、BBW、Yang–Zhang、最大真实波幅 |
| 波动与尾部 | `downside_tail_asymmetry` | 20 日下行半方差占比与 60 日 NCSKEW 形成固定簇 | SECURITY_DAILY | 单日最大跌幅、跳跃份额 |
| 市场与行业背离 | `breadth_context` | 市场宽度状态与行业宽度状态形成固定簇；两者各自按正确组级总体标准化 | MARKET_DAILY + INDUSTRY_DAILY / MARKET_INTRADAY_CLOCK + INDUSTRY_INTRADAY_CLOCK | A/D、站上 MA、新高新低等底层宽度项 |
| 市场与行业背离 | `idiosyncratic_divergence` | 个股相对 PIT 行业组合的绝对残差；方向只进入解释，不改变压力方向 | SECURITY_DAILY / SECURITY_INTRADAY_CLOCK | 正行业残差延伸、beta/相关、残差拥挤 |

簇内每个必需输入缺失时该簇缺失；任一核心簇缺失导致对应分面和总分 abstain。不得使用同簇其他输入
补权，也不得把非核心诊断提升为临时替代项。

### 3.1 日线、盘中来源与继承

十个贡献簇都支持 `post_close` 和 `intraday_10m`，但盘中不得临时发明一套未登记的原语。来源固定为：

| 贡献簇 | `post_close_source` | `intraday_source` | `inheritance_policy` |
|---|---|---|---|
| `activity_anomaly` | 当日日线换手与成交额 | 当前完整窗口及开盘至当前的 RVOL、自由流通换手 | 重新计算，使用 `SECURITY_INTRADAY_CLOCK` |
| `close_path_rejection` | 当日 OHLC 收盘几何 | 截至当前窗口的 session OHLC 与路径几何 | 重新计算，使用 `SECURITY_INTRADAY_CLOCK` |
| `reference_cost_extension` | 当日收盘相对 20 日参考成本 | 上一盘后确认簇 | 整簇继承并记录 source assessment；盘中价格距参考成本只作诊断 |
| `short_path_extension` | 当日 5 日路径与 60 日位置 | 上一盘后确认簇 | 整簇继承并记录 source assessment |
| `price_impact` | 20 日 Amihud | 上一盘后确认簇 | 整簇继承；同钟点价格冲击只作诊断 |
| `spread_proxy` | 20 日 Corwin–Schultz | 上一盘后确认簇 | 整簇继承 |
| `volatility_expansion` | 20 日实现波动与短长 EWMA | 截至当前窗口的 session 波动相对同钟点历史 | 重新计算，使用 `SECURITY_INTRADAY_CLOCK` |
| `downside_tail_asymmetry` | 20 日下行半方差与 60 日 NCSKEW | 上一盘后确认簇 | 整簇继承；盘中上下半方差只作诊断 |
| `breadth_context` | 市场/行业日线宽度 | 当前完整窗口的市场/行业同钟点宽度 | 重新计算，分别使用 `MARKET_INTRADAY_CLOCK`、`INDUSTRY_INTRADAY_CLOCK` |
| `idiosyncratic_divergence` | 当日个股相对行业残差 | 当前完整窗口个股相对 PIT 行业残差 | 重新计算，使用 `SECURITY_INTRADAY_CLOCK` |

继承只允许来自当前交易日前最近一份、公式兼容的 `post_close/CONFIRMED` assessment；必须记录来源 ID、
版本、截至日和 lineage。来源缺失、过期、权利失效、公司行为或规则变化导致不可比时，对应簇缺失并使
盘中总分 abstain。继承簇不得因盘中诊断变化而被暗中重算；上述映射变化属于公式破坏性变化。

### 3.2 贡献与解释

```text
facet_score = mean(two cluster scores)
raw_total = mean(five facet scores)
cluster_contribution = (cluster_score - 50) × 0.10
```

- 解释只在贡献簇级排序，底层原语折叠为簇内证据；
- V1 展示阈值规划为 `abs(cluster_contribution) >= 1.0 raw point`；
- 最多三个推高簇、两个缓解簇，按 `abs(contribution) DESC, cluster_id ASC`；
- 经验分位映射后的非线性最终分不宣称可被精确加总归因；
- `formula_version`、`feature_set_version`、簇角色/权重和阈值仅在单次影子实验内固定；是否成为产品
  语义由 promotion gate 与所有者批准决定。

### 3.3 从原 20 项规划迁移

原 20 项不再被称为 20 张等权选票：

- `absolute_return_activity` 降为 `activity_anomaly` 的诊断交互，避免成交额重复计票；
- `MAX5`、CGO、参考价距离和区间位置归入延伸语义族，只保留两个核心簇；
- 成交集中度、一字板频率改为诊断/规则护栏；
- 最大真实波幅改为波动诊断，避免与实现波动重复；
- 行业残差只在 `idiosyncratic_divergence` 核心出现一次；
- `residual_crowding` 改为行业共振诊断，不再单独计分；
- 市场/行业宽度按各自粒度标准化，不再套证券同行分位。

## 4. 盘面结构解读：六维不计分状态

`MarketStructureProfile` 固定包含六个维度槽。每个槽固定包含 `dimension`、`availability`、可空的
`state/change`、`supporting_indicator_ids`、`counter_indicator_ids`、`quality`、`reason_codes`、`as_of` 和
`semantics_hash`。`availability` 只有 `ready / degraded / abstained / not_applicable`：前两者必须有状态，
后两者状态为空且必须有稳定原因。状态可以在压力总分 abstain 时独立可用；页面必须明确“计分项”与
“读图语境”的区别。

profile 的 overall availability 由适用维度确定：全部适用维度 ready 时为 ready；至少一个适用维度可用但
存在 degraded/abstained 时为 degraded；所有适用维度都无法分类时为 abstained。维度 not_applicable 不
算缺失，profile 本身不得省略或用 null 表示未运行。

### 4.1 趋势组织

回答“路径是否有序”，不回答“接下来涨还是跌”。

| 指标 | 规划公式/含义 | 角色 |
|---|---|---|
| `ma_structure_20_60_120` | MA 排列、ATR 标准化斜率和排列持续天数 | DIAGNOSTIC |
| `log_trend_ols_20_60` | log(close) 回归斜率、ATR 标准化斜率和 R² | DIAGNOSTIC |
| `path_efficiency_20` | `abs(net move) / sum(abs(bar moves))` | DIAGNOSTIC |
| `adx_14` | 趋势强度原语；不使用统一 25 阈值做买卖判断 | DIAGNOSTIC |

固定状态：`上行有序 / 下行有序 / 震荡拉扯 / 结构切换`。

### 4.2 位置与修复

| 指标 | 规划公式/含义 | 角色 |
|---|---|---|
| `drawdown_depth_duration_20_60_252` | 相对滚动峰值的深度、距峰值交易日和修复比例 | DIAGNOSTIC |
| `range_position_20_60` | 收盘在前序高低区间的位置；当日不得参与形成比较阈值 | DIAGNOSTIC |
| `breakout_acceptance` | 越过此前区间后收盘保留/回落；只描述确认与否 | DIAGNOSTIC |
| `gap_absorption` | 隔夜跳空与盘中回吐/保留分开；除权、复牌、涨跌停单独处理 | DIAGNOSTIC |
| `candle_geometry` | 实体、上下影和收盘位置的连续数值 | DIAGNOSTIC |

固定状态：`高位延伸 / 突破被接受 / 突破未确认 / 回撤中 / 低位修复 / 中位震荡`。

### 4.3 成交参与

| 指标 | 规划公式/含义 | 角色 |
|---|---|---|
| `turnover_persistence` | 最近窗口超过自身活跃阈值的次数和连续长度 | DIAGNOSTIC |
| `relative_volume_at_time` | 当前完整窗口与历史同钟点中位数之比和分位 | DIAGNOSTIC，R4c 核心语境 |
| `cumulative_relative_volume_at_time` | 开盘至当前的累计值与历史同进度比较 | DIAGNOSTIC，R4c 核心语境 |
| `volume_burst_persistence` | 单窗口脉冲与连续三个完整窗口活跃分开 | DIAGNOSTIC |
| `zero_trade_and_stale_price_windows` | 无成交与有成交无价格变化分别统计；可作为状态解释输入 | GUARDRAIL |

固定状态：`清淡 / 正常 / 持续活跃 / 一次性脉冲 / 放量分歧`。

### 4.4 波动环境

| 指标 | 规划公式/含义 | 角色 |
|---|---|---|
| `atr_percentile` | ATR/close 的自身及同行分位 | DIAGNOSTIC |
| `bollinger_bandwidth_percentile` | 带宽相对证券自身历史的位置及变化 | DIAGNOSTIC |
| `ewma_volatility_ratio` | 固定短/长衰减口径的波动比 | CORE_CLUSTER_INPUT |
| `yang_zhang_volatility` | OHLC 波动交叉核验；通过替换闸门前不计分 | RESEARCH |
| `intraday_semivariance_balance` | 完整 10 分钟收益上/下半方差；标记粗粒度 | DIAGNOSTIC |

固定状态：`压缩 / 常态 / 扩张 / 下行波动偏重 / 跳变待核`。

### 4.5 盘中接受

只在 `intraday_10m` 且至少一个完整窗口形成后评价。`post_close` 或尚无完整盘中窗口时，该槽为
`not_applicable` 且状态为空，不能把“未到盘中”混入领域状态或复用昨日标签。

| 指标 | 规划公式/含义 | 角色 |
|---|---|---|
| `session_vwap_distance` | 收盘相对累计 VWAP 的距离，以同钟点 ATR/波动缩放 | DIAGNOSTIC |
| `time_above_vwap` | 完整窗口位于 VWAP 上方的比例 | DIAGNOSTIC |
| `vwap_crossings` | 相邻完整窗口跨越 VWAP 次数；10 分钟粒度可能漏掉窗内穿越 | DIAGNOSTIC |
| `opening_range_retention` | 突破首两个完整窗口区间后保留比例 | DIAGNOSTIC |
| `intraday_drawdown_repair` | 从日内高点回吐、从低点修复及高低点发生时段 | DIAGNOSTIC |
| `same_clock_price_impact` | `abs(window return)/window amount` 的同钟点分位；观察等级为经验代理，只称价格冲击代理 | DIAGNOSTIC |

固定状态：`VWAP 上方接受 / VWAP 下方接受 / 冲高回落 / 下探修复 / 双向拉锯`。

### 4.6 相对环境

| 指标 | 规划公式/含义 | 角色 |
|---|---|---|
| `industry_residual_strength_20_60_120` | 个股相对 PIT 行业组合的多周期残差及变化 | DIAGNOSTIC |
| `market_industry_beta_correlation` | 60/120 日 beta、相关和特异波动 | DIAGNOSTIC |
| `breadth_stack` | 上涨占比、站上 MA20/60、新高减新低；整个宽度簇只获一次核心权重，也可支持状态解释 | CORE_CLUSTER_INPUT |
| `cross_sectional_dispersion` | 行业收益绝对离差和共振，只描述分化/同步 | DIAGNOSTIC |
| `leadership_concentration` | PIT 权重下正/负贡献分别计算 HHI | RESEARCH |
| `intraday_industry_confirmation` | 个股 10 分钟残差与行业同钟点宽度 | DIAGNOSTIC，R4c |

固定状态：`行业共振 / 个股领跑 / 孤立上涨 / 个股独弱 / 系统共振 / 相对环境分化`。

该维度的正式输入仍是 A 股个股、行业和市场。隔夜外部环境不是技术指标、核心输入或第八类参照总体；
只有在目标暴露和离线验证成立后，才可附加为支持/反证引用，且不得改变
`breadth_context`、`idiosyncratic_divergence` 或本槽的确定性状态。方法见
[`overnight-external-context.md`](overnight-external-context.md)。

## 5. 规则护栏和不可观测项

### 5.1 A 股规则状态条

- 当时有效的涨跌停价格及距离；
- 是否触及、是否收于限制价；
- 10 分钟 bar 只能说明“窗口内观察到触及后离开”，不能声称开板次数或封单强弱；
- 一字板、停牌、复牌、ST/风险警示、IPO 特殊规则期、临时停市；
- 公司行为和自由流通股本 revision。

缺少必要规则时相关状态和总分 abstain，不用今天规则回填历史。

### 5.2 当前数据明确不可观测

| 用户常见名称 | 所需数据 | 当前处理 |
|---|---|---|
| 主力净流入、主动买卖、CVD | 逐笔成交方向或账户分类 | `REJECTED` |
| 盘口深度、撤单、封单金额、订单失衡 | Level-2 订单簿与事件流 | `REJECTED` |
| VPIN、Kyle lambda 的真实微观实现 | 逐笔或细粒度带方向成交 | `REJECTED` |
| 真实筹码峰、持有人成本与获利人数 | 成交价分布、换手路径及持有人数据 | `REJECTED`；CGO 只称获利盘代理 |
| 10 分钟 Volume Profile | 窗口内逐价成交分布 | 当前拒绝；不能用 OHLC 均匀分配伪造 |

## 6. 冗余和角色冻结闸门

在 R4a 编码前，以合成和获授权的 PIT 研究数据执行：

1. 对所有候选原语建立 `parent_indicator_ids` 有向图，父子或派生交互不得同时独立计分；
2. 按年份、板块、规则期和市场状态计算滚动 Spearman；
3. 以层次聚类、VIF 和条件数确认同一语义族；
4. 对十个贡献簇做 leave-one-cluster-out 的 1/3 日 MAE 排序增量；
5. 核验覆盖率、abstain 率、分档抖动和提醒负担；
6. 高相关候选保留更简单、数据要求更低且更容易解释者，其余降为诊断或研究；
7. 该过程只去重和冻结，不训练权重，不用最终保留集调阈值。
8. 海外宽基、离岸资产、商品和宏观序列不得作为技术指标重复计入压力或相对环境。

## 7. 历史相似向量

不再预先写死 `vector(20)`。R4b 只处理 `post_close` 历史情景，候选向量因此由十个贡献簇连续值和趋势
组织、位置与修复、成交参与、波动环境、相对环境五个盘后诊断坐标组成；盘中接受不进入盘后相似向量。
候选使用与第 6 节相同的冗余门禁，按以下确定性规则冻结：

1. 每个 `owner_concept` 最多保留一个代表；
2. 冗余门禁可以只为相似距离删除任一核心簇或诊断坐标，但不得改变压力公式；五个压力分面各至少保留
   一个代表坐标；
3. 诊断坐标只有在对相似片段区分产生跨窗口稳定增量时保留；
4. 最终维度必须在 8–14 之间，成员、缩放和距离按 `analog_feature_set_version` 固定；
5. 数据库迁移只能在维度冻结后创建，旧维度和新维度分表/分版本，不混写；
6. 同交易板块、同规则期、相近自由流通市值为候选资格；行业信息通过相对环境坐标表达，不再硬性
   收窄到“板块∩行业∩市值分组”；
7. pgvector 只召回，应用层执行精确距离、PIT、结果成熟和相邻行情片段去重。

## 8. 版本与快照要求

- `indicator_definition_version`：单个原语公式；
- `feature_set_version`：贡献簇、角色、权重和参照总体；
- `diagnostic_semantics_hash`：六维状态、availability 和阈值；
- `analog_feature_set_version`：历史相似向量成员、缩放和距离；
- `comparison_semantics_hash`：只控制压力分可比性，不因纯诊断规则变化无谓重置；
- 每个状态和贡献簇随 assessment 物化，历史页面不使用当前代码重算；
- 事件锚定参考点必须在 `as_of` 前已知，禁止事后挑选最低点；
- MA、ATR、区间、缺口、VWAP 和行业总体都使用当时可见的复权、公司行为、规则和成分版本。

本目录只有在论文/官方定义复核、golden vectors、A 股困难集、PIT、冗余、覆盖和用户理解测试全部通过
后，才能从 `draft` 变为 `frozen`。

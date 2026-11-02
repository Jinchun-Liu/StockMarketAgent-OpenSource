# 交易结构压力指数方法与数据规范

> 2026-08-07 R1 冻结：本方法的五分面、十簇和 0–100 经验秩映射以
> `behavior-pressure-research.v1` 用于兆易创新单股研究展示。它仍不是下跌概率、买卖建议、目标价或正式
> 投资指标；样本、PIT、去相关、精确复核和缺失即不评分的约束全部保留。

状态：`planned`
方法版本：`experimental-v2`（simple baseline + shadow candidate）
产品语义：[`behavior-pressure-product-spec.md`](../product/behavior-pressure-product-spec.md)
工程边界：[`behavior-pressure-framework.md`](../architecture/behavior-pressure-framework.md)
指标目录：[`behavior-indicator-catalog.md`](behavior-indicator-catalog.md)

> 本文件定义研究约束，不表示仓库已经产生任何指数或指标。未通过 promotion gate 的权重、阈值和
> 五分面结构都是假设，不是产品真理。

## 1. 方法原则

1. 只描述结构压力，不估计明日涨跌概率；
2. 所有输入满足 point-in-time（PIT）：`available_at <= as_of` 且 `revision_known_at <= as_of`；
3. 先使用当时有效的交易规则、自由流通股本、公司行为、行业成分和复权因子，再计算特征；
4. 先运行少量成熟信号的 `simple_baseline_version`，再把五分面、十簇等权作为
   `candidate_full_formula_version` 影子比较；不在线学习、不静默调参；
5. 两版本都先报告覆盖与缺失；候选缺失任一核心贡献簇时拒绝给候选总分，缺失值绝不当作 0；
6. 日线确认和 10 分钟临时模式分别版本化、分别比较；
7. LLM 不进入公式，只解释已经物化的确定性结果；
8. 结果必须携带公式、特征、规则、同行总体、样本数、数据清单和权利快照版本/hash。
9. 海外市场、国际政策、地缘事件、商品、汇率和利率不进入五分面、十贡献簇或总分；它们属于独立的
   事件与基本面上下文。

## 2. 数据上限与基本单位

首版只规划：

- 全市场日线；
- 全市场标准化 10 分钟线；
- 当时有效的自由流通股本、公司行为、交易规则、ST/风险警示、停复牌、涨跌停制度；
- 当时有效的交易板块、申万一级行业和自由流通市值观测；
- 一个主动关注标的的压力快照；全市场和少量同行只形成可复用参照总体，不生成逐股产品快照。

不规划逐笔、Level-1、Level-2、盘口深度、订单失衡、VPIN 或交易者身份。日线和 10 分钟数据不能证明
“主力出逃”“散户接盘”或具体投资者盈亏状态。

金额和成交额使用精确十进制；收益率是小数比例；所有时间持久化为 UTC，交易时段和展示使用
`Asia/Shanghai`。

## 3. 数据可见性和修订

每条行情或状态至少具有：

```text
observed/effective time
available_at
revision_known_at
source_id
source_record_id or market_data_lineage_id
rights_snapshot_hash
data_version / adjustment_version
supersedes_revision_id (optional)
```

历史查询只能选择在 `as_of` 前已经可见的最新 revision。供应商更正、股本补录或复权因子修订产生新
快照并通过 `supersedes_assessment_id` 关联，不能覆盖用户当时看到的旧结果。

assessment 自身固定满足：

```text
window_end <= assessment.as_of <= generated_at <= revision_known_at
```

knowledge-as-of 查询必须同时过滤 `assessment.as_of`、`generated_at` 和 `revision_known_at`，避免延迟
计算结果或后来追加的修订泄漏到其实际生成前的历史视图。

原始价格用于交易规则、涨跌停和停牌判断；收益特征使用当时可见的公司行为和复权因子。禁止使用今天
重新计算的复权结果改写历史决策输入。

## 4. 候选五分面与十个贡献簇

原“每分面四个原始特征直接等权”的规划经深层指标调研后取消。成交额及其交互、收益与波动、行业
残差和宽度会产生明显重复计票；市场/行业组级指标也不能套用证券同行分位。V1 每个分面只保留两个
固定权重贡献簇，底层成员和诊断角色以指标目录为唯一事实源。

### 4.1 交易热度与分歧

| 贡献簇 ID | 规划定义 | 压力方向 |
|---|---|---|
| `activity_anomaly` | 自由流通换手分位与成交额/前 20 日中位数分位的簇内均值 | 活跃异常越高 |
| `close_path_rejection` | 上影线比例与收盘位置压力的单一几何组合 | 冲高拒绝越明显 |

收盘位置压力规划为：

```text
upper_shadow_ratio = (high - max(open, close)) / max(high - low, epsilon)
close_location_pressure = (high - close) / max(high - low, epsilon)
close_path_rejection = mean(upper_shadow_ratio, close_location_pressure)
```

一字板或零振幅日不使用 `epsilon` 伪造普通位置，而由交易规则护栏单独处理并记录质量状态。
`absolute_return_activity` 只保留为簇内诊断，不能与成交额再次获得权重。

### 4.2 延伸与拥挤

| 贡献簇 ID | 规划定义 | 压力方向 |
|---|---|---|
| `reference_cost_extension` | 收盘价高于 20 日成交额加权参考价的距离，以 ATR 标准化 | 离参考成本越远 |
| `short_path_extension` | 正向 5 日路径收益与 60 日区间位置形成一个固定簇 | 短期正向延伸越高 |

CGO 继续作为不计分的经验代理，参考 Grinblatt–Han 成交换手衰减思路。对窗口内第 `k` 个滞后日：

```text
raw_weight[t-k] = turnover[t-k] × product(1 - turnover[j])
                  for j in (t-k+1 ... t-1)
reference_price = sum(normalized_weight × adjusted_close)
CGO = (close[t-1] - reference_price) / close[t-1]
```

换手率必须基于当时自由流通股本并限制在数据可解释范围。确切截尾、权重归一化和除权处理在实现前以
论文样例和 A 股公司行为 golden vectors 冻结。CGO 只帮助解释“参考成本代理”，不能识别真实持有人
盈亏，也不能与 `reference_cost_extension` 重复计分。行业残差只归属第 4.5 节。

### 4.3 流动性脆弱

| 贡献簇 ID | 规划定义 | 压力方向 |
|---|---|---|
| `price_impact` | 20 日 Amihud `mean(abs(return) / amount)`，成交额单位固定 | 同量价格冲击越大 |
| `spread_proxy` | 基于相邻两日高低价的 20 日 Corwin–Schultz 代理 | 低频价差越宽 |

Amihud 代理按论文定义使用绝对收益与成交金额比率；它是低频非流动性代理，不等同于盘口深度。

Corwin–Schultz 两日估计的设计表达为：

```text
beta = log(H[t] / L[t])² + log(H[t-1] / L[t-1])²
gamma = log(max(H[t], H[t-1]) / min(L[t], L[t-1]))²
alpha = (sqrt(2 × beta) - sqrt(beta)) / (3 - 2 × sqrt(2))
        - sqrt(gamma / (3 - 2 × sqrt(2)))
spread = 2 × (exp(max(alpha, 0)) - 1) / (1 + exp(max(alpha, 0)))
```

实施前必须复核论文中的隔夜跳空调整、零振幅、负估计、最少有效对数和公司行为处理，不能只按上述
摘要直接进入生产。成交集中度降为诊断，一字涨跌停频率降为交易规则护栏；两者不再各占核心权重。

### 4.4 波动与尾部

| 贡献簇 ID | 规划定义 | 压力方向 |
|---|---|---|
| `volatility_expansion` | 20 日实现波动水平与固定短/长 EWMA 波动比形成一个簇 | 高波动且扩张越明显 |
| `downside_tail_asymmetry` | 20 日下行半方差占比与 60 日 NCSKEW 形成一个簇 | 下行/左尾越重 |

NCSKEW 先以日对数收益 `W` 规划：

```text
NCSKEW = - n × (n - 1)^(3/2) × sum(W³)
         / ((n - 1) × (n - 2) × (sum(W²))^(3/2))
```

实现前必须明确 `W` 使用个股原始收益还是 PIT 行业/市场模型残差，并以公式版本区分。V1 选择一条后
冻结，不得在同一历史序列中混用。该指标描述过去收益分布的负偏形态，不是未来崩盘概率。最大真实
波幅、ATR%、Bollinger BandWidth 和 Yang–Zhang 只作诊断/交叉核验，不能再次进入核心权重。

### 4.5 市场与行业背离

| 贡献簇 ID | 规划定义 | 压力方向 |
|---|---|---|
| `breadth_context` | 市场宽度状态和行业宽度状态形成固定簇；分别按市场级/行业级总体标准化 | 参与宽度越弱越高 |
| `idiosyncratic_divergence` | 个股收益相对当时 PIT 行业组合的绝对残差 | 独立偏离越大越高 |

宽度底层可包含上涨占比、站上 MA20/60 和新高/新低参与，但整个语义簇只有一个权重。`residual_crowding`
改为不计分的行业共振/离散诊断；精确窗口、名称和最少成员仍需在 R4a 方法 spike 冻结，不得称“羊群”
或用未经版本化的黑箱替代。

境外市场变化不能进入 `breadth_context` 或 `idiosyncratic_divergence`，也不能作为压力方向修正项。

## 5. 标准技术原语和复用

- TA-Lib 规划用于 MA、ADX、ATR、BBANDS 等标准技术原语的对照或实现；采用前固定版本、制品 hash、BSD 许可、
  wheel 供应链和与 golden vectors 的一致性；
- Polars 规划继续用于滚动、横截面百分位和 Parquet 批处理；
- PostgreSQL/pgvector 只召回历史相似候选；应用层执行精确距离和 PIT 复核；
- scikit-learn 只用于离线精确近邻和基线核验；
- Qlib 继续隔离在离线 PIT 研究，不进入在线关键路径；
- Amihud、Corwin–Schultz、CGO、NCSKEW、EWMA 和本项目贡献簇/状态规则由项目拥有最小实现，并附论文引用、公式
  版本、golden vectors、性质测试和退出说明。

V1 不使用 LightGBM 或 LLM 训练权重；不引入 pandas-ta、vectorbt、mlfinlab、STUMPY、ruptures、tsfresh
或归档演示项目的在线生产代码。后三者只保留离线研究候选，不构成当前依赖。

## 6. 参照总体

原“同交易板块 ∩ 申万一级行业 ∩ 市值五分位”的交集容易在科创板、北交所和小行业大量少于 30 只，
并会让行业宽度等组级常量在证券横截面退化。本方法固定六类连续参照总体和一个离散规则总体：

| 粒度 | 标准化方法 | 最少样本 |
|---|---|---:|
| 证券日线 | 50% 自身 252 日 + 50% 当日规模/规则同行 | 自身 120 日、同行 30 只 |
| 行业日线 | 50% 该行业 252 日 + 50% 当日跨行业 | 自身 120 日、20 个行业 |
| 全市场日线 | 100% 全市场自身 252 日 | 120 日 |
| 证券盘中 | 50% 自身同钟点 120 日 + 50% 当前同钟点规模/规则同行 | 自身 120 日、同行 30 只 |
| 行业盘中 | 50% 该行业同钟点 120 日 + 50% 当前同钟点跨行业 | 自身 120 日、20 个行业 |
| 全市场盘中 | 100% 全市场同钟点 120 日 | 120 日 |
| 离散规则 | 不做百分位，直接 guardrail/abstain | 必要 revision 完整 |

证券同行排除目标证券，并在 `as_of` 时必须同交易板块、交易规则可比、证券有效、输入、权利和自由
流通市值有效。按
`abs(log(free_float_market_cap_peer) - log(free_float_market_cap_target))` 排序，取最近 100 只；同距离
按 `instrument_id` 升序。30–100 只时全部使用；盘中还要求相同 `session_offset` 的完整窗口。行业不再
作为证券同行硬交集，而由行业残差和行业宽度独立表达。有效同行少于 30 时对应证券级核心输入 abstain，
不使用临时 fallback。自身历史总体不包含当前观测；成员、距离、PIT 市值 revision 和样本数进入 manifest。

## 7. 百分位和合成

### 7.1 mid-rank 百分位

对值 `x` 和总体 `S`：

```text
percentile(x, S) = 100 × (count(S < x) + 0.5 × count(S = x)) / count(S)
```

并列规则固定为 mid-rank。异常值不先 winsorize 后假装原始；如需截尾，必须作为特征公式版本的一部分
记录阈值和原因。

### 7.2 指标标准化

每个指标必须由指标目录固定 `comparison_population_kind`，再按第 6 节相应公式计算。证券/行业/市场的
日线与同钟点盘中口径，以及离散规则不得混用。所有历史只使用当前 `as_of` 前、同公式版本的有效值；
盘中只比较相同 session offset 的完整窗口。

### 7.3 候选分面和原始总分

候选完整公式暂令每个贡献簇占总分 10%，每个分面由两个簇等权形成，五个分面再次等权：

```text
cluster_score = frozen aggregation of required normalized inputs
facet_score = mean(2 complete cluster scores)
raw_total = mean(5 complete facet scores)
```

任一核心输入、贡献簇或分面不完整时总分 `null`，不得重分配剩余权重。簇内聚合规则随
`feature_set_version` 冻结；增加原语不能增加该簇权重。

### 7.4 候选经验分位映射

原始总分在过去五年、同交易板块、同规则期的可比证券日中，按对数自由流通市值距离取最近的最多
10,000 个 `instrument-day`，再映射为 mid-rank 经验分位，形成最终 0–100 指数。参考状态少于 1,000 个
时 abstain。公式、参考总体版本、距离规则、成员、样本数和数据清单 hash 必须进入快照。

### 7.5 驱动与缓解因素的确定性归因

解释使用经验分位映射前的贡献簇，不伪造对非线性最终分位的精确因果分解。每个贡献簇全局权重为
`0.5 × 0.20 = 0.10`：

```text
explanation_contribution = (cluster_score - 50) × 0.10
```

- 正值为推高因素，负值为缓解因素，单位为 raw composite score point；
- V1 最小展示阈值规划为 `abs(contribution) >= 1.0`；实施前随 explanation version 冻结；
- 按 `abs(contribution) DESC, cluster_id ASC` 稳定排序；
- 最多展示 3 个推高因素和 2 个缓解因素，数量不足时如实少显示；
- 底层原语折叠为簇内证据，不能在首屏伪装成多个独立理由；
- 页面、LLM 和前端不得自行降低阈值、重排或把诊断项包装成计分反证。

### 7.6 比较语义键

同 mode 的两个值只有下列语义键完全相同才可计算 delta、连线或触发 transition：

```text
comparison_semantics_hash = sha256(
  formula_version,
  feature_set_version,
  canonical_score_precision,
  rule_semantics_version,
  band_semantics_version,
  peer_semantics_version,
  reference_mapping_version
)
```

每日变化的同行成员、参考样本和数据 revision 属于 input manifest，不进入语义键；但任何会改变特征、
公式、精度、分档阈值、同行定义或参考映射方法的修改都必须提升对应版本、生成新 hash，并同步提升
作为 umbrella release 的 `formula_version`。不得只换实现而沿用旧语义键。

`explanation_version` 不改变分数语义，可在未来窗口独立演进，但创建 assessment 时即冻结；同一逻辑
assessment 重放必须返回已物化解释，不能仅因当前 explanation policy 更新而改写或另建旧窗口结果。

六维盘面结构诊断使用独立 `diagnostic_semantics_hash`。只改变诊断规则不重置压力分历史；只有贡献簇、
角色、权重或参照总体改变时才提升 `feature_set_version`、`comparison_semantics_hash` 和 umbrella
`formula_version`。

## 8. 历史候选分档（不用于 R4A 上线）

```text
0 <= score < 40     平稳
40 <= score < 60    升温
60 <= score < 75    关注
75 <= score < 90    拥挤
90 <= score <= 100  脆弱
```

这些阈值仅用于影子研究和既有样例，不在 promotion 前生成正式产品档位。若晋级后采用，分档阈值由独立
`band_semantics_version` 冻结。修改阈值需要新主版本、历史口径说明、更新
`comparison_semantics_hash` 并同步提升 umbrella `formula_version`。分数为 `null` 时分档也必须为
`null`。

## 9. 盘中临时公式

盘中公式与盘后公式必须具有独立版本，例如 `behavior-intraday-v1` 与 `behavior-daily-v1`。

- 当前 10 分钟窗口只使用窗口结束前已经接收的数据；
- 快变量固定包括 regular/cumulative 同钟点相对成交、session OHLC/路径、session 波动、个股行业残差，
  以及市场/行业同钟点宽度；市场和行业盘中值分别使用 `MARKET_INTRADAY_CLOCK`、
  `INDUSTRY_INTRADAY_CLOCK`，不得复用全日总体；
- session VWAP 偏离与上方时间、开盘区间保留、日内回撤/修复、同钟点价格冲击和盘中上下半方差只进入
  六维状态支持/反证，不获得额外压力权重；
- `activity_anomaly`、`close_path_rejection`、`volatility_expansion`、`breadth_context` 和
  `idiosyncratic_divergence` 按当前完整窗口重新计算；
- `reference_cost_extension`、`short_path_extension`、`price_impact`、`spread_proxy` 和
  `downside_tail_asymmetry` 整簇继承当前交易日前最近一份公式兼容的确认 assessment；
- 同钟点相对成交同时保存比率和 mid-rank 分位，不能用全日均量线性外推；
- 每个继承簇记录 source assessment、版本、截至日和 lineage；缺失、过期、权利失效或公司行为/规则变化
  导致不可比时对应簇缺失，盘中总分 abstain；
- 当前窗口只与过去相同钟点比较；
- 集合竞价、午休、停牌、临时停市和未完成窗口不计算；
- 行情延迟超过水位目标时标记 `degraded`，不伪装为当前完整值；
- 盘中变化只和上一个 `intraday_10m` assessment 比较；盘后只和上一个 `post_close` assessment 比较；
- comparison 还必须具有相同 `comparison_semantics_hash`；首次基线或版本切换时 delta 为空、历史图断线
  且不形成 transition。
- 10 分钟窗口可能漏掉窗口内 VWAP 穿越、开板次数和瞬时成交顺序，产品必须标记粗粒度，不得用状态名
  暗示逐笔可见性。

## 10. 历史相似情景

只为盘后确认值计算。相似向量不再预先写死为 20 维；R4b 候选集为十个贡献簇，以及趋势组织、位置与
修复、成交参与、波动环境、相对环境五个盘后诊断连续坐标。盘中接受不进入盘后向量。analog 专用
冗余门禁可以删除任一候选坐标而不改变压力公式，但五个压力分面各至少保留一个代表；最终冻结为
8–14 维 `analog_feature_set_version`。数据库迁移只能在维度冻结后创建，新旧维度不混写。

候选必须：

- 早于当前 `as_of`；
- 同交易规则期、交易板块，且自由流通市值相近；行业语境由向量中的相对环境坐标表达；
- 在候选选择截止时间前已经存在；
- 1 日、3 日结果满足 `label_available_at <= current_selection_as_of`；候选 assessment 当日不应知道未来
  标签，选择时只要求标签在本次选择截止时间前已经成熟；
- 同一证券每 20 个交易日最多保留一个样本；
- 最多保留 200 个精确复核后的样本。

未来只有在独立 walk-forward 证明有稳定增量后，外部环境分类才可作为版本化候选筛选条件；它不得进入
相似向量距离、改写当前压力分或把历史结果转成当前方向概率。筛选条件变化必须升级
`analog_selection_semantics_version`，新旧样本不混算。

pgvector 只做候选召回。应用层按固定距离函数重算、验证 PIT、向量版本、规模距离和相邻行情片段去重。
有效样本少于 50 时返回“样本不足”，不输出分布区间。

## 11. 结果标签

对候选日 `t` 和 horizon `h ∈ {1, 3}`：

- 原始收益：`close[t+h] / close[t] - 1`；
- 行业超额收益：原始收益减去当时行业组合同期收益；
- MAE：窗口内相对候选日收盘的最小价格路径收益；
- MFE：窗口内相对候选日收盘的最大价格路径收益。

交易日、停牌、除权和不可成交窗口必须按当时规则处理并记录。输出中只展示样本数、中位数、25% 和
75% 分位，不计算上涨概率、命中率或目标价。

## 12. A 股特殊规则清单

实现前至少覆盖：

- 主板、创业板、科创板、北交所；
- ST 和风险警示切换；
- IPO 初期特殊涨跌幅和无涨跌幅限制期；
- 停牌、复牌、临时停市；
- 一字涨停、一字跌停和普通涨跌停；
- 自由流通股本变化；
- 分红、送转、拆并股及除权；
- 退市证券和历史行业成分。

必要规则、股本或公司行为缺失时必须 abstain。

## 13. 权利闸门

每次真实市场数据处理分别检查：

- `FETCH`：是否允许获取；
- `RETAIN`：是否允许保存原始或派生数据及保留期限；
- `DERIVE`：是否允许计算并保存特征、指数、相似向量和统计结果。

`DERIVE` 是未来对现有 `RightsOperation` 的兼容扩展，只有契约、来源配置、检查脚本、生产者、消费者、
测试和文档同时实施时才可加入代码。无授权时连接器失败关闭，CI 和演示只使用脱敏合成数据。

## 14. 快照可复现字段

未来每个 assessment 至少记录：

```text
formula_version / feature_set_version / rule_semantics_version / band_semantics_version
peer_semantics_version / reference_mapping_version / comparison_semantics_hash
indicator_definition_versions / diagnostic_semantics_hash / analog_feature_set_version
peer_universe_manifest_hash / reference_state_manifest_hash
data_manifest_id / data_manifest_hash
rights_snapshot_hashes
history_days / peer_count / reference_sample_count
tie_rule / missing_reason_codes
comparison_assessment_id / supersedes_assessment_id
market_data_lineage_refs
explanation_version / explanation_threshold
selected_driver_cluster_ids / selected_mitigator_cluster_ids
market_structure_profile_availability / dimension_availabilities
materialized_structure_states / structure_reason_codes / structure_support_and_counter_ids
```

`data_manifest_hash` 必须覆盖行情、股本、公司行为、交易规则、同行总体和参考状态全部输入 revision/hash。
相同 `instrument + mode + window_end + formula_version + diagnostic_semantics_hash + data_manifest_hash` 只能形成
一个逻辑快照。`diagnostic_semantics_hash` 的变化可以生成新的物化读图状态，但不能倒推改写旧压力值，也
不能在主分数 `comparison_semantics_hash` 未变化时伪造压力 delta 或分档 transition。该 hash 即使六维均
不可用也必须非空；纯诊断修订设置 `supersedes_assessment_id`，其压力 `comparison_assessment_id` 仍指向
上一不同窗口、同压力比较语义的 assessment，不得指向同窗口旧诊断 revision，也不得生成 outbox。

## 15. 离线评价

评价只回答“分数是否稳定地排序更脆弱的历史状态”，不评价方向概率。

- 压力等级对未来 1 日/3 日 MAE 的排序与单调性；
- 相对行业/规模匹配基准的 lift；
- 分数和分档稳定性；
- 十个贡献簇的滚动相关、父子依赖、VIF/条件数和 leave-one-cluster-out 增量；
- 数据覆盖率、abstain 率、过期率；
- 分档提醒负担；
- walk-forward 的 purge/embargo；
- 对重叠窗口使用 block bootstrap 或 Newey–West 置信区间。
- 盘面状态相对原 K 线/分时/MA/量柱的成对任务正确率和完成时间；
- 压力被误解为概率、诊断被误解为计分、stale/abstained 被误解为当前完整值的比例。
- 加入外部上下文筛选前后，检查压力排序本身保持不变，并单独报告样本覆盖和选择稳定性。

不使用方向准确率、Brier Score、ECE 或概率校准指标评价本指数。

## 16. 论文与设计依据

- Amihud, 2002：低频非流动性代理，`abs(return) / dollar volume`；
- Corwin & Schultz, 2012：日高低价估计价差；
- Grinblatt & Han, 2005：资本利得悬挂和处置效应代理；
- Chen, Hong & Stein, 2001：交易量、过去收益与负偏形态；论文明确其“crash”讨论不是负期望收益预测；
- Llorente et al., 2002：量价关系的含义取决于风险分担和信息交易语境。
- Yang & Zhang, 2000：使用 OHLC 和隔夜跳空的波动估计，作为波动口径交叉核验；
- RiskMetrics：EWMA 波动框架，衰减因子和期限必须在本项目数据上冻结；
- Lo, Mamaysky & Wang, 2000：图形可被系统化，但不构成普适预测保证；
- Sullivan, Timmermann & White, 1999：大量技术规则需要防数据窥探和多重检验。

官方/论文入口：

- <https://www.sciencedirect.com/science/article/pii/S1386418101000246>
- <https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2012.01729.x>
- <https://www.anderson.ucla.edu/documents/areas/fac/finance/prospect_momentum.pdf>
- <https://www.nber.org/papers/w7687>
- <https://www.nber.org/papers/w8312>
- <https://ideas.repec.org/a/ucp/jnlbus/v73y2000i3p477-91.html>
- <https://www.msci.com/documents/10199/5915b101-4206-4ba0-aee2-3449d5c7e95a>
- <https://www.nber.org/papers/w7613>
- <https://ideas.repec.org/a/bla/jfinan/v54y1999i5p1647-1691.html>

## 17. Promotion gate

R4A 固定顺序为：

1. 审计数据覆盖、PIT 可用性和 abstain 原因；
2. 实现 `simple_baseline_version`，只产生研究分，不产生正式档位；
3. 实现 `candidate_full_formula_version` 作为影子候选；
4. 对两版本做 walk-forward、市场阶段、冗余和反例比较；
5. 项目所有者批准后才创建 `promoted_formula_version` 和正式 0–100/分档语义。

候选公式只有在下列事项完成后才可晋级：

1. 逐项论文公式复核和引用；
2. golden vectors、性质测试和 TA-Lib 对照；
3. A 股规则/公司行为困难集；
4. PIT、修订和缺失路径测试；
5. 两类“高换手＋大涨”条件化样例；
6. 为实验复现冻结一份 candidate revision（不等于产品公式晋级）及其指标目录、父子依赖和六维盘面状态；
7. 同行覆盖、分粒度参照总体、冗余聚类、VIF 和 leave-one-cluster-out 通过；
8. 为同一轮 walk-forward 固定公式、同行总体、经验状态库和相似向量版本，禁止测试中途调参；
9. 用户读图成对任务与误解护栏达到实施规格阈值；
10. 权利、许可和数据保留审核；
11. 证明外部环境没有进入压力公式、比较总体或分档语义；
12. 候选覆盖率不明显低于基线，且各板块/市场阶段 abstain 原因可解释；
13. 无事件状态下抖动不劣于基线，1日/3日最大不利波动排序增量在 walk-forward 中稳定；
14. 冗余聚类、相关性和 leave-one-cluster-out 证明十簇没有重复计票；
15. 固定反例能区分高位拥挤与低位有效突破；
16. 用户在十秒内识别状态、驱动、数据质量和“不是概率”；
17. 不存在任一重大市场阶段明显退化；
18. 真实生产者落地后才登记统一指标。

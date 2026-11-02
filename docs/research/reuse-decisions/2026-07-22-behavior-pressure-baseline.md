# 交易结构压力能力复用决策

<!-- reuse-decision-id:behavior-pressure-baseline-2026-07-22 -->

- 日期：2026-07-22
- 负责人：repository owner / behavior CODEOWNER
- 状态：accepted planning baseline
- 最终决定：`adapt`
- 机器登记：`governance/reuse-decisions.json`
- 运行时状态：未实现；本轮没有新增依赖、制品或复制代码

## 问题与验收标准

为交易结构压力指数选择唯一实现路线。输入是授权的 A 股日线、10 分钟线、股本、公司行为、交易规则、
行业和市场总体；输出是可解释、PIT、可修订的五分面/十贡献簇状态和历史相似情景。首版只物化一个
主动关注标的，10 分钟窗口水位完成后 120 秒内结束，重复任务幂等，缺失核心输入时 abstain。全市场
和同行只用于可复用参照总体，不产生逐股产品快照。

方案必须许可清晰、可固定版本、离线可重放，不泄漏第三方类型，不破坏 rights、PIT、evidence、revision
和非概率产品语义。

## 检索范围

核验或比较了 TA-Lib Python、Polars、Microsoft Qlib、PostgreSQL/pgvector、scikit-learn、FinReport、
pandas-ta、vectorbt 和 mlfinlab 的官方仓库/许可证/维护状态及本仓库现有锁定依赖。同时检查了韭圈儿、
有知有行、StockCharts SCTR、Simply Wall St Snowflake 和 MarketSurge 的公开产品表达，仅作设计参考。

论文公式分别核对 Amihud、Corwin–Schultz、Grinblatt–Han、Chen–Hong–Stein 和 Llorente 等原始论文或
出版页。代码许可不覆盖上游行情；Choice、Tushare 或任何供应商仍需逐动作数据权利登记。

## 硬门槛

| 候选 | 初始快照 | 许可 | 结果 |
|---|---|---|---|
| TA-Lib Python | 官方仓库/PyPI reviewed 2026-07-22；实际采用前重新固定 | BSD-2-Clause | `adopt` planned；只负责标准技术原语/对照，先验收 wheel、hash 和 golden vectors |
| Polars | 当前 optional data lock | MIT | `adopt` existing；负责批量窗口、横截面和 Parquet，不拥有领域公式 |
| Microsoft Qlib | 当前 optional ML lock | MIT | `adapt`；只在隔离离线 PIT 研究，不进入在线关键路径 |
| PostgreSQL/pgvector | 当前基础依赖 | PostgreSQL License | `adapt`；只做相似候选召回，应用层精确距离和 PIT 复核 |
| scikit-learn | 当前 optional ML lock | BSD-3-Clause | `adopt`；离线近邻和基线核验 |
| FinReport | archived repository reviewed 2026-07-22 | MIT | `reference`；demo 且归档，不作为生产基础 |
| pandas-ta | current release/licence path unresolved | 待重新核验 | `reject`；硬门槛未过且与 TA-Lib 重复 |
| vectorbt | repository reviewed 2026-07-22 | Apache-2.0 + Commons Clause | `reject`；source-available 且与 Qlib 角色重复 |
| mlfinlab | public issue repository reviewed 2026-07-22 | all rights reserved | `reject`；不是可直接融合的开源生产库 |

产品参考不复制代码、数据、公式、文案或视觉资产。FinReport 的 MIT 代码也不复制；归档和 demo 属性使其
无法通过生产维护硬门槛。

## 决定与边界

唯一默认路线是：

1. 继续使用 Polars 处理批量滚动、横截面百分位和 Parquet；
2. 未来在固定 release/hash、供应链和 contract test 通过后采用 TA-Lib 标准原语；
3. 继续使用 PostgreSQL/pgvector 候选召回、scikit-learn 离线精确核验、Qlib 隔离 PIT 研究；
4. 项目自行拥有 A 股规则、分粒度 PIT 参照总体、mid-rank、Amihud/Corwin–Schultz/CGO/NCSKEW 的
   产品公式包装、十贡献簇合成、abstain、历史情景精确复核和分档状态机；
5. 第三方库不进入 contracts/ports/modules，TA-Lib 只能在未来 platform adapter 或验证测试中使用。

自研缺口的确切原因是：没有候选同时满足 A 股历史规则、每动作数据权利、双时态 revision、分粒度
参照总体、重复计票治理、缺失拒绝、条件化解释和“分数不是概率”的契约。项目不自研滚动引擎、
Parquet、向量索引或通用技术
指标库。

本轮只批准规划路线。TA-Lib 状态保持 selected，不进入 `pyproject.toml`/`uv.lock`；只有实际代码、锁
文件、制品 hash、测试和 THIRD_PARTY 同步后才能称为 present。

## 验证

本次完成仓库接口/指标检索、候选职责和许可初审、单一路线、planned Port/目录边界、公式引用清单、
失败状态和测试闸门。没有运行第三方代码、没有下载数据或制品，也没有执行真实性能基准。

未来验证必须包含：

- TA-Lib 与固定 OHLCV 样本的 golden-vector 对照；
- 论文公式的独立 golden vectors、性质测试、父子依赖和冗余聚类；
- Polars 批处理与纯 Python 小样本结果一致；
- pgvector 召回后精确距离/PIT 复核；
- 全市场同行基线每窗口一次、单主动标的/120 秒代表性基准；
- 目标平台 wheel/hash、SBOM、CVE 和退出演练。

## 供应链与归属

本轮没有新增包、修改 lock、复制代码、模型、数据、截图、文案或公式实现。现有 Polars、Qlib、
scikit-learn 和 pgvector 的实际版本继续由锁文件证明；TA-Lib 只有 selected 记录，不存在已采用版本。

实际引入 TA-Lib 时重新核验 BSD-2-Clause、C/Python 发行物、wheel 的 SHA-256、发布渠道、传递依赖和
目标 Python/平台支持，并同步 SBOM、NOTICE（如需）、`THIRD_PARTY.md`、复用机器登记和退出方案。

市场数据的代码/客户端许可与数据服务权利分开。任何真实输入只有在 rights registry 允许 FETCH、
RETAIN、DERIVE 后才能进入该能力。

## 退出与复审触发条件

TA-Lib/Polars/Qlib/pgvector/scikit-learn 大版本、许可证变化、归档、重大 CVE、发行渠道变化、wheel 无法
验证、性能目标持续失败、论文实现发现口径错误或数据条款变化时强制复审。

替换通用组件必须保持项目 contracts、两个 Repository Port、PIT、manifest 和历史 assessment 语义。
若 TA-Lib 无法通过供应链或 golden-vector 闸门，退出路线是保留项目纯函数语义并用已验证的最小标准
数学实现替代，不增加第二套并行指标库。

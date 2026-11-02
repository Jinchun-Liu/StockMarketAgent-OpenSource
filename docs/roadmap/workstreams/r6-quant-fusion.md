# R6：量化与融合实施规格

> 路线编号由原 R5 顺延为 R6。交易结构压力指数不是本阶段的概率预测，也不因本阶段存在而改变语义。

状态：规格 `approved`；交付 `blocked`，待 R5 合规退出、足量 PIT 数据与冻结文本特征生成器。
机器状态与阻塞码以 `governance/delivery.json` 中的 `R6` 为准。

## 用户结果

在严格 point-in-time 和纸面条件下，用户可选择查看经过校准的独立概率研究；若模型不能稳定优于单模
或未通过风险闸门，系统继续只做证据助手和交易结构状态压缩。

## 前置与非目标

- 有足量 PIT 行情、公司/行业历史映射、事件 revision、可交易性和结果数据；
- 文本特征生成器已冻结且可重放；R5 可以通过“无需启动 LoRA”或“冻结 LoRA champion”任一路径合规
  退出，R6 不要求 LoRA 已训练或合并，只要求实际使用的文本特征生成器具有固定版本/hash 和回放证据；
- 交易结构压力如被用作候选特征，必须使用冻结版本和严格 OOF，不能把当期结果回灌到历史分数；
- 不优化真实下单，不以收益截图替代校准、费用和置信区间，不反复查看最终保留集。

## TDD 与研究顺序

1. 先写 future-data、修订、成分、退市、复权和重叠标签泄漏测试；
2. 建立 purged rolling split、5 日 embargo、交易成本和不可成交模拟；
3. 训练 LightGBM 价格 binary 与 q10/q50/q90 单模；
4. 只用 OOF、冻结文本特征训练文本单模；
5. 只在 OOF 概率上训练 L2 Logistic 融合，并在独立时间段校准；
6. 进行单模/融合消融、block bootstrap 和一次性最终 6 个月保留集；
7. 通过后冻结代码、数据源、模型、prompt、rule、阈值和成本运行 60 个交易日。

## 验收

报告 Brier、log loss、ECE、coverage、Rank IC、费用后表现、回撤和分层稳定性；ECE ≤0.05，q10–q90
coverage 在 75%–85%；融合改善有 block bootstrap 证据；所有样本只使用决策时可见数据。

这些概率指标只评价 R6 预测，不用于评价交易结构压力指数。

## 失败与降级

任一泄漏、校准或稳定性闸门失败时保持 `ENABLE_DIRECTIONAL_FORECAST=false`；数据缺失时不让 LLM
补数；60 日冻结期内影响预测的变更创建 challenger 并重新计时，不修改 champion 结果。

## 接口、指标与标准同步

复用现有 forecast/fusion/outcome Port 和 paper-only feature gate，不新建压力预测接口。只有真实离线
producer 和研究页面消费方存在时才登记/提升接口与 Brier、ECE、coverage、drift 等 R6 指标；这些指标
不得复用为 R4 压力质量。实施同步 PIT 数据 manifest、模型/特征版本、复用登记、锁文件、研究报告、
回滚开关和完整测试。

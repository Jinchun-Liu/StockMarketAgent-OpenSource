# 统一指标与演进标准

<!-- governance-doc:metric-standards:v1 -->

本规范覆盖运行时可观测指标、数据质量指标、产品质量指标和 ML/评估指标。它与
`interface-standards.md` 共同约束跨团队语义：代码、Dashboard、告警、验收文档和模型报告不得各自
发明同名异义或异名同义的指标。

## 1. 单一事实源与已有定义优先

`governance/metrics.json` 是统一指标目录。新增或修改任何指标前必须依次执行：

1. 搜索 `governance/metrics.json` 的 ID、名称、owner、description、unit、labels 和 `semantic_key`；
2. 搜索代码、Dashboard、告警、runbook、验收标准、数据/ML 文档和历史决策中的等价计算；
3. 能通过已有指标、已有标签或派生查询表达时，必须复用，禁止仅因调用方或页面不同另建指标；
4. 确认没有语义等价项后，才可登记新指标，并记录为什么已有定义不能满足；
5. 修改已有指标前完成生产者、消费者、历史序列、阈值、Dashboard、告警和评估基线影响分析。

禁止擅自改变已登记指标的计算公式、样本总体、聚合窗口、单位、标签、缺失值、分母、方向或阈值语义。
这些变化即使保留原名称也属于破坏性变化，必须发布新版本/新名称，并提供迁移窗口。

## 2. 指标分类

| 分类 | 用途 | 示例 |
|---|---|---|
| `runtime` | 服务、任务和依赖的吞吐、延迟、错误、积压 | 请求延迟、任务失败数 |
| `data_quality` | 新鲜度、完整性、来源缺口、去重和修订质量 | 来源延迟、字段缺失率 |
| `product_quality` | 简报、通知、检索和证据链的质量 | 重复通知率、引用有效率 |
| `ml_quality` | 训练、离线评估、校准、漂移和冻结纸面运行 | Brier score、区间覆盖率 |

一个指标只有一个 owner 和一个规范定义。多个 Dashboard 可以消费同一指标，但不得复制计算规则。
在线监控与离线评估可使用不同存储，仍必须共享规范 ID、定义、单位和版本。

## 3. 登记字段

每个登记项至少包含：

- 稳定 `id`：`metric:<domain>.<name>:v<major>`；
- 导出 `name`：Prometheus 风格 `stock_assistant_<domain>_<name>`；
- `semantic_key`：描述“总体/事件/计算/窗口/单位”的稳定机器键，用于发现异名同义项；
- `category`、`type`、`unit`、`owner`、中文或英文完整 `description`；
- 有界 `labels`、允许的标签值来源、直方图 buckets（如适用）；
- `status`、实现位置、Dashboard/告警消费者和替代关系；
- 对 ML 指标额外记录数据切分、horizon、样本过滤、方向和置信区间方法。

名称只描述观测量，不嵌入易变化阈值。单位使用基本单位：秒、字节、items 或 0–1 ratio；不得在同一
序列中混用毫秒/秒、百分数/比例。counter 使用 `_total`，seconds/bytes 等单位进入名称。

## 4. 标签与基数

- 标签值必须来自受控枚举或有界目录；禁止 `request_id`、`correlation_id`、用户、持仓、证券代码、
  URL、原始错误文本、prompt、evidence ID 等无界或敏感标签。
- 实体级诊断进入结构化日志/trace，不进入指标标签。
- 新标签必须给出最大基数估算和聚合需求；能由现有标签组合查询时不得新增。
- 错误使用稳定原因码，不使用异常文本。模型标签只允许受控的逻辑别名，不暴露本地路径或私有版本。

## 5. 版本、兼容与历史

以下为兼容变化：补充说明、增加 Dashboard、在不改变序列语义的前提下调整实现性能。

以下为破坏性变化：改变计算、样本总体、单位、类型、标签集合/含义、bucket、窗口、缺失值处理、
分母、正负方向或归一化。破坏性变化必须：

1. 新建 `v<major+1>` ID 和新导出名称；
2. 在旧项记录 `deprecated`、`replaced_by` 和停止日期；
3. 在兼容窗口双发并对比差异；
4. 同步 Dashboard、告警、runbook、验收阈值、数据/ML 文档与测试；
5. 保留旧序列说明，禁止把历史数据静默按新公式解释。

## 6. 接口与指标共同变更

接口定义观测对象，指标定义其可观察行为。新增/修改 HTTP、任务、事件、Port、模型输出或数据管道时，
必须先检查已有接口和已有指标，随后检查是否影响：

- `governance/interfaces.json` 与 `governance/metrics.json`；
- Pydantic contract、生成 schema、OpenAPI、任务/事件名；
- 指标生产者、Dashboard、告警、SLO、runbook；
- 数据口径、ML 基线、验收阈值和 point-in-time 语义；
- 契约、注册表、兼容、降级和历史可比性测试。

“检查同步”不等于无意义地修改每个文件；未受影响的标准保留原文，但工作包 handoff 必须逐项说明
检查结果。受影响内容必须在同一改动中更新，禁止拆分为“先改代码、以后补标准”。

## 7. 机器检查与验收

运行：

```bash
python3 scripts/check_metric_registry.py
python3 scripts/check_interface_registry.py
```

指标检查器验证注册表格式、ID/名称/semantic key 唯一性、类型/单位、标签基数规则、直方图 buckets、
状态和实现引用。它不能判断两个不同公式是否在业务上等价，因此语义检索、影响分析，以及
PAE 负责指标语义与注册，QIR 负责运行时生产者、集成验证和里程碑发布。部门仅表示协调职责；真实
写权限仍由当前轻量工作包和单写区决定。

# 本地模型部署与两阶段 LoRA 训练

## 1. 结论与硬件基线

唯一基座模型是 [Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)，采用 Apache-2.0 许可。本地推理由 [vLLM](https://docs.vllm.ai/en/stable/) 提供 OpenAI-compatible API；训练以
[ms-swift](https://github.com/modelscope/ms-swift) 作为唯一高层运行器，底层使用 Transformers、PEFT、
TRL、bitsandbytes 和 Accelerate。LlamaFactory 不作为并行训练路线，自研 Trainer 只允许补齐已有
复用决策记录确认的局部缺口。

当前工程基线假设：

- Linux x86_64 + NVIDIA CUDA；
- 单卡至少 24GB 显存；
- 系统内存至少 64GB；
- 可用 SSD 至少 500GB；
- 训练 BF16，QLoRA 权重 NF4，gradient checkpointing 开启。

实际硬件未确认前不下载权重、不承诺 batch size 或训练时长。首个硬件闸门是 100 条样本、2K 序列
的单卡 QLoRA smoke test；8K 推理上下文也需实测，不能用模型卡最大上下文代替本机结论。
Qwen3.5-9B 含多模态能力，本项目训练集是纯文本；训练时冻结视觉塔和投影层，只把语言主干的线性层
列入 LoRA 目标。模块清单由版本固定后的模型结构检查脚本生成并保存哈希，不用模糊名称误训视觉参数。

## 2. LoRA 能解决与不能解决的事

LoRA 负责：

- 金融任务指令遵循；
- JSON/Markdown 格式稳定；
- 事件类型、状态、方向与事实槽位的领域表达；
- 证据不足时拒答；
- 常见中文公告结构和术语处理。

LoRA 不负责：

- 获取今天的公告与新闻；
- 保证事实正确或消除幻觉；
- 证明次日涨跌因果；
- 绕过数据授权；
- 直接“修正”量化概率。

实时知识来自检索，事实正确性来自引用、schema 和确定性校验。原设想“减少 40% 以上幻觉”不作为承诺，只在固定金标集上报告相对基线、置信区间和错误类型。

## 3. 数据治理

每个数据集发布目录包含：

```text
dataset_card.md
manifest.parquet
rights_snapshot.json
schema.json
train.jsonl / validation.jsonl / test.jsonl
dedup_report.json
label_guideline.md
adjudication_log.csv
```

manifest 每条记录至少有：来源类型、source ID、event cluster、published/first_seen、权利哈希、生成方式、标注者、质量状态和内容哈希。

### 允许数据

- 人工编写的任务模板、拒答样例和困难负例；
- 自有合成样本，经人工核验后使用；
- 合同明确允许训练的数据；
- 经法务/权利复核允许使用的正式政府文件；
- 公告中人工抽取的结构化事实与必要短证据位置。

### 禁止数据

- 未经训练授权的财经新闻、券商研报和股吧全文；
- 训练/验证之间的同一事件转载或修订；
- 含持仓、成本、账户、个人笔记或 API key 的数据；
- 由未来行情或事后新闻反向写出的“当时理由”；
- 不可追溯的大模型批量伪标签。

## 4. 任务 schema

训练不是让模型自由写研报，而是覆盖明确任务：

1. `event_extract`：事件类型、状态、主体、数值、期间、证据；
2. `sentiment_assess`：方向、期限、重要性、置信度和理由；
3. `source_compare`：新增、重复、更新、更正、冲突；
4. `portfolio_brief`：多来源持仓简报；
5. `policy_map`：政策到行业/地区/公司的关联与不确定性；
6. `grounded_qa`：只依据证据回答；
7. `abstain`：证据不足、冲突、过期或权限不足时拒答；
8. `format_repair`：把语义正确但 schema 非法的输出修复为合法 JSON。

所有事实输出都带 `evidence_id`；数值事实同时带 `evidence_span`。训练集把“事实、推断、概率预测”分成不同字段。

## 5. 阶段 1：指令、格式和证据遵循

### 数据规模

从 1,000–3,000 份获准使用的公告/文档构造 6,000–10,000 个任务样本。至少 500 个完全人工审核的事件簇作为金标测试集，永不参与训练、超参选择或合成提示调试。

### 样本构成

| 类型 | 比例 | 重点 |
|---|---:|---|
| 事件/事实抽取 | 30% | 数值、日期、主体、状态 |
| 固定 JSON/Markdown | 20% | schema 和字段完整性 |
| 多来源简报 | 15% | 引用、变化、去重 |
| 拒答与冲突 | 20% | 无证据、旧闻、同名公司、冲突数字 |
| 政策相关性 | 10% | 地区/行业/适用范围 |
| 格式修复 | 5% | 只修结构，不补事实 |

困难负例必须覆盖：否定词、单位变换、报告期与公告日混淆、拟/已完成、1%/10%、同名公司、撤回/终止、更正公告、网页 prompt injection、旧闻改标题重发。

### 起始训练配置

配置是 smoke test 起点，不是未经验证的最优值：

```yaml
method: QLoRA
quantization: NF4
compute_dtype: bfloat16
lora_rank: 32
lora_alpha: 64
lora_dropout: 0.05
target: language_backbone_all_linear
gradient_checkpointing: true
optimizer: paged_adamw_8bit
learning_rate: 0.0001
epochs_max: 3
early_stopping: validation_composite_metric
seed: [17, 31, 73]
```

训练先用 100 个样本完成 overfit/smoke test，再用 5% 数据做端到端试跑。batch size、gradient accumulation、sequence length 根据实际 24GB 显存测量后锁定。至少运行 3 个 seed，避免把偶然波动当提升。

### 阶段 1 闸门

- JSON Schema 合法率 ≥ 99.5%；
- 输出引用 ID 存在率 100%；
- 数字与证据一致率 ≥ 99%；
- 证券代码/主体一致率 ≥ 99%；
- 应拒答样本召回率 ≥ 95%，且正常回答误拒率可接受；
- 相比未微调基线，无证据事实率的 95% bootstrap 置信区间显示实质下降；
- 所有高严重度错误人工复核为 0 才能进入阶段 2。

## 6. 阶段 2：金融事件理解

### 数据规模与标签

构建 20,000–50,000 个事件任务，覆盖财报、业绩预告、减持、回购、处罚、诉讼、订单、重组、停复牌、分红、融资、政策及行业影响。

标签包含：

- 事件类别和阶段；
- 主体、对象、金额、比例、期间；
- 已发生事实、管理层预计、第三方观点；
- 方向、影响期限、重要性和置信度；
- 证据位置、来源等级和冲突状态。

阶段 2 从通过闸门的阶段 1 adapter 继续训练，同时混入 30% 阶段 1 replay 样本，防止 schema、引用和拒答能力退化。

### 数据划分

- 先按事件簇去重，再按时间划分；
- 同公司同事件的修订不得跨 train/test；
- 最新时间段作为测试，不能随机打散；
- 标注规范变更后要重跑一致性检查；
- 至少 10% 双人标注，对分歧进行 adjudication 并报告一致率。

### 阶段 2 闸门

- 事件分类 macro-F1；
- 主体/金额/比例/期间槽位 micro-F1；
- event status 与否定/取消识别 F1；
- 方向与重要性按事件类型的混淆矩阵；
- 引用、数字和拒答指标不得低于阶段 1；
- 在未见公司、未见行业和市场状态子集上无显著崩溃。

具体阈值在完成第一版人工金标后冻结，不能为了让新模型“通过”而在看完测试结果后修改。

## 7. 为什么不把“新闻 + 次日涨跌”直接做生成式 SFT

次日涨跌是高噪声结果，不证明新闻的因果方向。让 LLM 学习“给定新闻生成次日涨跌”容易产生事后归因、夸大置信度和未来泄漏。

本项目只让 LLM 抽取可审计事件特征；涨跌标签进入独立文本影响模型和融合模型，并按 `first_seen_at` 做滚动样本外训练。这样可以分别评估文本抽取质量与市场预测价值。

## 8. 模型登记与可复现

MLflow run 必须记录：

- base model repo、revision、文件 SHA-256 和许可证；
- dataset manifest/hash、rights hash 和拆分版本；
- 代码 commit、容器 digest、CUDA/PyTorch/ms-swift/Transformers/PEFT 版本；
- 完整训练参数、seed、GPU、耗时和能耗；
- adapter hash、prompt/template hash、规则版本；
- 全部评估、分组指标和错误样例索引。

只有通过闸门的模型获得 `candidate`；人工签署后升级为 `champion`。上一 champion 永久可回滚。

## 9. 部署与合并

生产优先保留独立 LoRA adapter，vLLM 启动时静态挂载。这样模型可回滚、A/B、复核和更换。

若最终确需合并：

1. 载入 BF16/FP16 原始基座；
2. 用 PEFT `merge_and_unload()` 合并；
3. 跑完整 schema、引用、数字、拒答与性能回归；
4. 生成新的模型卡和哈希；
5. 最后重新做受支持的推理量化。

不把 LoRA 直接“合并到已经量化的基座”作为标准流程，因为这会降低可审计性并可能引入数值误差。

## 10. 推理安全

- 温度固定 0，所有关键任务要求 JSON Schema；
- 最多两次格式修复，不能在修复时补充证据外事实；
- 证据包限制来源、实体、时间和权限，网页指令作为纯文本引用；
- 输出校验器回查证券代码、日期、数字、单位和 evidence ID；
- 失败时降级为原文列表或“无法确认”，不降级为无约束自由生成；
- 对每个模型版本保持 canary 金标回归和每日抽检。

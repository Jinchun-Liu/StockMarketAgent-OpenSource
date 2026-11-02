# R2 记忆分层、事件去重与生命周期

## 0. R2 强制记忆边界

R2 只把稳定宏观参考知识视为长期语义记忆。`macro_reference_facts` 是唯一长期语义记忆表，数据库
约束只允许稳定定义、制度关系、稳定分类、版本化解释规则和已确认历史事实；禁止写入个股事实、当前
宏观数值、预测、模型结论或会话 embedding。

个股研究按三种数据目的隔离：

- 原始 evidence、事件 revision、rights snapshot 和审计记录按权利/PIT策略保留；它们是证据系统，
  不是模型长期记忆；
- 报告、主张、模型中间文本、临时上下文和 embedding 绑定 `session_id + expires_at`，最长七天；
- 删除或到期立即终止正文访问，清理器在二十四小时内移除报告正文、临时上下文、embedding 和对象
  制品，只保留最小 tombstone、任务回执、内容 hash 和证据索引。

现有 `EpisodicMemoryPort` 只保留兼容声明，不绑定 R2 运行时，也不得新增长期个股情景记忆。下面的事件
去重、PIT 和证据审计规则仍适用；原“三层记忆”描述只用于 R1 后续领域历史，不得覆盖本节。

全球国际事件事实、raw evidence、revision 和 rights snapshot 属于长期 PIT/审计数据，不属于
`macro_reference_facts` 或模型长期语义记忆。事件与用户股票的暴露匹配、方向推断和模型上下文必须
绑定 `session_id + expires_at`，随会话七日清理。

## 1. 核心原则

“记忆”描述检索策略，不描述数据是否删除。用户提出的 3–7 天窗口用于近期事件比较；如果把预测、建议和证据一周后删除，就无法审计通知、评估模型或发现长期修订。

因此系统把工作集、检索窗口和合规保留期分开管理。

## 2. R1 后续三层记忆设想（不绑定 R2）

交易结构压力 assessment 不属于三层记忆中的“文本情景记忆”。它是带公式、数据 manifest、rights 和
PIT revision 的结构化领域记录，未来由独立 `BehaviorPressureRepositoryPort` 管理。pgvector 只为历史
相似状态召回使用由十簇加五个盘后诊断候选经冗余门禁冻结的 8–14 维向量；盘中接受不进入盘后
首版。不能把 assessment 写入 `EpisodicMemoryPort`，也不能与文档 1024 维 embedding 混表。贡献簇、
必需六槽盘面 profile、相似向量和结果均是结构化 assessment 子记录。
Valkey 只缓存最新结果，分档连续状态和提醒抑制留在 PostgreSQL。

隔夜外部环境、公司暴露和开盘反馈同样不是文本情景记忆。未来它们分别作为结构化外部快照、
`ExternalImpactAssessment` 和 `PostOpenFeedback` revision 保存；事实事件 revision 不因主动标的切换或
推断规则变化而改变。

### 短时记忆：Valkey

保存：

- 当前会话主动标的 revision、可选脱敏仓位快照与对话摘要；
- 本轮采集候选、尚未完成的 evidence bundle；
- 源游标的临时锁、限流桶、熔断状态；
- 推送冷却、幂等键和 SSE 连接状态。

TTL 为 24–48 小时，任务成功后可提前释放。Valkey 丢失只影响短时效率，不能导致事实、预测或审计丢失。

### 情景记忆：PostgreSQL + pgvector（R2 禁用）

以下为历史规划，不得写入 R2 长期记忆：

- `raw_documents` 与修订链；
- `event_clusters / market_events / event_claims`；
- 许可范围内的 1024 维 embedding；
- 历史预测、真实结果和当时建议；
- 通知、确认状态和完整生成元数据。

R2 不建立该 90 天个股向量窗口；其会话向量最多保留七天。原始证据可按来源合同保留，但不得作为
模型长期记忆检索。

### 语义记忆：Git 规则库

保存事件分类、来源等级、行业/地区/公司映射逻辑、利好利空候选词、否定/条件/取消词、金额阈值、交易规则和推送规则。

关键词只召回候选。例如“公司不存在减持计划”同时出现“减持”，但否定词和事实槽位必须让它保持非利空；“拟回购”与“已完成回购”也必须处于不同状态。

每份规则包含 `version/effective_from/change_reason`，同步到数据库后生成内容哈希。历史预测必须使用当时规则版本。

## 3. 标准事件结构

事件最少包含：

```text
event_id / cluster_id / revision
security_ids / company_ids / region_ids
event_type / event_status / direction
materiality / impact_horizon / confidence
event_time / published_at / first_seen_at
facts[] / evidence[]
extractor_version / rule_version
```

每个 fact 包含 `name/value/unit/period/evidence_id/evidence_span`。缺少证据位置的数字不能进入事实层。

跨境影响关系不嵌入上述事实字段。它必须另外引用事件、证据、源/目标节点、传导渠道、暴露直接度、
暴露档案版本和推断规则版本，并分别保留支持与反证。

## 4. 五步去重和变化识别

### 第一步：规范化

- 删除 URL 跟踪参数，保留稳定来源 ID；
- 统一 Unicode、全半角、空白、公司简称、证券代码、日期和金额单位；
- 区分“万元/亿元”“同比/环比”“报告期/公告日”；
- 保存原始哈希和规范化哈希，规范化文本不覆盖原文。

### 第二步：精确去重

按以下唯一键依次判断：

1. `source_id + source_document_id`；
2. 规范化 URL；
3. 正文 SHA-256；
4. 规范化正文 SHA-256。

命中时更新采集观测，不创建新事件，也不删除已有来源记录。

### 第三步：同事件候选召回

在“相同主体 + 相近事件类型 + 72 小时窗口”内执行：

1. pgvector 取 top-k 文档/事件候选；
2. Qwen3-Reranker 对标题、事实槽位和短证据片段重排；
3. 只有候选分达到经标注集确定的阈值，才进入同簇比较。

不能拍脑袋固定余弦阈值。至少构造 1,000 对中文财经重复/非重复金标，以“重复 precision ≥ 99%”选阈值，宁可漏合并，也不能把实质变化压成旧闻。

### 第四步：事实槽位比较

按主体、事件类型、状态、期间、金额、比例、对手方和方向比较，输出：

- `NEW`：没有对应事件簇；
- `DUPLICATE`：事实和状态没有实质变化；
- `UPDATE`：新增进展或关键槽位变化；
- `CORRECTION`：来源明确更正；
- `CONTRADICTION`：可信来源之间关键事实相冲突。

仅靠 embedding 会把“减持 1%”与“减持 10%”误判相同，所以数值和期间必须确定性比较。

### 第五步：物化变化与推送

重复文档作为 cluster member 保留，用于交叉印证和来源覆盖统计，但不推送。更新事件创建新 revision，并生成关键槽位 diff。

通知幂等键：

```text
sha256(owner_id + event_cluster_id + material_revision + channel)
```

同一个 revision 即使 worker 重试也只发一次；后续真正的进展使用新 revision，可以再次通知。

## 5. 来源冲突

冲突不能用简单权重平均消失：

1. 法定公告与媒体冲突，以公告确认公司披露事实，媒体作为待核验说明；
2. 两份法定来源冲突，标记 `CONTRADICTION` 并等待更正，不让 LLM自行选择；
3. 研报与公告方向不同，不是事实冲突，分别记录“事实”和“分析师观点”；
4. 股吧与任何正式来源冲突时只降低舆情可信度，不改变事实；
5. 更正公告到来后关闭旧 revision 的 `valid_to`，但保留旧版本和当时通知审计。
6. 战争或外交事件的相反官方声明并列保存为 `CONTRADICTION`；系统不把任一行为方声明自动等同于已
   独立核实的事实。
7. 制裁、出口管制或央行文件的后续更正生成新事实 revision；旧外部快照和旧传导路径继续引用当时版本。
8. 公司暴露变化只生成暴露/影响 revision，不覆盖原始事件，也不能由新闻共现升级为供应关系。

## 6. 时间与防泄漏

回测查询必须加：

```sql
WHERE first_seen_at <= :decision_time
  AND valid_from <= :decision_time
  AND (valid_to IS NULL OR :decision_time < valid_to)
```

同一新闻簇在划分训练/验证/测试之前先聚类，避免转载跨集合。模型、规则或实体映射的今天版本不得回填过去。

外部历史查询还必须同时冻结交易会话版本、08:55 截止、目标激活 revision 和暴露档案版本；08:55 后
到达的事实或供应商修订不能改变用户 09:05 实际看到的简报。

## 7. 生命周期

| 数据 | 热窗口 | 默认保留 | 删除依据 |
|---|---:|---:|---|
| Valkey 会话/候选 | 24–48 小时 | TTL | 任务完成与隐私策略 |
| R2 会话文本/embedding | 最长 7 天 | 7 天 | 会话到期或用户提前删除 |
| 授权新闻/研报原文 | 3–7 天 | 按合同 | 权利记录 |
| 政府正式文件 | 90 天热 | 长期 | 法律复核 |
| 事件与事实槽位 | 90 天热 | 长期 | 审计策略 |
| R2 模型结论 | 最长 7 天 | 7 天 | 禁止进入长期语义记忆 |
| 稳定宏观参考事实 | 版本化 | 长期 | 仅 `macro_reference_facts` 约束允许的类型 |
| 通知与生成元数据 | 90 天热 | 长期 | 审计策略 |

清理任务只能删除权利到期或超出许可的对象内容；先保留文档元数据和哈希，再重建 embedding 索引。任何物理删除都写审计日志。

## 8. 候选观测问题（尚未登记指标）

以下内容只说明未来需要回答的问题，不定义稳定指标名、公式或标签。首个真实生产者出现时必须先在
`governance/metrics.json` 登记，再由本文引用 metric ID：

- exact duplicate rate / cluster duplicate rate；
- `UPDATE/CORRECTION/CONTRADICTION` 比例；
- 人工抽检的去重 precision/recall；
- 重复推送率和漏推重大更新率；
- 每个来源首次看到延迟；
- 事件事实引用覆盖率；
- 向量召回后 reranker 分布和阈值漂移；
- 规则版本变化前后的事件分类回归。

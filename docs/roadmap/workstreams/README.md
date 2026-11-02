# 独立实施规格索引

本目录把 `docs/roadmap/implementation-plan.md` 拆成可独立执行和验收的工作流。路线图仍是阶段顺序与范围的唯一事实源；这里不重新定义公共接口、统一指标或产品边界。
工作流的机器状态、依赖、唯一能力所有权和允许激活条件统一登记在
`governance/delivery.json` (upstream private record, excluded)；本表只做阅读索引。

| 规格 | 结果 | 状态 |
|---|---|---|
| [`r0-environment-and-governance.md`](r0-environment-and-governance.md) | 环境诊断、合成简报、实施纪律 | 已完成（当前基线） |
| [`r0p-parallel-delivery-foundation.md`](r0p-parallel-delivery-foundation.md) | 契约冻结、迁移一致性、worktree隔离与CI硬闸门 | `verified`：本地、云端、远端历史和 revert 恢复证据已封存 |
| [`r1-premarket-vertical-slice.md`](r1-premarket-vertical-slice.md) | 固定兆易创新、原创合成五类覆盖、多源冲突和可下钻简报 | `completed_in_synthetic_environment`；只作冻结兼容，不代表生产就绪 |
| [`r2-multi-source-memory.md`](r2-multi-source-memory.md) | 搜索 A 股、全球事件库按需匹配、证据硬门报告与七日生命周期 | `in_progress`；待最终合成门禁，四项生产发布门禁仍阻断 |
| [`r2-external-context.md`](r2-external-context.md) | 曾拆分的外部上下文方案 | 历史术语说明，不是活动阶段或依赖节点 |
| [`r3-assistant-experience.md`](r3-assistant-experience.md) | 会话内证据问答、报告解释和有限历史比较 | 后续规划；不实现今日页、inbox、SSE、通知或长期 watchlist |
| [`r4-behavior-pressure.md`](r4-behavior-pressure.md) | 用户发起研究时计算 PIT 市场结构快照 | 后续规划；不持续监控个股或发送档位提醒 |
| [`r5-local-llm-and-lora.md`](r5-local-llm-and-lora.md) | 本地模型基线和条件式 LoRA | 规格 `approved`；交付 `blocked`，允许“无需启动 LoRA”合规退出 |
| [`r6-quant-fusion.md`](r6-quant-fusion.md) | 独立概率研究、融合和冻结纸面验证 | 规格 `approved`；交付 `blocked`，待稳定文本生成器与 PIT 数据，不要求 LoRA 完成 |

每份规格都必须包含：用户结果、非目标、前置闸门、复用/接口/指标检查、TDD 顺序、验收场景、降级或回滚，以及标准同步清单。执行时只把当前工作流标为进行中，不并行宣称后续阶段完成。旧
`r4-local-llm-and-lora.md` 与 `r5-quant-fusion.md` 仅保留兼容链接，不再作为当前规格。

规格不是可直接并行的 lane。跨模块实现必须先按
[`parallel-development-protocol.md`](../../engineering/parallel-development-protocol.md) 冻结契约，
由 PAE 冻结契约包，QIR 再把当前 workstream 拆成写范围互斥的轻量工作包。

六个责任域、短生命周期分支和 R1 并行波次统一引用
[`implementation-plan.md`](../implementation-plan.md)。新包只登记 base/contract SHA、精确范围、依赖
和聚焦测试；不创建 control/attempt/seal/package tag，也不要求远端工作分支。legacy refs 保持不变。

多股票组合、全市场逐股新闻和 weighted-fair 板块轮动属于后续扩展轨道，不在当前 R0–R4 前期闭环中。

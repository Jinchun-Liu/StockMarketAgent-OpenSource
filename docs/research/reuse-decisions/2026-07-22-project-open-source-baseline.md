# 项目开源基线复用决策

<!-- reuse-decision-id:project-oss-baseline-2026-07-22 -->

- 日期：2026-07-22
- 负责人：repository owner / architecture CODEOWNER
- 状态：accepted
- 最终决定：`adapt`
- 机器登记：`governance/reuse-decisions.json`
- 关联文档：[`open-source-evaluation.md`](../open-source-evaluation.md)、
  [`ADR-0005`](../../decisions/0005-reuse-first.md)、
  [`ADR-0007`](../../decisions/0007-permissive-storage-baseline.md)

## 问题与验收标准

为 A 股研究助手选择唯一工程底座与各功能实现。要求许可身份明确、可固定版本、支持本地部署和退出，
且不能破坏来源权利、evidence、point-in-time、通知幂等和 paper-only 边界。

## 检索范围

核验 GitHub 官方仓库/Release/LICENSE/SECURITY、Hugging Face 官方模型卡及官方文档。比较了股票 Agent、
数据平台、文档解析、缓存/对象存储、RAG/向量、训练/推理、量化、通知、前端和可观测项目。完整候选、
评分和证据链接见主调研文档。

## 硬门槛结果

| 候选 | 精确快照 | 许可证 | 结果 |
|---|---|---|---|
| daily_stock_analysis | v3.27.0 / `b36c721` | MIT | `reference`；数据权利、证据修订、三层记忆和 PIT 融合不足 |
| OpenAshare | v0.1.0 pre-release / `fed8a10` | MIT | `reference`；工作台高度重合，但发布和治理成熟度不足 |
| Vibe-Trading | v0.1.11 / `835dea7` | MIT | `reference`；交易、shell 和广权限工具路径必须排除 |
| AlphaSift | main / `9f522747caaf` | Apache-2.0 | `reference`；板块/热点可做 Port spike，免费数据和 T+N 语义不照搬 |
| TradingAgents | v0.3.1 / `01477f9` | Apache-2.0 | `reference`；只借鉴角色、checkpoint 和防前视测试 |
| TradingAgents-CN | 2026-07-22 仓库快照 | 混合，`app/frontend` 专有 | `reject`；核心路径需要独立授权 |
| OpenBB | 2026-07-22 仓库快照 | AGPL-3.0 | `reject`；重复 provider 边界且不能解决 A 股数据权利 |
| Valkey | `9.1.0-alpine` / `sha256:c9b77919daeba2c02ad954d0c844cc4e7142069d177b89c5fd771f405daf9e02` | BSD-3-Clause | `adopt`；R0P 缓存/Celery broker，生产恢复仍受闸门约束 |
| SeaweedFS | `4.40` / `sha256:52194fba4fecd0083c842158b3a902ba6e04a63619b2b0efcd08007bdb6a4602` | Apache-2.0 | `adopt`；R0P 对象存储，S3 保留/校验/恢复仍受闸门约束 |

数据连接器方面只允许正式 CNINFO/交易所/Choice/政府 adapter 进入生产；AKShare 仅开发验证。通用组件
唯一目标为 PostgreSQL/pgvector、Valkey/Celery、SeaweedFS、Docling/PaddleOCR、RapidFuzz/datasketch、
LangGraph、Qwen/vLLM/ms-swift、Qlib/LightGBM/scikit-learn、DVC/MLflow、Apprise 与 OpenTelemetry。

## 评分

本表按“能力 40 + 工程 20 + 许可 15 + 权利/安全 15 + 集成 10”评价复用适用性，不是主调研按十项
产品需求计算的整体匹配分。

| 候选 | 能力 40 | 成熟度 20 | 许可 15 | 权利/安全 15 | 集成 10 | 总分 |
|---|---:|---:|---:|---:|---:|---:|
| daily_stock_analysis | 30 | 18 | 15 | 4 | 8 | 75 |
| Vibe-Trading | 29 | 19 | 15 | 3 | 7 | 73 |
| OpenAshare | 30 | 10 | 15 | 4 | 8 | 67 |
| TradingAgents | 20 | 17 | 15 | 6 | 8 | 66 |
| AlphaSift | 16 | 16 | 15 | 5 | 8 | 60 |

硬门槛失败的候选不以总分覆盖拒绝结论。

## 决定与边界

不整体 fork。唯一默认路线是保留本项目 contract/Port/领域规则，并在每个通用能力上只适配一个成熟
组件。应用、Agent/PIT、板块分别以 daily_stock_analysis、Vibe-Trading、AlphaSift 为首要参考；
OpenAshare 仅作本地工作台交互次级参考。所有第三方类型不得越过 contract。

自主实现的最小缺口仅包括 rights gate、PIT 主数据、证据/事件修订、政策时态映射、差异通知、
purged grouped split 和融合质量闸门。公平板块轮换属于当前单主动标的范围之后的延期缺口，不进入
R0–R6 活动工作流。当前依赖与选定待引入项见 `THIRD_PARTY.md`；只有锁
文件、镜像 digest 和模型 revision/hash 能证明实际引入版本。

## 验证

本次完成官方证据与许可初审、功能覆盖矩阵、唯一路线和机器登记。治理脚本会验证实际登记中的可信
候选数量、单一决定、默认路线、自研缺口、记录 marker 与 `THIRD_PARTY.md` 同步项；是否确已完成代码
级许可证、安全与数据合同审查仍需人工复核。每个未来采用项还须独立完成固定版本/hash、SBOM/CVE、
contract test、代表性基准、故障注入、备份恢复和数据/模型权利核验。

R0P 已将上述固定 digest 的 Valkey 与 SeaweedFS 引入隔离 Compose，并验证四个完整 worktree 的缓存、
对象、网络、卷及动态端口互不污染。该证据只提升开发运行时成熟度；生产 adapter、对象版本与
checksum、备份恢复及故障注入仍未因此放行。

## 供应链与归属

本次没有复制第三方源代码、模型、数据或制品。参考项目和计划组件已登记到 `THIRD_PARTY.md`；后续
实际引入必须固定 release/commit/hash/digest，生成 SBOM，并分别核验代码、模型、训练数据和外部数据
许可。机器登记只证明审查记录完整，不构成许可证或数据权利的自动批准。

## 退出与复审触发条件

许可证或数据条款变化、仓库归档、重大 CVE、大版本、维护者/发行渠道变化、SLO 连续失败、模型卡或
权利变化时强制复审。替换必须保持 Port 和项目 contract，迁移数据时保留 PIT、hash 与审计链。

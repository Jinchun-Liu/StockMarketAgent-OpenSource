# R0P：并行交付基础实施规格

状态：`approved` 规格、`verified` 交付。R0P 只建设共享工程基础，不实现 R1 产品能力。

## 工程结果

本阶段的退出结果是：不同开发者或代码 Agent 可以从同一冻结提交创建至少四个独立 worktree，在不
共享数据库、对象、缓存、任务状态、端口或 Compose 资源的情况下并行工作；公共契约、迁移、运行时
和发布状态仍由单一 Steward 管理，所有候选由 Release Steward 按串行列车集成。四槽隔离、
分层 CI、legacy H/S、远端历史回读和 revert 恢复均已通过历史退出验收。这些证据继续保留，但治理
v2 将远端追溯性归入 release gate，不再阻断 R1 本地开发。

## 进入条件

- R0 已验证；
- 当前治理与路线图基线已形成可追溯提交；
- 本阶段只有一个 `active_execution`，所有 lane 共享同一 base commit。

## 唯一工作包

1. Contract Steward 冻结主动标的、R1 两个任务及四个 wire schema，并建立跨提交兼容检查；
2. Migration Steward 只增不改地对齐 event revision、job history 和 outbox/inbox 存储；
3. Platform/Test Steward 提供 worktree Compose 隔离、迁移/集成测试底座和分层 CI；
4. Runtime Integration Steward 冻结 R1 调度范围与延期任务的失败关闭行为；
5. Release Steward 维护本规格、交付证据、远端集成推送历史核验和最终集中蓝图。

## 退出闸门

- 契约包具有共同 base SHA、可复现 SHA-256 和正反 fixture；
- 既有 published schema 与历史迁移不能被同版本修改；
- event/job/outbox 的新迁移、回填、PIT、幂等和恢复测试在真实 PostgreSQL 通过；
- 四个 worktree 同时运行且数据、端口、网络和卷完全隔离；
- CI 对治理、Python、Web、迁移、集成、E2E harness 和四槽隔离分别检查，并由一个稳定
  `quality-gate` 汇总；
- 远端集成推送的分支、提交及祖先关系具有可回读证据，
  `REMOTE_GIT_HISTORY_NOT_VERIFIED` 转为 `verified`；
- 完整 `make verify` 通过。

R1 的真实业务 E2E 是 R1 退出闸门，不是 R0P 或 R1 的循环进入条件。数据授权、真实来源账号和原始
私有数据不进入 R0P；没有授权时连接器保持禁用。

## 当前证据边界

- 已验证：契约冻结及
  `e7e8deaf8ffdfb7d55d29eea3b99d48122ea3719b6ccf168b729ff03928dd72e` hash、两个 R1 task 的
  强类型失败关闭、四个延期 task 禁用、追加式迁移图与真实 PostgreSQL 旧 head 升级；
- 已验证：Job/Outbox/技术 Inbox 恢复测试（真实 PostgreSQL 15 项）、基础设施集成、四个完整
  worktree 同时运行、数据隔离和精确销毁；
- 已验证：七个分层 CI job、最终 `quality-gate` 及失败传播。失败运行 `30066840311` 使 Python 和
  `quality-gate` 失败；修复运行 `30067750506` 全部成功；
- 已验证：提交 `2244a92` 上的完整 `make verify`，包括 403 项通用测试、11 项迁移、真实
  PostgreSQL/Valkey/SeaweedFS 基础集成、15 项恢复、完整栈 runtime smoke 和四 worktree 隔离；
- 已验证：远端 `main`、`integration/R0P/20260724-ci-repair` 和 annotated tag 的历史对象关系；
  严格来源 H/S、no-ff package merge、integration H_i/S_i 和 revert 演练均作为 legacy 历史可审计；
- 未进入本阶段：R1 来源、业务 API/Web、产品任务链和真实产品 E2E。

完整收尾证据见
`bootstrap-closeout.md` (upstream private record, excluded)。

九部门责任域和 R1 波次统一引用
[`implementation-plan.md`](../implementation-plan.md)。本阶段不建立长期部门分支；后续每个工作包
使用短生命周期独立分支，完成需求、调研、规划、测试、实现、推送与交接；交接后停止写入并封存，
已有远端分支与 annotated tag 作为 legacy 保留；新普通包不再创建 package tag。R1 已进入本地实施，
本规格不实现任何
产品能力。

## 非目标

- 不实现外部市场、压力指数、LLM、LoRA、量化预测或真实交易；
- 不预登记 R2–R6 的接口、事件、任务 payload 或指标；
- 不把模块化单体拆成微服务；
- 不以 README、占位任务或仅声明 Port 作为运行时能力。

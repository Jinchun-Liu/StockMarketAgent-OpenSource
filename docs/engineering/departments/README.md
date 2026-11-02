# 六个执行部门

<!-- department-manual-index:v1.0 -->

六部门是长期责任标签，工作分支是短生命周期结果。部门名称不自动授予写权限；真实权限来自当前
轻量工作包的精确范围和本页固定单写区。

实时事实只读取：

- 阶段顺序和进入/退出闸门：`docs/roadmap/implementation-plan.md`；
- workstream 实施规格：`docs/roadmap/workstreams/`；
- 当前阶段、少量活动工作和正式里程碑：`governance/delivery.json`；
- 接口、指标、产品、公式和调度：各自既有规范事实源。

## 1. 部门索引

| ID | 部门 | 手册 | 责任范围 |
|---|---|---|---|
| PAE | 产品与架构标准部 | [pae.md](pae.md) | 公共契约、接口、指标和产品范围 |
| DMI | 数据与市场情报部 | [dmi.md](dmi.md) | 来源、rights、证据、PIT、迁移和 adapter |
| BAE | 助手后端应用部 | [bae.md](bae.md) | 用例、简报、记忆、通知和薄 HTTP handler |
| FEX | 前端产品体验部 | [fex.md](fex.md) | `apps/web` 的页面、组件、交互和个人定制 |
| QMR | 量化与模型研究部 | [qmr.md](qmr.md) | 压力指标、历史情景、LLM、LoRA 和量化研究 |
| QIR | 质量、运行与集成部 | [qir.md](qir.md) | runtime、worker、依赖、integration/E2E 和发布 |

相对路径按本目录解释；调用时直接使用任务四字段与根规则，不生成部门提示词副本。

## 2. 固定单写区

统一阶段 ID：

```text
R0 → R0P → R1 → R2 → R3 → R4 → R5 → R6
```

| 边界 | 唯一责任部门 |
|---|---|
| contracts、Ports、OpenAPI、接口/指标注册和生成前端类型 | PAE |
| 数据迁移 | DMI |
| `apps/web` 手写产品代码 | FEX |
| runtime 组合根、依赖锁、CI、integration 和发布 | QIR |

BAE 可以写薄 HTTP handler，但不能修改公共 Schema。FEX 不能修改 Python handler、OpenAPI 源或
生成类型。其他部门需要跨界变更时，创建责任部门的依赖包。

## 3. 每个具体制品工作包的生命周期

1. 写明 User Capability、Runnable Path、Acceptance Evidence 和 Non-goals；
2. 需要公共边界时由 PAE 先冻结 contract pack；
3. 在一个干净短分支实现，并用 `make package-check` 验证范围和聚焦测试；
4. 不同 Agent 用 `make agent-review` 只读审查最终 HEAD；
5. 最多一次原分支修复并重检；
6. QIR 串行 优先快进 integration，candidate 完成后运行一次 `make integration-check`。

普通包不创建 manifest、control/attempt、seal、handoff、receipt、package tag 或远端分支。部门名称
仅用于责任归属，不要求角色轮转。

所有角色调用细则见 `docs/engineering/agent-invocation-protocol.md`。

## 4. 跨部门交接规则

- 产品选择、公共 contract、Port、Schema、接口或指标：交 PAE；
- 来源、权利、证据、事件、PIT、Repository 和迁移：交 DMI；
- 主动标的、简报、记忆、聊天、通知和薄 handler：交 BAE；
- 页面、交互、移动端、无障碍和个人定制：交 FEX；
- 行为公式、历史情景、模型、训练和离线评估：交 QMR；
- composition、worker、scheduler、依赖、测试、integration 和发布：交 QIR。

交接必须指向最终提交和 contract SHA。禁止通过聊天约定或跨 worktree 读取临时文件形成隐式依赖。
Legacy 九部门矩阵只存在于 Git 历史中，不再生成或维护。

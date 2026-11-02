# 工程规范入口

<!-- governance-doc:engineering-index:v1 -->

本目录是工程规则的规范性文档入口。根目录 `AGENTS.md` 提供每次修改都必须加载的短规则；
这里保存可长期演进的详细标准。规则冲突时，安全与合规要求优先，其次是更靠近被修改目录的
`AGENTS.md`，再其次是本目录文档；局部规则不得降低根规则。

## 必读顺序

所有代码修改都按以下顺序阅读：

1. 根目录 `AGENTS.md`；
2. 本页；
3. `reuse-first-policy.md`；
4. `agent-change-protocol.md`；
5. 多个工作包或 worktree 并行时读 `parallel-development-protocol.md`；
6. 使用 Agent 或并行工作时读 `agent-invocation-protocol.md` 和 `departments/README.md`；
7. `coding-standards.md` 中与改动相关的章节；
8. 跨边界改动再读 `../architecture/interface-standards.md`；
9. 运行时、数据、产品或 ML 指标改动再读 `../architecture/metric-standards.md`；
10. 数据、ML、安全、运维改动继续阅读对应 `docs/` 专题文档和 ADR。

## 规范分层

| 层级 | 载体 | 作用 |
|---|---|---|
| Agent 上下文 | `AGENTS.md`、`CLAUDE.md` | 自动注入必须遵守的行为与入口 |
| 人类规范 | `docs/engineering/`、`docs/architecture/` | 解释设计、边界、演进和审查标准 |
| 机器规则 | `governance/*.json` | 为脚本、hook 和 CI 提供单一机器事实源 |
| 本地阻断 | `.codex/hooks.json`、`.claude/settings.json` | 运行前保护敏感路径和危险 Git 操作 |
| 交付审计 | 真实 diff、最终 HEAD 审查、串行 integration、main ff-only | 本地开发不被远端阻断，阶段发布仍可追溯和恢复 |

说明文档不会单独构成强制执行。真正的约束由类型、schema、架构检查、测试、hook、本地审计和云端
CI 共同完成。Agent 不得修改这些门禁来规避失败。

同一仓库的多个 worktree 共用 hook 规则时，必须区分 hook 脚本位置与工具实际工作目录。普通推送仅可指向同一 Git common-dir 中已登记的精确 worktree 根，并继续核对该目录的授权、候选、独立审核、完整门禁及唯一远端；独立克隆不能据此继承放行。Git 修改按工具目录判断分支，含目录切换或包装的组合修改命令失败关闭。不得静态重定向后仍以脚本目录判断 main，也不得执行未审查工作区中的另一份策略脚本。部分宿主仅传递会话cwd，省略工具实际workdir；此时禁止以会话cwd推定Git修改位置，应在正常命令中使用 `git -C <规范绝对worktree路径>`。受控推送接受这一明确形式，其余候选及远端条件不变。正常根目录查询和推送的hook附加上下文区分脚本路径、可用的工具目录、Git仓库和裁决；没有工具目录时显示空值。

Git行政文件仍禁止内容读取和写入。为诊断实际损坏的worktree登记，guard仅允许完整单命令
`/bin/ls -ld -- <绝对commondir路径>`查看文件属性；也接受仅增加macOS文件标志显示的精确`-ldO`选项。
路径必须为加载策略所属Git common-dir下的
`worktrees/<单个名称>/commondir`，父目录不得经符号链接逃逸。`-d`不跟随最终符号链接，也不读取
文件内容；文件不存在时允许系统ls报告实际缺失。该路径判定不依赖损坏的全量worktree枚举，也不凭
宿主缺失的工具cwd推定任何写入权限。其他属性工具、cat、递归/跟随链接选项、重定向、组合命令及
任意写入仍按原敏感规则拒绝；行政路径不会因符号链接解析而失去保护。普通push的登记、授权、审核、
门禁和远端校验保持不变。

首次在 Codex 打开仓库时，需要把项目标记为可信，并在 `/hooks` 中审阅、信任当前 hook 哈希；hook
变化后必须重新审阅。Claude Code 也需要允许仓库级设置。项目级 hook 是本地护栏，拥有本机配置权的
用户仍可关闭它，因此不能把 hook 描述为不可绕过边界。补偿控制固定为：受管 worktree、规范化权限、
最终 Git diff、绑定 final HEAD 的独立审查、一次只处理一个候选的 integration 列车、本地完整门禁，以及正式
发布前的普通 CI 结果、远端 `main --ff-only` 和 stage tag 回读。云端失败只阻止远端正式发布，不阻止
本地 `locally_verified`；完整本地门禁通过且项目所有者明确授权时，本地 `main` 可以先以 `--ff-only`
前进为未发布里程碑基线。GitHub 仅在异机备份、协作审查或已验收候选/发布需要时批量同步；必要同步沿用根 AGENTS.md 的持续授权，共享历史禁止重写。
在没有托管平台强制权限时，仓库所有者技术上仍可绕过这些步骤；它们是项目唯一认可、可审计和可恢复
的标准路径，而不是不可绕过的权限边界。

## 文档职责

- `coding-standards.md`：Python、TypeScript、API、数据库、异步任务、ML、安全、测试与 Git 规范。
- `agent-change-protocol.md`：Agent 从接单、检查、实现、验证到交付的确定流程。
- `parallel-development-protocol.md`：契约冻结、工作包、单写区、worktree、合并列车与交接规则。
- `agent-invocation-protocol.md`：可选薄 Agent 包装、范围检查、最终 HEAD 审查和集成规则。
- `departments/README.md`：六部门职责、单写区和部门手册入口。
- `developer-experience.md`：五分钟环境诊断、合成演示、反馈环和支持矩阵。
- `reuse-first-policy.md`：新能力与依赖的开源调研、许可硬门槛、采用边界和自研例外。
- `../architecture/interface-standards.md`：统一契约、端口、适配器、消息和版本兼容规则。
- `../architecture/metric-standards.md`：统一运行时、数据、产品和 ML 指标的口径、标签和演进规则。
- `governance/standards.json`：路径保护、命名、版本、质量命令和必读文档。
- `governance/architecture.json`：Python 包分层及允许的内部依赖。
- `governance/interfaces.json`：当前真实存在的 HTTP、任务、发布 schema 与公共 Port 注册表。
- `governance/metrics.json`：当前已声明或实现的统一指标注册表与语义去重键。
- `governance/reuse-decisions.json`：复用候选、唯一决定、自研缺口与第三方登记同步的机器注册表。
- `governance/delivery.json`：当前阶段、少量活动工作、正式里程碑和发布约束。

虚拟研发组织固定分为 PAE 产品与架构标准部、DMI 数据与市场情报部、BAE 助手后端应用部、
FEX 前端产品体验部、QMR 量化与模型研究部、QIR 质量、运行与集成部。部门只帮助拆分责任和交接；
真实写范围取自任务明确声明和固定单写区。整个仓库最多四个可写 worktree，integration worktree 计入
四槽；已交接功能 worktree 必须停写。

所有部门共享同一阶段序列
`R0 → R0P → R1 → R2 → R3 → R4 → R5 → R6`。
部门不再维护每阶段九宫格矩阵。只有在当前阶段产生具体制品时才登记工作包；详细职责由
`departments/` 手册解释，活动工作包的精确 `write_scopes` 才授予写权限。

产品交互改动还必须阅读 `../product/experience-design.md`。复杂工作流使用
`../roadmap/workstreams/` 中当前阶段的独立规格；规格只分解路线图，不得另行定义公共接口或指标。

历史交易结构压力资料只用于理解被重定界的旧方案；活动 R4 以“一次 ResearchSession 计算一个 PIT
市场结构快照”为准。相关工作按职责读取：

- `../product/behavior-pressure-product-spec.md`：产品语义、双轴体验和禁止文案；
- `../data/behavior-pressure-methodology.md`：公式、PIT、同行、历史情景和评价口径；
- `../data/behavior-indicator-catalog.md`：计分、诊断、护栏和研究指标的角色、参照总体与冗余边界；
- `../research/technical-indicator-landscape.md`：K 线、分时、MA、量柱、成熟产品和开源实现调研；
- `../architecture/behavior-pressure-framework.md`：只新增两个 planned Port 的边界、未来目录和同步顺序；
- `../roadmap/workstreams/r4-behavior-pressure.md`：当前 R4 单次快照边界与历史方案说明。

这些规划文件不证明能力已实现；机器注册表仍只登记代码中可静态证明存在的接口和有真实生产者的指标。

R2 全球事件库或跨境事件相关工作还必须分别读取：

- `../research/global-market-and-cross-border-events.md`：学术依据、官方来源和开源/数据候选；
- `../architecture/external-context-framework.md`：stock-agnostic 事件刷新、零跳/一跳匹配和存储边界；
- `../roadmap/workstreams/r2-multi-source-memory.md`：统一 R2 的失败测试、顺序和退出闸门；
- `../data/overnight-external-context.md` 与 `../roadmap/workstreams/r2-external-context.md`：仅作历史路线说明。

历史文件中的 `Briefing 2.0`、隔夜六块和盘中反馈不得据此预登记接口、指标或数据源。

## 修改规范本身

规范变更必须作为独立、可审查的意图出现，并同时完成：

1. 说明现有规则阻碍的具体场景；
2. 更新人类规范与机器规则，避免两套事实；
3. 检查全部必读标准和机器注册表，逐项确认影响；只修改受影响项，并为新增机器规则添加正反例验证；
4. 运行 `make governance`；完整 `make verify` 留给 integration candidate；
5. 在最终回复或提交说明中列明兼容性、安全性、迁移影响、验证证据和精确回退方式。

不得以临时例外、宽泛忽略或“稍后补测”代替规范变更。

# 统一代码规范

<!-- governance-doc:coding-standards:v1 -->

## 1. 基本原则

- 正确性、可追溯性和 point-in-time 安全优先于模型效果与开发速度。
- 代码按领域能力和依赖方向组织，不按框架控制器、工具函数或供应商堆放。
- 每个公开边界都必须强类型、版本化、可观测、可超时、可幂等、可测试。
- 事实数据不可被 LLM 输出覆盖；模型只产生带证据的结构化候选、解释或概率。
- 保持最小变更：不重写无关代码，不混合格式化、重命名和功能修改。

## 2. 目录与职责

| 路径 | 唯一职责 |
|---|---|
| `src/stock_assistant/contracts` | 稳定数据模型、传输信封、错误和公共值对象 |
| `src/stock_assistant/ports` | 业务所需能力的 `Protocol` 端口 |
| `src/stock_assistant/modules` | 用例、领域规则、策略和状态转换 |
| `src/stock_assistant/platform` | 数据库、缓存、对象存储、来源、LLM 等适配器 |
| `apps` | API、worker、scheduler、Web 组合根与薄入口 |
| `ml` | 可复现的数据集、训练、回测、融合、评估和登记流水线 |
| `scripts` | 无业务状态的开发/运维入口；可重复执行且默认安全 |
| `schemas` | 从 Pydantic 生成并版本化的公共 JSON Schema |
| `infra/migrations` | 只增不改的数据库演进记录 |

通用代码只有在至少两个清晰调用方、语义稳定且不属于某领域时才进入 `common`。禁止创建
`utils.py`、`helpers.py` 作为混杂抽屉。

## 3. Python

- 固定 Python 3.12；公共函数、方法、属性和模块常量必须完整类型化。
- 边界禁止 `Any`。内部无法避免时，把不可信值保持为 `object` 或 `JsonValue`，在最近边界验证后
  转成具体类型；每个例外必须局部且有说明。
- 数据契约使用严格 Pydantic 模型，默认 `extra="forbid"`；领域内部优先不可变值对象或
  `dataclass(frozen=True, slots=True)`。
- 端口使用 `typing.Protocol`，名称以 `Port` 结尾；协议方法接收/返回契约，不接收 ORM、HTTP、
  Celery 或厂商 SDK 类型。
- 使用绝对包导入。避免循环依赖；不得用函数内导入掩盖架构环。
- 捕获具体异常。只在边界将底层异常转换为稳定的领域错误；使用 `raise ... from exc` 保留因果链。
- 不使用可变默认参数、隐式全局状态、运行时 monkey patch、裸 `except` 或无说明的 `# noqa`。
- 异步函数只用于真实异步 I/O。CPU/GPU 重任务进入专用 worker；同步 SDK 用有界线程池隔离。
- 所有外部调用显式设置连接/读取/总超时；重试只覆盖明确可重试异常，并使用有界指数退避和抖动。
- Ruff 负责格式与静态规则，mypy 使用 strict。不得手工调整格式去对抗格式化器。

## 4. TypeScript 与前端

- TypeScript 保持 `strict: true`；公共组件、hook、服务和状态必须声明输入输出。
- 禁止 `any`、`@ts-ignore`、无理由的类型断言和非空断言。外部 JSON 先以 `unknown` 接收，再由
  生成类型或运行时 schema 校验。
- Server Component 为默认；只有交互或浏览器 API 才使用 Client Component。
- 浏览器不接触数据库、来源密钥或模型端点；数据访问统一经过版本化 API client。
- 金融判断、来源权重、去重、权限和预测规则只能在后端实现；前端只负责呈现和交互。
- FEX 独占 `apps/web` 的手写产品代码；PAE 单写生成 API 类型，QIR 单写前端依赖锁。其他部门不得
  顺手修改页面，FEX 不得修改 Python handler、OpenAPI 源或生成类型。
- 加载、空数据、部分失败、过期数据和无障碍状态必须明确；颜色不能是多空含义的唯一载体。
- API 静态类型只从 `schemas/http/openapi.v1.json` 生成到
  `apps/web/lib/api/openapi.generated.ts`；运行时解析器仍集中维护，不允许页面复制一套“近似类型”
  或手改生成文件。类型生成必须先通过本地引用检查，并在禁网 Node 进程中执行。

## 5. 命名与公共类型

- Python：模块/函数/变量 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`。
- 数据库：表和列 `snake_case`；主键、外键、唯一约束和索引必须显式命名。
- UUID 是跨进程对象主标识；证券使用命名空间标识 `MARKET:SYMBOL`，显示代码不能充当唯一键。
- 金额用 `Decimal`，JSON 用十进制字符串；收益率和概率可用有限浮点数，并明确单位与区间。
- 所有 datetime 必须带时区；持久化/消息为 UTC，交易日历与展示明确使用 `Asia/Shanghai`。
- 枚举值采用稳定的小写 `snake_case`，已发布后不复用或改变语义。

## 6. HTTP API

- 路径固定 `/api/v{major}`；资源名复数、小写、连字符仅用于 URL，不把动作塞进资源路径。
- 成功响应使用统一 `ApiResponse[T]`，携带 `request_id`、`correlation_id`、`as_of`、生成时间和契约版本。
- 列表使用不透明 cursor 分页；禁止公共 API 依赖易漂移的 offset 分页。
- 错误使用 `application/problem+json` 和 RFC 9457 结构，扩展稳定 `code`、追踪 ID、`retryable` 与
  安全字段错误；不得泄漏堆栈、SQL、密钥或原文。
- 创建返回 `201`；异步长任务返回 `202` 与 `job_id`；幂等创建要求 `Idempotency-Key`。
- `GET` 无副作用。认证失败、授权失败、冲突、限流与暂时不可用使用准确状态码，不统一返回 200。
- OpenAPI 与 FastAPI/Pydantic 同源；路由只做认证、校验、调用用例和传输映射。提交前必须通过
  快照、前端生成类型和相对 merge-base 的破坏性变化检查。

## 7. 事件、任务与通知

- 领域事件名：`com.stockassistant.<module>.<event>.v1`。
- 后台任务名：`stock_assistant.<module>.<operation>.v1`。
- 信封必须含消息/任务 ID、类型、schema 版本、发生/入队时间、生产者、correlation/causation ID、
  幂等键、尝试次数和强类型 payload。
- 消费者按消息 ID 或业务幂等键去重；副作用与 outbox 在同一事务提交。
- 任务参数只传 ID、小型契约和时间边界，不传 ORM 实体、整篇文档或不可序列化对象。
- 重试前区分永久失败与暂时失败；耗尽重试进入可审计失败状态，不无限循环。
- 通知只消费已验证的物化变化，使用稳定幂等键，不因 worker 重试重复推送。

## 8. 数据库与迁移

- Repository 端口返回领域/契约对象；事务边界由 Unit of Work 明确控制。
- 查询必须有界；批量路径避免 N+1；索引需对应真实查询并通过执行计划或基准验证。
- 乐观锁或唯一约束保护并发修订；禁止依赖“先查再写”保证唯一性。
- 已进入 Git 主分支或共享环境的迁移不可修改、重排或复用 revision；只新增迁移。
- 破坏性结构变化采用 expand/migrate/contract：先兼容添加、再回填、最后在后续版本移除。
- 回填可恢复、可重复、分批执行，并记录进度；迁移不得进行无界网络调用。
- 删除数据需明确保留策略、审计、备份和恢复路径。

## 9. 数据、ML 与预测

- 原始数据、持仓、授权全文、embedding、模型权重和实验大产物不进入 Git。
- 数据集必须登记来源、阶段标签、提取时间、时间覆盖、schema、去重方式、划分和内容哈希；R7 起还
  必须登记已核验授权及用途范围。
- 训练/评估严格按时间切分；特征满足 `first_seen_at <= decision_time`，禁止随机切分造成未来泄漏。
- 实验固定随机种子、环境、代码、数据、规则、特征、模型和 adapter 版本；指标写入登记系统。
- 预测输出是校准概率或区间，始终 `paper_only=true`；必须包含 as-of、预测窗口、模型版本和不确定性。
- LLM 请求采用 JSON Schema 约束，发送最小证据包；R1–R6 外部模型只接收公开或脱敏且标明开发阶段
  的数据，R7 起只接收逐用途正式授权并脱敏的数据。
- 模型失败不能阻止原始证据持久化；无足够证据时返回 abstain，不编造补全。

## 10. 安全、隐私与可观测性

- 配置从环境或密钥管理器注入；示例只保留无效占位值。任何日志、异常、追踪和测试快照都先脱敏。
- 输入按不可信数据处理；网页内容不能成为 system/tool 指令。工具调用必须白名单授权并限制资源。
- 来源访问默认只允许登记过的固定 allowlist。R1–R6 保存开发阶段策略快照并拒绝未登记来源；R7 起
  抓取、缓存、保留、embedding、训练和导出分别按正式权利记录决策。
- 日志使用结构化字段，至少含时间、级别、组件、事件名、request/correlation ID 和安全错误码。
- 指标覆盖吞吐、延迟、失败、重试、队列积压、数据新鲜度、来源缺口和模型降级；禁止高基数标签。
- 指标定义以 `governance/metrics.json` 和 `metric-standards.md` 为唯一口径；新增或修改前先检索已有
  semantic key，语义等价时必须复用。不得在代码、Dashboard 或 ML notebook 中私建第二套公式。
- 健康检查有界且不泄密；liveness 不依赖外部系统，readiness 才报告必要依赖。

## 11. 测试

- 单元测试覆盖领域规则、边界值、拒绝路径和确定性；不得访问网络或真实服务。
- 契约测试覆盖 JSON Schema、端口适配器、事件兼容、任务序列化、权限和迁移。
- 集成测试使用隔离基础设施并负责清理；端到端测试只保留关键用户路径。
- 缺陷修复必须包含能在旧实现失败的回归测试。时间用可注入时钟，随机数固定种子。
- 外部模型和来源用录制后脱敏的固定样例或 fake port；不得断言非确定的自然语言全文。
- 覆盖率是最低门槛，不替代行为断言；禁止用排除文件或删除测试提高数字。

## 12. 依赖、Git 与审查

- 新能力和依赖必须先执行 `reuse-first-policy.md`：比较可信候选，给出一个默认采用路线，并记录
  官方证据、许可证、数据/模型权利、维护、安全、基准和退出方式；无合适候选才允许自研。
- 新依赖必须有现有依赖/标准库无法满足的理由，检查许可证、维护状态、漏洞和锁文件变化；固定
  release/commit/hash 或镜像 digest，并同步 `THIRD_PARTY.md`、SBOM 和 NOTICE。
- 第三方库、SDK、服务、模型和 fork 只能在 `platform` adapter、`ml` 离线流水线或明确的组合根出现；
  不得把第三方类型泄漏到 contract、Port 或领域模块。通用子能力不得在业务层重复实现。
- 修改接口、任务、事件、schema、Port 或指标前必须先查两个机器注册表和全部调用方；存在对应定义时
  兼容演进，不得另建。确需变更时一次同步代码、注册表、标准、schema/公式、消费者、观测和测试。
- 没有明确许可证、限制商业使用、已归档或数据权利不清的候选默认拒绝；GPL/AGPL/SSPL、
  source-available、Commons Clause 和混合许可必须专项审查，不能因“当前不商业化”绕过。
- 使用 Conventional Commits：`type(scope): summary`；一次提交表达一个可回滚意图。
- 不提交生成缓存、临时数据、密钥、编辑器状态或无关格式变化。
- 任务四字段必须说明行为变化、接口/迁移/安全影响、聚焦测试和精确回退方式；高风险改动必须
  拆成更短、可独立合入和 revert 的分支。
- 并行工作遵循 `parallel-development-protocol.md`：公共契约先冻结、共享区单写、所有 lane 基于同一
  提交且写范围不重叠；禁止两个分支分别修改迁移链、任务目录或注册表后依赖人工拼接。
- 六个执行部门不自动拥有路径；真实权限来自任务明确范围和固定单写区。最多三个后端功能
  worktree、一个 FEX worktree 和一个 QIR integration worktree。
- 新包可通过薄包装 `make agent-run` 启动，并用 `make package-check` 做聚焦验证；不同 Reviewer
  由 `make agent-review` 在当前最终 package HEAD 上只读审查累计 diff。
- 普通包不创建 control/attempt/seal/handoff/package tag，也不要求远端工作分支。Legacy R1 的
  H/S/tag 只存在于 Git 历史；当前流程只认一个 final HEAD 及其独立审查。
- QIR 逐个以 `--ff` 合入 integration 并运行 `make integration-check`；未通过时 `main` 和阶段
  tag 不得移动。
- 功能 diff 按 `base..HEAD`、staged、unstaged 和 untracked 共同检查；任何越界都失败关闭。
- 禁止强推共享分支、移动已有 tag、重写用户提交、跳过 hook，或在未授权时提交、推送、创建 tag、
  合入 integration candidate、以 `--ff-only` 更新 `main` 或部署。普通包集成后删除普通远端分支，
  永久标签只保留正式阶段发布标签。

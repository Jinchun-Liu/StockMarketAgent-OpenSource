# 统一接口与演进标准

<!-- governance-doc:interface-standards:v1 -->

## 1. 固定架构方向

项目采用六类边界，依赖只向左：

```text
contracts/common <- ports <- modules/application <- platform adapters <- apps/bootstrap
                                      ^
                                      |
                               ml pipelines（经端口）
```

| 类型 | 定义 | 不允许包含 |
|---|---|---|
| Contract | 跨模块/进程稳定数据与错误语义 | ORM、HTTP Request、Celery、SDK 对象 |
| Port | 业务需要的能力接口 | 厂商名、连接池、序列化细节 |
| Use case | 一次业务目标的编排与规则 | 路由、队列声明、数据库会话 |
| Adapter | 一个端口到具体技术/供应商的实现 | 跨领域业务决策 |
| Entrypoint | HTTP、任务、调度、CLI 的输入输出映射 | 业务规则和持久化查询 |
| Composition root | 根据配置装配端口实现 | 运行时动态导入未验证代码 |

`src/stock_assistant/contracts` 与 `src/stock_assistant/ports` 是公共内核。模块不得 import
`platform`；用例通过构造函数接收所需端口，入口接收用例，组合根负责装配具体适配器。可选供应商
由配置选择实现，不改变端口。

任何接口工作开始前，必须先搜索 `governance/interfaces.json`、`src/stock_assistant/contracts`、
`src/stock_assistant/ports`、路由、任务和调用方。已有接口能够表达需求时必须复用或兼容扩展，禁止因
供应商、页面、Agent、团队或命名偏好不同另建语义重复接口。确需新增时记录现有接口的具体缺口；
修改已有接口时同步影响矩阵。指标口径遵循 `metric-standards.md`，接口变化必须同时检查已有指标。

## 2. 契约源码与序列化

- Pydantic `ContractModel` 是公共 JSON 契约的唯一源码，默认拒绝未知字段并校验赋值。
- JSON Schema 由脚本生成到 `schemas/<domain>/<name>.v<major>.<minor>.schema.json`，生成文件禁止手改。
- 契约只使用可移植值：string、boolean、有限 number、十进制 string、带时区 datetime、UUID、
  明确 enum、list、map 和嵌套契约。
- 公共 JSON 禁止 `Any`、任意元组、Python 对象、bytes、异常对象、ORM 实体和厂商响应；
  对象存储的数据平面仅允许有界异步字节流，密钥只允许受保护的 `SecretBytes`。
- 真正开放的 JSON 扩展字段使用 `JsonValue`，必须有大小限制、命名空间和明确拥有者。
- 字段语义包含单位、时区、可空含义、最大长度、排序、唯一性和时间点，不依靠调用方猜测。
- 所有含 `schema_version` 的入站 payload 都必须显式提供版本；解析器不得用模型默认值替调用方补版，
  缺失或未知主版本必须在进入业务逻辑前拒绝。
- 所有 Decimal JSON 值只能使用十进制 string；JSON number 即使当前值可精确表示也必须拒绝，避免
  上游语言、序列化器和数据库之间发生不可见精度漂移。

每个公共请求携带或可映射到 `OperationContext`：

```text
request_id, correlation_id, actor_id, workspace_id, as_of,
deadline_at, idempotency_key, locale, timezone
```

`as_of` 是业务可见信息截止点，不等同于服务器当前时间。跨进程时必须原样传递 correlation 和
point-in-time 上下文。

## 3. 端口设计

- 端口定义在 `src/stock_assistant/ports`，用 `Protocol` 表达结构化子类型，名称以 `Port` 结尾。
- 一个端口围绕业务能力而非 CRUD 表或供应商设计，例如 Source、Memory、Generation、Forecast、
  Notification、Repository、UnitOfWork、Scheduler、ObjectStore、Cache、Authorization、Audit、Health。
- 方法接收单一请求契约并返回单一结果契约；查询可返回 `CursorPage[T]`。
- 业务端口不得用裸 `bool`、`None` 或 `T | None` 表达成功、失败或未找到；使用带 `found`、状态、
  `as_of`、稳定原因码和幂等信息的显式结果契约，为未来新增元数据保留兼容空间。生命周期方法与
  明确记录的数据平面流是受控例外。
- 异步端口只表示 I/O 或异步资源；不得为统一外观把纯计算强制 async。
- 可选能力通过显式 capability 描述和启动时校验，不通过 `hasattr` 或捕获 AttributeError 探测。
- 错误必须转换为稳定的端口错误类别：validation、not_found、conflict、unauthorized、forbidden、
  rights_blocked、rate_limited、timeout、unavailable、permanent_failure。保留底层异常为 cause，
  但不向上泄漏供应商文本。
- 超时由调用方 deadline 与适配器更短的传输超时共同约束；取消必须传播，不得捕获后继续副作用。

### 3.1 Port 生命周期闸门

从治理 v2 起，新 Port 不能只凭路线图或“以后可能需要”创建：

- 同一阶段必须存在真实 consumer 工作包；
- 同时必须有同阶段 provider/adapter，或有紧邻下一工作包且显式依赖该 consumer 的 provider；
- consumer、provider、引入阶段和到期阶段登记在 `governance/delivery.json`，不污染接口语义注册表；
- `declared` Port 跨过一个阶段仍无 consumer/provider 时自动进入 `cleanup_review`；
- cleanup review 必须形成 `reuse / merge / implement / deprecate` 决定，未关闭时下一次 contract freeze
  失败；
- 当前 59 个 declared Port 作为 legacy baseline，不逐个建包，在 R1 退出时集中审查一次。

`governance/interfaces.json` 仍只描述接口身份和真实状态，不承担交付排期。

## 4. HTTP 接口

- 公开路径使用 `/api/v{major}`；major 与资源契约主版本一致。
- 非流式成功响应统一为 `ApiResponse[T]`；健康检查可使用最小固定模型。
- 错误统一为 `ProblemDetails` 和 `application/problem+json`；`code` 稳定、可告警、可国际化。
- 列表使用 cursor、`has_more`、`as_of`；cursor 是不透明、可签名、有限时效的字符串。
- 长任务返回 `202` 和 `JobAccepted`；查询状态返回强类型 `JobResult`，SSE 只传版本化事件信封。
- API 模型与领域契约分离仅在传输语义确有差异时发生；映射函数显式、纯函数、双向有测试。
- OpenAPI 的破坏性变化与契约主版本同步，不允许只改文档隐藏兼容问题。
- FastAPI `app.openapi()` 是 HTTP 描述的唯一源码；`scripts/generate_openapi.py` 必须在空临时目录和
  固定、非敏感环境中生成 `schemas/http/openapi.v1.json`，并以 Python audit hook 对所有
  `socket.*` 操作失败关闭；不得读取仓库 `.env`、启动服务或访问网络。
- `apps/web/lib/api/openapi.generated.ts` 只由锁定版本的 `openapi-typescript` 从上述快照生成。它提供
  静态类型；生成前必须拒绝外部 `$ref` 和隐式 Redocly 配置，并由禁网 Node 进程执行。生成类型
  不替代现有 `unknown` 入站值的运行时校验，也不得由页面手工修改。
- 破坏性变化以真实 merge-base 为基线，使用固定镜像摘要的 `oasdiff` 断网检查，并禁止外部 `$ref`。
  首次发布没有旧快照时仍须通过规范校验和已知破坏性变化负例；之后删除快照或破坏兼容必须失败。

## 5. 消息、任务与调度接口

事件名称固定 `com.stockassistant.<module>.<event>[.<subevent>].v1`，任务名称固定
`stock_assistant.<module>.<operation>.v1`。信封至少包含：

```text
message_id/type/schema_version/occurred_at/produced_at/producer
correlation_id/causation_id/idempotency_key/attempt/payload
```

- payload 是具体契约，不是 `dict[str, Any]`。
- 生产代码中的版本化事件名必须作为字面量出现在 producer 文件并登记到
  `governance/interfaces.json.events`；登记项引用已发布 payload schema。规划中的事件不能预登记。
- 生产者先写业务状态和 outbox；发布器只转发。消费者先做 schema、权限和幂等检查。
- 语义为 at-least-once；所有消费者必须能安全重复。不能用“队列通常只发一次”作为一致性保证。
- 定时器只发出触发意图；worker 再验证交易日历、会话窗口、锁、游标和上次成功状态。
- 重试使用原 message/job ID 并增加 attempt；新的人工重放产生新 ID，同时记录 causation ID。
- 死信/永久失败保留安全错误码、尝试摘要和恢复入口，不保存敏感 payload 副本。

## 6. 数据源与权限接口

`SourceConnectorPort` 类能力必须把来源身份、capability、cursor、watermark、rights snapshot 和时间范围显式化。

- cursor 为供应商适配器拥有的不透明值；上层不得解析或拼接。
- R1–R6 每次 fetch/cache/embed/train/export/retain/delete 调用阶段策略端口，只允许登记的
  `DEV_ONLY/UNVERIFIED` 研究用途；R7 起调用正式权限端口，授权缺失时失败关闭。
- 返回 `SourceRecord` 只含标准元数据、许可范围内 payload 和不可变证据引用。
- 原文是否缓存与元数据是否缓存是两个能力；适配器不得因“可访问”推断“可永久保存/训练”。
- 限流、条件请求和增量游标按 `source_id` 隔离，单一来源故障不阻断其他来源证据持久化。

新增来源只需实现端口、声明 capability/rights、通过适配器契约套件并在组合根注册；领域模块不得新增
来源分支判断。

## 7. 记忆、检索与去重接口

- 短时工作记忆、情景向量检索和语义规则是不同端口与生命周期，不共享隐式全局缓存。
- 写入返回稳定 memory/item ID、版本、内容哈希、首次可见时间和保留期限。
- 检索请求明确 workspace、实体、时间窗、as-of、过滤条件、top-k 和最小分数；结果返回距离、
  rerank 分、evidence ID 与首次可见时间。
- 向量相似只做候选召回；精确哈希、来源 ID、实体/事件类型/时间和事实槽位决定重复或修订。
- 删除按主体/来源/保留策略可审计传播到向量、对象和缓存，不保留无法追踪的孤儿 embedding。

## 8. LLM、Embedding 与 Rerank 接口

- `GenerationRequest` 明确 task type、消息、response schema/hash、evidence IDs、privacy level、
  operation context 和 timeout。
- `GenerationResponse` 只接受 schema 校验后的 payload，并记录 provider/model/adapter、prompt/hash、
  token、延迟、finish reason 和 degraded 状态。
- 本地与外部模型实现同一端口；路由决策基于 privacy、capability、健康和配置，不写进业务提示词。
- 不向外部模型发送 `local_only` 内容；发送前做最小化和脱敏，返回后仍执行确定性 schema/证据校验。
- embedding 返回维度与每个输入哈希；维度或模型改变时建立新索引版本，不原地混写。
- rerank 返回原输入 index 和 score，不复制整篇文档。

## 9. 预测与融合接口

- 量化模型输入必须是 point-in-time 特征快照，包含 feature set/version、decision time、horizon 和
  数据质量；文本模型输入只引用当时可见事件。
- 输出使用概率、分位区间、校准信息、模型/数据/代码版本和 `paper_only=true`，不输出交易指令。
- 融合器是确定性、版本化适配器；输入保留量化与文本子预测，输出记录权重、约束和降级原因。
- 任一输入过期、缺失或冲突时按明确策略降级或 abstain；不得用 LLM 猜测缺失数值。
- outcome 写入使用预测 ID 和观察窗口，保证一次结算并支持滚动样本外审计。

## 10. Repository、事务与对象存储接口

- Repository 按 aggregate/查询意图命名，不公开 SQLAlchemy Session、Query 或数据库行。
- `UnitOfWorkPort` 显式提交/回滚；用例不得跨等待网络 I/O 持有数据库事务。
- 新增/更新使用乐观版本、唯一键或 idempotency key 解决并发；冲突返回稳定错误。
- Object store 返回 `StoredObjectRef`（namespace/key/hash/media type/size/rights），不返回本地文件路径；
  上传下载使用异步流，禁止把大文档整体装入队列消息。
- Cache 只是派生加速层；事实主库不可依赖缓存存在。key 需命名空间、版本和 TTL。

## 11. 版本与兼容策略

公共 schema 使用 `MAJOR.MINOR`：

- 可选字段且有安全默认值、扩大 enum 前已证明旧消费者可忽略：升 minor；
- 删除/重命名字段、改变类型/单位/时区/约束/默认语义、收紧合法值、改变错误或幂等语义：升 major；
- 补充文档且不改变机器行为：不升版本。

生产者遵循“先扩展、双写/双读、迁移消费者、观测、再移除”。至少在声明的兼容窗口内支持旧主版本；
不能静默 reinterpret 旧字段。事件消费者必须拒绝未知主版本并把错误送入可审计隔离队列。

## 12. 机器接口注册表

`governance/interfaces.json` 是当前仓库公开接口的机器索引，只登记代码中可静态证明存在的内容：

- FastAPI 组合根实际挂载的 HTTP 路由；
- `apps/worker/task_names.py` 中实际注册的版本化任务；
- producer 模块中实际存在并有已发布 payload schema 的版本化领域事件；
- `schemas/` 中不可变、已生成并发布的 JSON Schema；
- `src/stock_assistant/ports` 中继承 `Protocol`、以 `Port` 结尾并从 `ports.__all__` 导出的端口。

状态必须忠实表达成熟度：可调用路由为 `implemented`；业务工作流尚未实现但任务名及 callable 已
存在的任务为 `registered_stub`；生成 schema 为 `published`；只有接口声明而未声称存在生产适配器
的端口为 `declared`；已存在 producer 和 payload schema 的事件为 `implemented`。禁止为了路线图
完整而登记尚不存在的接口。

这些状态不能代替交付成熟度：

- `registered_stub` 必须声明 `stub_behavior`：已有 request/result schema 的 R1 边界使用
  `typed_skipped`，先验证冻结请求再返回类型化 `SKIPPED`；没有 payload schema 的延期任务使用
  `fail_closed_disabled`，只能保留稳定任务名并抛出 `FEATURE_NOT_ENABLED_FOR_STAGE`；
- 两类 `registered_stub` 都不能被并行 lane 当作已实现业务能力；延期任务不得进入 Beat 或运行队列；
- `declared` Port 可以作为编译期边界，但不能证明 fake、生产 adapter、组合根或运行时集成存在；
- `implemented` HTTP 只证明当前路由行为存在，仍须检查其 maturity 与真实依赖；
- adapter 和 consumer 的真实状态登记到 `governance/delivery.json`，不污染接口存在性注册表。

每个 Port 还必须声明调用方向：驱动适配器发起业务能力使用 `inbound_use_case`，应用或领域代码调用
外部/可替换能力使用 `outbound_dependency`，生命周期、健康、时钟和标识等横切能力使用
`lifecycle_utility`。direction 描述的是调用方向，不是进程边界；同一进程中的模块化单体同样遵守。

新增、删除、重命名、改变语义或升版以上任一接口时，同一改动必须更新注册表，并运行：

```bash
python3 scripts/check_interface_registry.py
```

检查器只解析 AST 和 JSON，不导入 FastAPI、Celery、配置或适配器，因此可安全用于 hook、pre-commit
和 CI。注册表 ID、版本、owner、status 与 compatibility 是稳定审查元数据；未来扩展通过新增可选
字段完成，既有字段不得静默改变语义。

## 13. 并行契约冻结

跨模块实现分发给多个 lane 前，Contract Steward 必须按
`docs/engineering/parallel-development-protocol.md` 建立契约冻结提交。冻结范围包含 contracts、ports、
JSON Schema、OpenAPI 快照、前端生成类型、接口注册表、任务名称、生成器和兼容检查器，并以
`contract_bundle_sha256` 固定。

R1 的既有冻结 bundle 中包含一个 active-target PUT 历史候选。该候选不能被解释为已登记或可实现
接口；R1 的可执行 HTTP 面以 `governance/delivery.json#r1_product_scope.public_http` 与
`governance/interfaces.json` 的成熟度共同判定。当前只允许三个只读 GET，PUT 仅保留其通用
selection/mutation Schema 作为 R1 后续兼容边界。为了“删掉候选”而改写已冻结 bundle 同样被禁止。

冻结后：

1. 所有 lane 从同一 base commit 建立；
2. 功能 lane 只能引用已登记 ID 和版本，不能直接修改公共边界；
3. 新缺口由 Contract Steward 的独立短契约分支兼容扩展或升主版本，完成 handoff 并串行集成后
   重新冻结；
4. provider 与 consumer 使用同一 fixture/contract suite；
5. task/event 跨进程使用前必须有已发布 payload schema；
6. 最终 integration lane 在合并候选上验证生产 adapter、组合根、幂等和降级。

接口注册表回答“存在什么”，交付注册表回答“谁实现/消费以及是否验证”，两者必须同步引用稳定 ID，
不得建立另一套名称。

接口由 PAE 产品与架构标准部单写协调。Pydantic → OpenAPI → 生成 TypeScript → fixture 是唯一
前后端契约链，详细内容见 `api-contract-pack.md`。契约包形成最终 package HEAD 后由独立 Reviewer
绑定审查；QIR 只把通过聚焦检查与审查的提交逐个 `--ff` 合入唯一 integration candidate。普通包
不创建 package tag 或远端分支；阶段验收通过且所有者批准后，`main` 才能 `--ff-only` 前进并创建一个
正式阶段 annotated tag。

## 14. 扩展验收清单

任何新端口、适配器或公共接口 handoff 前必须确认：

1. 已检索注册表与代码，不存在语义对应接口；若存在则复用，若新增则记录确切缺口；
2. 业务拥有者、输入输出、权限、时间语义和失败模式明确；
3. 契约无框架/厂商泄漏，无 `Any`，限制和单位齐全；
4. 版本、命名、幂等、timeout、retry、cancellation 与 observability 已设计；
5. 正常、边界、拒绝、超时、重复、部分失败和旧版本兼容有测试；
6. 生产者、消费者、schema/OpenAPI、指标、Dashboard/告警、迁移、配置、runbook 和安全文档同步；
7. fake adapter 与至少一个真实适配器通过相同契约测试；
8. `check_architecture.py`、`check_interface_registry.py`、`check_metric_registry.py`、JSON Schema /
   OpenAPI / 前端类型生成漂移与跨提交兼容检查、类型检查和完整测试通过。

缺少任一项时接口仍是内部实验，不得被外部调用方或持久消息依赖。

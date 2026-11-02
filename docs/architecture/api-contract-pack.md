# API Contract Pack 标准

状态：`approved`

API Contract Pack 是前后端并行开发的唯一拼接协议。PAE 为每个共享功能冻结一份包，BAE/DMI 与 FEX
必须引用同一 `contract_base_sha`。

## 唯一生成链

```text
Pydantic 公共契约
→ FastAPI OpenAPI
→ schemas/http/openapi.v1.json
→ openapi-typescript
→ apps/web/lib/api/openapi.generated.ts
```

Pydantic 是公共数据结构唯一源码；OpenAPI 快照是进程边界事实源；TypeScript 类型只能生成，不能手改。
FEX 可以建立 ViewModel，但不得复制后端状态机、金融判断、rights、证据或推断规则。

## 每个契约包的内容

- endpoint、请求、成功响应和 RFC 9457 错误；
- 成功、空、无变化、部分数据、过期、冲突、依赖故障和 rights blocked 示例；
- 固定时间、确定性数据和全部 `evidence_id`；
- OpenAPI 快照、生成类型和 `contract_base_sha`；
- fixture Schema 校验和真实 handler 契约测试。

fixture 放在 `tests/contract/packs/<feature>/`，前端镜像只读取这些事实，不另建手写类型。fixture 模式
必须显示“合成演示”，且不能成为生产构建默认值。

## 边界

后端负责稳定语义状态、证据、数据质量、时间和错误码，不返回 HTML、颜色或布局指令。FEX 负责排版、
颜色、动效、响应式、无障碍和交互，不重新计算领域结论。

FEX 需要字段时向 PAE 提交契约需求。PAE 只能选择复用已有字段、兼容增加 minor 字段或建立新的读模型；
未冻结前双方都不得猜测字段。契约变化后，所有消费者必须更新到同一新 contract SHA。

## R1 Active Target 契约

R1 只读加载固定目标 `XSHG:603986`。不提供搜索、输入、添加、激活、切换、删除或 PUT。生产
Portfolio provider 尚未装配时返回稳定 `503 DEPENDENCY_UNAVAILABLE`；不得以 `found=false` 或
fixture 冒充依赖正常。FEX 在 fixture 模式可独立展示成功与 503 状态，但真实 API 联调必须覆盖二者。

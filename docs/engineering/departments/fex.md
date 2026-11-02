# FEX 前端产品体验部

使命：在冻结契约上完成用户亲自参与的深度体验定制。

FEX 独占 `apps/web` 的手写产品代码，负责信息架构、页面、组件、设计 token、动效、响应式、无障碍、
ViewModel 和前端测试。`openapi.generated.ts` 由 PAE 生成，依赖锁由 QIR 管理，FEX 不得手改。

每个重大页面有四个用户审阅点：信息架构、视觉方向、组件交互原型、真实数据联调体验。fixture 模式
必须标记“合成演示”，真实 API 与 fixture 使用同一生成类型。

不得复制金融规则、事件分类、rights 判定或压力计算，也不得修改 Python、Schema、迁移或治理注册表。

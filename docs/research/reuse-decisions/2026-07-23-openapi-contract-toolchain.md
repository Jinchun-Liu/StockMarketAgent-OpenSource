# OpenAPI 契约生成与兼容检查工具链复用决策

<!-- reuse-decision-id:openapi-contract-toolchain-2026-07-23 -->

- 日期：2026-07-23
- 负责人：dependency steward / architecture CODEOWNER
- 状态：accepted
- 最终决定：`adopt`
- 机器登记：`governance/reuse-decisions.json`
- 关联工作流：`R0P / openapi-toolchain`

## 问题与验收标准

为冻结的 FastAPI/Pydantic OpenAPI 快照建立两项确定性工程能力：

1. 从仓库内 `schemas/http/openapi.v1.json` 生成仅含静态类型的
   `apps/web/lib/api/openapi.generated.ts`，避免前端复制近似接口类型；
2. 在合并基线与候选快照之间检测破坏性 OpenAPI 变化，并以非零退出码阻断兼容性回归。

输入必须是仓库内固定、可审查的 OpenAPI JSON；工具不得成为运行时依赖、不得联网解析远程
`$ref`、不得生成第二套 HTTP 客户端或业务规则。生成结果必须可重复，支持只检查不改文件的模式。
前端类型生成在普通开发机上应在秒级完成；兼容检查应适合在 CI 容器中运行并固定精确镜像 digest。

## 检索范围

2026-07-23 核验了各项目的官方 GitHub 仓库、Release、许可证、官方 CLI 文档和 npm 官方注册表：

- 类型生成：[openapi-typescript 7.13.0 release](https://github.com/openapi-ts/openapi-typescript/releases/tag/openapi-typescript%407.13.0)、
  [官方 CLI 参数](https://openapi-ts.dev/cli)、[Hey API release](https://github.com/hey-api/hey-api/releases/tag/2026-06-22)、
  [OpenAPI Generator 7.24.0](https://github.com/OpenAPITools/openapi-generator/releases/tag/v7.24.0)；
- 兼容检查：[oasdiff 1.25.1](https://github.com/oasdiff/oasdiff/releases/tag/v1.25.1)、
  [OpenAPITools/openapi-diff](https://github.com/OpenAPITools/openapi-diff/releases/tag/2.1.7)。

上述五项均为可信候选。选择按当前需求的最小边界进行：只生成 TypeScript 类型，不生成 SDK；
只比较本地 OpenAPI 快照，不引入 Java/Maven 工具链。

## 硬门槛

| 候选 | 精确版本/commit 或制品 | 许可证 | 维护/安全 | 数据/模型权利 | 结果 |
|---|---|---|---|---|---|
| openapi-typescript | `7.13.0` / release commit `5709d33`; npm integrity `sha512-EFP392gcqXS7ntPvbhBzbF8TyBA+baIYEm791Hy5YkjDYKTnk/Tn5OQeKm5BIZvJihpp8Zzr4hzx0Irde1LNGQ==` | MIT | 活跃发布；官方 CLI 提供 `--alphabetize` 与 `--check` | 只读取仓库内公开契约，不处理用户或市场数据 | `adopt`；唯一前端静态类型生成器 |
| `@hey-api/openapi-ts` | `0.99.0` / release commit `c9dc0b9`; npm integrity `sha512-SePU/5oEWWkvUBYmvzdYRctseoLuskyhs4ET0RvLIcmzc8yLQoA2R+KtBIQ8bPsoSUB0m4E5SmBnl6aGSA0szQ==` | MIT | 活跃，但 `0.x` 发布仍含破坏性变化；要求 Node `>=22.18.0` | 同上 | `reject`；SDK、客户端和插件生成超过当前静态类型需求 |
| OpenAPI Generator | `v7.24.0` / release commit `203d486` | Apache-2.0 | 成熟、生成目标广，官方发布持续维护 | 同上 | `reject`；Java/模板/多语言 SDK 工具链显著扩大供应链和生成面 |
| oasdiff | `v1.25.1` / release commit `0a97c00`; `tufin/oasdiff:v1.25.1@sha256:3a0556fff855d1e1c91ebeedf4800772090cbdb4f319e0b4ee3be549bd5b5072` | Apache-2.0 | 活跃发布、专用 breaking-change CLI、支持 OpenAPI 3.1 | 只挂载比较两个仓库快照；禁止 URL 输入和远程 `$ref` | `adopt`；唯一破坏性变化检查器 |
| OpenAPITools/openapi-diff | `2.1.7`（2026-01-26 release） | Apache-2.0 | 仍维护，提供 CLI/Maven 插件 | 同上 | `reject`；Java/Maven 运行时更重，当前无需其报告渲染和插件能力 |

所有候选代码许可证均通过宽松许可硬门槛。该能力不接触外部数据许可、模型权重、私有持仓或真实来源。
若未来允许 URL 或远程 `$ref`，必须重新完成网络、SSRF、凭据泄漏和可重放性审查。

## 评分

评分遵循“能力 40 + 工程成熟度 20 + 许可 15 + 权利/安全 15 + 集成 10”。

| 候选 | 能力 40 | 成熟度 20 | 许可 15 | 权利/安全 15 | 集成 10 | 总分 |
|---|---:|---:|---:|---:|---:|---:|
| openapi-typescript 7.13.0 | 39 | 19 | 15 | 15 | 10 | 98 |
| `@hey-api/openapi-ts` 0.99.0 | 35 | 18 | 15 | 15 | 7 | 90 |
| OpenAPI Generator 7.24.0 | 34 | 20 | 15 | 15 | 4 | 88 |
| oasdiff 1.25.1 | 40 | 19 | 15 | 15 | 10 | 99 |
| OpenAPITools/openapi-diff 2.1.7 | 34 | 17 | 15 | 15 | 5 | 86 |

## 决定与边界

唯一默认路线是：

- 以精确 npm 开发依赖 `openapi-typescript@7.13.0` 生成静态 TypeScript 类型；
- 生成命令固定输入、输出并启用 `--alphabetize`，漂移检查使用该工具官方 `--check`；
- 生成前递归拒绝所有非内部 `$ref` 和从工作目录向上发现的 Redocly 配置；Node preload 阻断
  HTTP、HTTPS、TCP、TLS、DNS、UDP 和 `fetch`，避免工具解析输入时触网；
- FastAPI 描述生成在独立进程中阻断全部 Python `socket.*` audit event；生成和兼容子进程均有
  固定超时；
- 以固定 OCI index digest 的 `oasdiff v1.25.1` 比较 merge-base 与候选 OpenAPI 快照；
- 保留现有 fetch 封装，不引入 Hey API 或 OpenAPI Generator 生成的 SDK；
- 不安装 OpenAPITools/openapi-diff，不维护第二套 breaking-change 规则。

两项工具均只属于开发/CI 组合根，不进入 contract、Port、领域模块或生产运行时。Pydantic/FastAPI
仍是 OpenAPI 的源码；生成 TypeScript 文件和差异报告都是衍生产物，不能反向定义接口语义。

## 验证

- npm 官方注册表确认 `openapi-typescript@7.13.0`、MIT 和制品 integrity；
- `apps/web/package-lock.json` 固定包版本、完整性和传递依赖；
- 官方 CLI 文档确认 `--alphabetize` 与 `--check`；
- 本地回环探针确认未加约束的 `openapi-typescript` 会解析远程 `$ref`，因此当前集成以外部引用
  负例、隐式 Redocly 配置负例和真实 Node 禁网探针作为强制门禁；
- Python audit hook 的真实 UDP 探针、Node HTTP 探针和 `oasdiff` 已知破坏性变化负例均能失败关闭；
- Docker manifest 实测确认 `tufin/oasdiff:v1.25.1` 的 OCI index digest 为
  `sha256:3a0556fff855d1e1c91ebeedf4800772090cbdb4f319e0b4ee3be549bd5b5072`，并同时包含
  `linux/amd64` 与 `linux/arm64`；
- `api-types:generate` 与 `api-types:check` 已对当前冻结的 `schemas/http/openapi.v1.json` 通过；
- 破坏性变更正反例、真实 merge-base 比较和首次发布路径由 CI/契约 lane 集成。

本次 npm 锁文件解析报告整个前端依赖图存在 4 个 high severity 项；受执行环境策略限制，未把完整
依赖图上传到 npm audit 服务，因此不能归因到新增工具或既有依赖，也不能宣称漏洞门禁已通过。

## 供应链与归属

没有复制第三方源代码、模板、模型或数据。`openapi-typescript` 是仅开发依赖，固定为精确版本并由
npm lock integrity 约束；`oasdiff` 不加入 npm，运行时必须引用完整 tag 和 OCI digest，禁止使用
`latest`。MIT 与 Apache-2.0 初审登记到 `THIRD_PARTY.md`；发布 SBOM 应包含 npm 传递依赖和 oasdiff
镜像 digest。仓库不保存工具下载包、Docker 层或生成缓存。

## 退出与复审触发条件

出现大版本、许可证或维护者变化、重大 CVE、生成结果不稳定、OpenAPI 版本不再受支持、`--check`
语义变化、镜像签名/发行渠道变化，或工具需要联网解析契约时必须复审。退出方式是移除一个精确
devDependency 和一个 CI 镜像调用；Pydantic/OpenAPI 快照、现有 fetch 封装及项目契约不受替换影响。

# 股票智能助手开源项目与组件调研

核验日期：2026-07-22

适用范围：StockMarketAgent 的产品底座、数据采集、证据/记忆、Agent、前后端、本地模型、LoRA、
量化融合、MLOps、可观测性与通知。

结论性质：工程选型初审，不构成法律意见；实际引入时仍须按固定 release/commit 重新核验。

## 1. 执行结论

没有发现一个许可清晰、工程完整且能基本覆盖本计划的现成项目。最高匹配候选仍缺少授权数据 gate、
国家/地方政策映射、事件修订链、三层记忆、point-in-time 评估与 LoRA/量价文本融合。因此唯一工程路线是：

1. **不整体 fork 任何股票 Agent 项目**，保留本仓库的模块化单体、强契约、Port/adapter 和 paper-only 边界；
2. **应用体验首要参考** `ZhuLinsen/daily_stock_analysis`；OpenAshare 只作本地优先工作台的次级参考；
3. **Agent 编排采用** LangGraph，参考 Vibe-Trading 的研究/PIT 安全边界和 TradingAgents 的角色、
   checkpoint 思路，但确定性事实规则归本项目；
4. **授权 A 股数据连接器自行实现**，只复用 HTTP、解析、OCR、队列等通用库；AKShare 仅开发验证；
5. **记忆采用** PostgreSQL + pgvector，R0P 短时缓存运行时已切换到 Valkey；不再增加独立向量数据库；
6. **文档解析采用** Docling，扫描件中文 OCR 以 PaddleOCR 作为有界回退；
7. **去重采用** 内容哈希 + RapidFuzz + datasketch 做候选召回，事实槽位/事件修订由本项目实现；
8. **本地模型采用** Qwen3.5-9B + vLLM；LoRA 训练采用 ms-swift，底层保留 Transformers/PEFT；
9. **量化采用** Qlib + LightGBM + scikit-learn 校准，不采用 RL 或交易执行框架；
10. **训练与数据治理采用** MLflow + DVC；可观测采用 OpenTelemetry + Prometheus；金融 K 线/分时采用
    Lightweight Charts，ECharts 只负责宽度、分布和质量等通用图；
11. **板块/热点子系统参考** AlphaSift，经独立 Port spike 后只选择性适配，不直接接管领域数据与评分；
12. **通知适配采用** Apprise，但浏览器 SSE/桌面通知仍是默认本地通道，第三方通道显式启用；
13. **MinIO 不进入 R1**：R0P Compose 已替换为固定镜像的 SeaweedFS；S3 adapter、版本保留、
    备份恢复、对象锁与故障注入未通过前，仍不得保存真实证据。

这里的“采用”表示唯一目标路线，不表示包、镜像或模型已全部加入当前锁文件。引入状态以
`THIRD_PARTY.md`、锁文件、镜像 digest 和实际代码为准。

## 2. 调研与评分方法

### 2.1 一手证据要求

只把官方仓库、LICENSE、SECURITY、release、官方文档和模型卡作为决定依据。星标只表示社区关注，
不证明安全、数据授权或功能正确。README 宣称的能力若未做本地 spike/contract test，只记为“项目宣称”。

代码许可、模型权重许可、训练数据许可、数据 API 合同和网页抓取条款分别判断。开源 SDK 不会自动授予
其访问的数据之缓存、embedding、训练、推送或再分发权利。

### 2.2 整体项目匹配分（100）

| 能力 | 权重 |
|---|---:|
| A 股数据与授权连接器 | 15 |
| 单主动标的与盘前差异简报 | 12 |
| 国内外政策、隔夜市场与一跳事件传导 | 12 |
| evidence、修订、去重、变化通知 | 13 |
| 短时/情景/语义三层记忆 | 8 |
| 交易结构压力与六维盘面解读 | 10 |
| 聊天、本地/外部 LLM | 8 |
| LoRA 数据、训练与评测 | 6 |
| point-in-time 量价/文本研究 | 8 |
| Web、调度、通知与运维 | 8 |

旧“盘中全板块轮动”权重已取消并移入后续扩展；外部上下文与单标的状态压缩成为当前匹配项。下表分数
已按该范围重新估算，仍只用于候选差距判断。

工程完整度为 0–5：0 是概念，1 是脚本/demo，2 是可运行研究原型，3 是结构化应用，4 是有测试、
CI、容器和发布流程的工程项目，5 是多版本稳定平台。分数是本项目需求下的审计判断，不是通用排名。

### 2.3 决策词

- `adopt`：作为依赖或服务使用；
- `adapt`：只通过本项目 Port/adapter 使用，第三方类型不越界；
- `reference`：借鉴公开设计，不形成运行时依赖、不复制代码；
- `reject`：当前不采用，并给出重新评估触发条件；
- `build`：候选未满足领域不变量，自行实现最小缺口。

## 3. 基本重合项目审计

### 3.1 总体对比

| 项目 | 许可初审 | 匹配分 | 完整度 | 主要重合 | 关键缺口 | 决定 |
|---|---|---:|---:|---|---|---|
| [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | MIT | 54 | 4.0 | A 股、多数据源、单股定时分析、Web、通知、问股、回测 | 权利 gate、跨境传导、修订链、三层记忆、PIT 状态 | `reference` |
| [OpenAshare](https://github.com/ZhiweiChen-coder/OpenAshare) | MIT | 45 | 2.5 | A 股工作台、新闻/热点、关注、Agent、本地优先记忆、FastAPI/Next.js | 仅预发布；权利 gate、证据修订、外部环境和严格 PIT 不足 | `reference` |
| [TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) | 混合许可 | 42 | 4.0 | A 股、多 Agent、FastAPI/Vue、缓存、通知 | 前后端需商业授权；缺权利/PIT/外部影响链 | `reject` |
| [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | MIT | 41 | 4.0 | Agent、PIT 基本面、量化研究、跨会话记忆、调度、Web/SSE/MCP | 非 A 股证据助手；权利、公告修订和一跳暴露不足 | `reference` |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache-2.0 | 32 | 3.5 | 多角色研究、结构化输出、checkpoint、provider | 海外数据假设、无 A 股权利/跨境证据/三层记忆 | `reference` |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | Apache-2.0 | 27 | 2.5 | 金融 Agent、分析任务、报告生成、量化概念 | 美股/API 假设、Notebook/研究导向、非实时助手 | `reference` |
| [AlphaSift](https://github.com/ZhuLinsen/alphasift) | Apache-2.0 | 21 | 3.0 | A 股全市场筛选、热点/板块、可审计 YAML、T+N 评估 | 当前板块轮动已延后；权利、证据、外部环境、记忆和前端不足 | `reference` |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | MIT | 21 | 3.0 | 多 Agent、基本面/情绪/技术/风险、Web/CLI | 美股数据与交易角色，不适配 A 股证据边界 | `reference` |
| [FinMem](https://github.com/pipiku915/FinMem-LLM-StockTrading) | MIT | 19 | 2.0 | 分层记忆、反思、金融 Agent 实验 | 研究交易目标、数据市场和工程运维不匹配 | `reference` |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | AGPL-3.0 | 18 | 4.5 | 数据 provider 平台、标准模型、FastAPI | AGPL 审查、A 股覆盖/数据合同不足、过重 | `reject` |
| [StockAgent](https://github.com/Eleeoeat/StockAgent) | MIT | 14 | 1.5 | AkShare、DeepSeek、Streamlit 财务分析 | 单文件、无完整调度/证据/记忆/外部传导 | `reference` |
| “FinAgent-Stock” | 无唯一仓库 | 不评分 | 不评分 | 用户描述称盘前与基础去重 | 无法核验 URL、许可证、维护和代码 | `reject` |

所有候选均低于“可作为本项目底座”的 70 分门槛，因而不存在功能重复到应停止本项目的情形。

### 3.2 daily_stock_analysis

官方 release 页在本次核验中把 **v3.27.0 / `b36c721`（2026-07-19）** 标为 Latest。该 release
进一步加入只读 Codex 问股原型、历史报告快照重算的可审计 guardrail、结构化多策略观点、基础冲突检测
和输入来源诊断；此前版本已经覆盖告警后台、通知冷却、持仓/自选联动、多种分析策略、相关板块、
新闻相关度分层与多市场数据 adapter。

适合复用/借鉴：

- 持仓、分析任务、报告历史、问股、告警中心和设置页的用户流程；
- provider fallback、任务状态、通知尝试记录和冷却的测试思路；
- 本地/外部模型配置、Docker/桌面部署的使用体验；
- “AI 预测与次日结果”页面的信息设计，但不能复制其评估口径。

不能直接复用：

- 免费网页源作为生产事实源；
- 未附 rights snapshot 的缓存、新闻、研报或全文处理；
- 把报告字符串当事实库，或用相似度直接决定重复；
- 与本项目 contract/Port 不兼容的领域模型和数据库对象。

决定：不 fork、不作为依赖。R3 前固定 v3.27.0 / `b36c721` 做文件级审计；若移植少量代码，保存
原路径、commit、MIT 版权与修改说明，并用本项目契约重新实现。

### 3.3 OpenAshare、Vibe-Trading 与 AlphaSift

OpenAshare 的官方 **v0.1.0 / `fed8a10`** 仍标记为 pre-release。它在交互层与本计划高度相似：
FastAPI + Next.js、A 股单股分析、新闻/热点、持仓、Agent 对话、本地设置和本地记忆。官网出现
“Policy watch”，但仓库发布说明和 README 没有证明存在授权政策连接器、时态地区映射或 evidence
revision，因此该项不能算已实现。其主数据依赖 Ashare/AKShare，存储为 SQLite/本地 JSON，仓库 roadmap
仍列出更完整测试与 CI。决定：只参考页面信息架构、SSE 进度和 portfolio-aware chat，不 fork、不复制
数据链路或记忆实现。

Vibe-Trading 的官方 **v0.1.11 / `835dea7`** 是可发布的 MIT 全栈研究 Agent，包含结构化 Agent、Web、
SSE、MCP、跨会话记忆、定时研究和 filed-date 锚定的 SEC 基本面。它是 Agent/PIT 工程参考中最完整的
候选，但 PIT 能力针对 SEC/海外市场，不能自动迁移为 A 股公告时态语义；同时仓库含 broker connector、
交易工具和可选 shell/file/web 工具，与本项目 paper-only/最小权限边界冲突。决定：参考研究目标、
checkpoint、只读工具和 PIT 测试；不整体依赖，任何 spike 必须先移除交易执行并固定工具 allowlist。

AlphaSift 截至核验日无正式 release，固定审计快照为 main **`9f522747caaf`**。它以 Apache-2.0 提供
A 股全市场筛选、确定性因子、可审计 YAML、热点时间线、leader fallback、保存运行和 T+N 评估；与盘中
板块关注最接近。但其职责是“选股/热点”而非“10 分钟板块新闻证据差异”，README 也明确 T+N 不是
严格事件回测，默认行情链含未获本项目生产授权的网页/免费源。决定：R3 仅通过
`SectorIntelligencePort` 做固定快照 spike，优先适配热点 schema、fallback 元数据和策略解释；来源、
评分、PIT、轮换公平性和事件证据仍由本项目拥有。

### 3.4 TradingAgents 与 TradingAgents-CN

TradingAgents 官方仓库本次可核验到 **v0.3.1 / `01477f9`** 的结构化 Agent、checkpoint resume、
持久决策日志、
多 provider、非美国市场 benchmark 和路径安全加固。适合参考角色划分与中断恢复，但多 Agent 的讨论
不能替代来源授权、事实抽取、冲突检测和确定性校验。

TradingAgents-CN 的功能重合更高，但仓库明确说明 `app/` 和 `frontend/` 不在普通 Apache-2.0 范围，
需商业授权。即使当前不商业化，也不能把未来核心前后端锁定在该混合许可中；禁止复制这些目录。

决定：LangGraph 是唯一编排依赖；TradingAgents 只做隔离参考。TradingAgents-CN 拒绝作为底座，除非
未来取得书面许可且仍有不可替代能力，届时重新做逐文件审计。

### 3.5 FinMem、FinRobot、FinGPT 与 ai-hedge-fund

- FinMem：分层记忆/衰减/反思是论文级参考；不引入交易动作和历史收益目标。
- FinRobot：Apache-2.0，适合参考金融分析任务拆解、报告和评估样例；不作为在线依赖。
- [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)：MIT，包含金融任务、LoRA Notebook、数据与模型；
  只参考任务定义。每个数据集/模型单独核验，不因仓库 MIT 就直接训练。
- ai-hedge-fund：MIT，适合参考 analyst/risk 角色和本地 Ollama UX；其交易组合、FinancialDatasets/
  美股假设不进入本项目。

### 3.6 OpenBB、StockAgent 与不可核验项目

OpenBB 是成熟金融数据 provider 平台，但核心为 AGPL-3.0，provider 的数据条款仍需分别处理；引入会
复制本项目已经稳定的 Port/contract 层，也不能解决 CNINFO/Choice 权利。只参考 provider 扩展设计。

StockAgent 是单文件 Streamlit 价值分析工具，可参考财务指标展示，不复用结构。没有唯一 URL、明确
许可证和可复现 commit 的 “FinAgent-Stock” 直接拒绝，名称相似不能作为供应链身份。

## 4. 分功能成熟实现与唯一采用路线

### 4.1 数据源、政策与来源权利

| 能力 | 候选 | 决定 | 工程边界 |
|---|---|---|---|
| A 股开发字段验证 | [AKShare](https://github.com/akfamily/akshare) | `adapt`，仅开发 | MIT 代码；官方声明数据仅学术研究且接口可能移除。默认禁生产、缓存、训练和推送 |
| 可选规范行情 SDK | [Tushare](https://github.com/waditu/tushare) | `reject` 当前默认 | 权限、配额与服务条款独立；只有用户合同覆盖具体用途时才新增 adapter |
| 生产公告/F10 | CNINFO/交易所正式入口 | `build` adapter | 无开源项目能替代授权；rights gate 先于 fetch/cache/embed/train |
| 资讯/研报/舆情 | Choice 正式合同 | `build` adapter | 合同能力逐项登记；股吧只消费授权聚合情绪，不抓取为公司事实 |
| 国家/地方政策 | 国务院/部委/地方政府官方站 | `build` connector | 条件请求、robots/条款、来源快照、行业/地区 PIT 映射 |
| 通用金融 provider 平台 | OpenBB | `reference` | AGPL 与功能重复，不形成运行时依赖 |

AKShare 官方仓库核验时为 MIT；**2026-07-22 16:12 Asia/Shanghai（08:12 UTC）的 latest API 返回
release-v1.18.73**，而本项目
`uv.lock` 仍固定 1.18.70。快速发布本身不代表生产稳定性，且官方明确“仅学术研究”和接口可能被删除；
因此适合 contract fixture 和字段探索，不是生产授权方案。东方财富/同花顺网页可访问也不等于有开放
API 权利。

### 4.2 网络、解析、PDF 与 OCR

| 能力 | 采用 | 被比较候选 | 决定理由 |
|---|---|---|---|
| HTTP/timeout/retry | httpx + tenacity | requests/自研重试 | 当前已锁定；异步、显式 timeout、有界重试 |
| HTML 正文 | [Trafilatura](https://github.com/adbar/trafilatura) | BeautifulSoup 自研正文规则 | `adopt`；只锁定经核验的 Apache-2.0 release，下载仍由 rights-aware HTTP adapter 完成 |
| HTML 元数据/RSS | BeautifulSoup + feedparser | 全功能爬虫框架 | 当前规模不需要常驻 crawler；来源规则由 rights gate 控制 |
| PDF/Office/XBRL | [Docling](https://github.com/docling-project/docling) | Unstructured、RAGFlow parser | `adopt` 于 R1；MIT、本地执行、布局/表格/XBRL，模型许可另审 |
| 扫描件中文 OCR | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Docling 内置 OCR、MinerU | `adapt` 为失败回退；Apache-2.0、中文结构化能力强 |
| MinerU | 不采用 | — | 新许可证含额外规模/归属条件，先拒绝，避免误标为标准宽松开源 |

Docling 只负责把授权文档转换为带位置的中间结构，不负责判断金融事实。OCR 输出必须保留页面/坐标、
置信度和原对象哈希；低置信度数字不得自动进入事实槽位。

### 4.3 事件、去重与三层记忆

| 能力 | 采用 | 被比较候选 | 决定理由 |
|---|---|---|---|
| 精确内容/来源去重 | SHA-256 + 数据库唯一键 | 自研缓存集合 | 可审计、幂等、跨重启 |
| 标题/别名模糊匹配 | [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | fuzzywuzzy | MIT、性能好，避免 GPL 依赖 |
| 近重复候选召回 | [datasketch](https://github.com/ekzhu/datasketch) MinHash/LSH | 自研 simhash | MIT、成熟；只召回候选，不做最终事实判断 |
| 情景向量记忆 | [pgvector](https://github.com/pgvector/pgvector) | Qdrant | `adopt`；与权限/事件关系数据同事务、同备份 |
| 独立向量库 | Qdrant | pgvector | `reject` 当前；仅 pgvector 有实测 SLO 缺口时重评 |
| 分层记忆设计 | FinMem | 自由 Agent memory | `reference`；生命周期和权利由本项目明确实现 |
| 语义规则 | Git 版本化 YAML | LLM 长期记忆 | `build`；规则需审计、测试和 PIT 版本 |

最终 NEW/DUPLICATE/UPDATE/CORRECTION/CONTRADICTION 判定属于本项目：向量/MinHash 只能找候选，
实体、事件类型、首次可见时间、事实槽位与数值变化决定结果。

### 4.4 主库、缓存、队列与对象存储

| 能力 | 采用路线 | 被比较候选 | 决定 |
|---|---|---|---|
| 事实主库 | PostgreSQL | MongoDB/Elastic | `adopt`；关系、PIT、事务、JSON 和向量统一 |
| 短时缓存/锁/broker | [Valkey](https://github.com/valkey-io/valkey) | Redis 7.2/8 | `adopted in R0P runtime`；BSD-3，生产恢复契约仍待验收 |
| 后台任务 | [Celery](https://github.com/celery/celery) | Temporal | `adopt`；BSD、当前单机规模足够，幂等/outbox 由本项目负责 |
| 持久工作流平台 | Temporal | Celery | `reject` 当前；额外服务与确定性约束过重，只有跨日工作流恢复 SLO 失败才重评 |
| S3 对象存储 | [SeaweedFS](https://github.com/seaweedfs/seaweedfs) | MinIO、Ceph | `adopted in R0P runtime`；Apache-2.0，生产 S3/恢复契约仍待验收 |
| MinIO server | MinIO | — | 已从当前 runtime 移除并拒绝进入 R1；官方仓库 2026-04-25 归档且为 AGPL-3.0 |

Redis 8 是 RSALv2/SSPLv1/AGPLv3 三选一，7.4–7.8 为 RSALv2/SSPLv1；避免长期停在旧 7.2 只为
BSD 许可。Valkey 是明确的长期目标。R0P 已验证容器健康与合成 SET/GET；当前 Python `redis` client
仍通过兼容 adapter 使用，进入 R1 前还必须验证 Celery broker、锁、TTL、限流和故障恢复。

SeaweedFS 已进入 R0P 开发运行时，但真实证据上线前仍必须通过：S3 SDK contract、版本保留/删除
标记、对象哈希、并发写、加密备份、恢复、磁盘满/断电、权限和对象锁测试。

### 4.5 Agent、RAG 与聊天

| 能力 | 采用 | 被比较候选 | 决定 |
|---|---|---|---|
| 有界状态图 | [LangGraph](https://github.com/langchain-ai/langgraph) | AutoGen/CrewAI/自研图 | `adopt`；MIT、checkpoint/中断；节点与工具白名单由本项目拥有 |
| 全套 RAG 平台 | RAGFlow | LlamaIndex/Haystack | `reject` | 重复存储、解析、Agent 和运维，过重且难嵌入 rights/PIT 语义 |
| RAG 库 | LlamaIndex/Haystack | 当前 Port + pgvector | `reject` 当前 | 已有 Retrieval/Generation Port，新增框架会产生第二套 contract |
| 多 Agent 金融流程 | TradingAgents | 自由对话 | `reference` | 只参考角色/恢复；不让 Agent 直接写事实或发送通知 |

唯一聊天链路是：意图 → 授权 → PIT 检索 → 最小证据包 → 结构化生成 → schema/引用/数字校验 →
回答或 abstain。任何第三方 Agent 的 tool schema 都先映射到项目 contract。

### 4.6 本地模型、Embedding、LoRA 与评测

| 能力 | 采用 | 被比较候选 | 决定理由 |
|---|---|---|---|
| 本地基座 | [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | 更大 Qwen/闭源 API | `adopt`；官方模型卡 Apache-2.0，24GB 单卡目标，仍需实测 |
| Embedding | [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | BGE-M3 | `adopt`；中英统一模型族，固定 revision、维度、instruction 与归一化 |
| Reranker | [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | bge-reranker-v2-m3 | `adopt`；只排序候选，分数不是概率或事实置信度 |
| 推理服务 | [vLLM](https://github.com/vllm-project/vllm) | SGLang/Ollama | `adopt`；Apache-2.0、OpenAI-compatible、结构化输出/LoRA |
| LoRA 训练器 | [ms-swift](https://github.com/modelscope/ms-swift) | LLaMA Factory、自写 Trainer | `adopt` R5；Apache-2.0、明确支持 Qwen3.5、LoRA/QLoRA/评测/导出 |
| 底层训练库 | Transformers + PEFT + TRL | 自研 PEFT | `adopt`，由 ms-swift 驱动；避免自写训练循环 |
| 备选训练平台 | LLaMA Factory | ms-swift | `reject` 当前默认；成熟但不保留两套训练入口 |
| 通用 LLM 回归 | [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) | 全自研 | `adapt` 为补充；金融 schema/引用/数字困难集仍由项目实现 |
| 金融任务/数据参考 | FinGPT | 项目自建数据 | `reference`；数据集和模型逐项 rights review |

ms-swift 官方仓库明确支持 Qwen3.5、LoRA/QLoRA、量化、评测和 vLLM，且提醒模型与数据遵循各自
许可证。训练制品默认保留独立 adapter，不把合并权重提交 Git。基座、processor、训练数据、prompt、
规则、代码和 CUDA 环境全部固定 hash。

BGE-M3/FlagEmbedding 仍保留为离线召回基线，但不形成第二套生产索引。模型、维度、instruction、
归一化或切片版本发生变化时创建新版 pgvector 索引，禁止把新旧向量写入同一索引。

### 4.7 量化、回测、融合与 MLOps

| 能力 | 采用 | 被比较候选 | 决定 |
|---|---|---|---|
| PIT 量化研究 | [Qlib](https://github.com/microsoft/qlib) | FinRL、Backtrader、vectorbt | `adopt` 离线研究，MIT |
| 价格/文本模型 | [LightGBM](https://github.com/microsoft/LightGBM) | LSTM/Transformer/RL | `adopt`；小数据、可解释、易校准 |
| 概率融合/校准 | scikit-learn Logistic + sigmoid | LLM 打分加权 | `adopt`；OOF、确定性、可复现 |
| RL 交易框架 | FinRL | Qlib 监督基线 | `reject`；带执行目标、样本效率和稳定性不适合第一阶段 |
| vectorbt | — | Qlib | `reject`；现版为 Apache 2 + Commons Clause，不是标准宽松开源 |
| Backtrader | — | Qlib | `reject`；GPLv3+ 且项目链路重复 |
| 数据版本 | [DVC](https://github.com/iterative/dvc) | 只用文件名/hash | `adopt` R5–R6；Apache-2.0，只提交指针/manifest，不进入在线压力路径 |
| 实验/模型登记 | [MLflow](https://github.com/mlflow/mlflow) | 自研表格 | `adopt`；使用 alias/tag，不使用已弃用 stage 语义 |

Qlib 中出现 order execution 能力不代表本项目启用执行；只使用数据集、特征、模型、滚动研究和
paper backtest。所有输入必须满足 `first_seen_at <= decision_time`，文本特征用 OOF 生成，最后 6 个月
保留集只能开启一次。

### 4.8 Web、图表、通知与可观测性

| 能力 | 采用 | 被比较候选 | 决定 |
|---|---|---|---|
| Web | Next.js/React/TypeScript strict | Streamlit/Vue | `adopt` 当前架构；复杂证据与差异视图需要正式前端 |
| 通用图表 | [Apache ECharts](https://github.com/apache/echarts) | Plotly/Recharts | `adopt` R3；Apache-2.0，只负责宽度、分布和质量等非金融通用图 |
| K 线 | [Lightweight Charts](https://github.com/tradingview/lightweight-charts) | 自绘 canvas/ECharts K 线 | `adopt` R3；Apache-2.0，只负责 OHLCV 并保留许可证要求的署名/链接 |
| UI 基础组件 | [shadcn/ui](https://github.com/shadcn-ui/ui) | 自建组件库 | `adapt`；MIT、源码归项目维护，禁止复制第三方业务规则 |
| 完整聊天壳 | Open WebUI | 当前 Next.js | `reject`；许可证含附加品牌条件且会引入第二套用户/插件/数据边界 |
| 外部通知 | [Apprise](https://github.com/caronc/apprise) | 每渠道自写 SDK | `adapt`；BSD-2，多通道统一，必须 allowlist 与密钥隔离 |
| 本地实时通知 | SSE + Web Notification API | WebSocket 全量双工 | `build` 薄层；当前需求单向，避免无必要协议 |
| traces/metrics | OpenTelemetry + Prometheus | 私有埋点 | `adopt`；开放标准、与统一指标注册表结合 |
| Dashboard | Grafana 可选独立服务 | 自研运维图 | `reject` 作为产品依赖；若部署需单独 AGPL/镜像审查 |

Apprise 不获得读取持仓或原始证据的权限，只接收已经过通知 policy 的最小消息。每个 transport 默认
关闭，凭据来自 secret provider，发送有幂等键、超时、重试上限和审计结果。

### 4.9 供应链、SBOM 与漏洞扫描

| 能力 | 采用 | 被比较候选 | 决定 |
|---|---|---|---|
| SBOM | [Syft](https://github.com/anchore/syft) | Trivy SBOM | `adopt`；Apache-2.0，输出 SPDX/CycloneDX 并随 release 保存 |
| 镜像/文件漏洞 | [Grype](https://github.com/anchore/grype) | Trivy | `adopt`；Apache-2.0，与 Syft SBOM 组合，数据库和二进制固定来源/hash |
| 依赖漏洞交叉检查 | [OSV-Scanner](https://github.com/google/osv-scanner) | 单一 scanner | `adopt`；Apache-2.0，覆盖 lockfile/OSV，结果与 Grype 去重后门禁 |
| Trivy | — | Syft + Grype + OSV-Scanner | `reject` 当前默认；2026-03 官方确认生态供应链被临时攻陷，待重新完成信任审查 |

[Trivy 官方安全公告](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)
记录了恶意 release/action/tag 的暴露窗口。上游已处置不等于永久拒绝；只有完成制品签名、不可变 SHA、
发行流程与隔离 runner 复审，且能减少而非增加工具链风险时才重新评估。任何 scanner 都不得使用
`latest`，漏洞数据库快照和例外必须带过期时间。

## 5. 交易结构压力产品与实现调研

### 5.1 产品匹配度

| 产品 | 与本项目重合点 | 缺口与采用结论 |
|---|---|---|
| [韭圈儿](https://apps.apple.com/cn/app/%E9%9F%AD%E5%9C%88%E5%84%BF-%E6%B8%A9%E6%9A%96%E4%BD%A0%E7%9A%84%E6%8A%95%E8%B5%84/id1517062915) | 恐贪、股债性价、估值和组合回测把复杂数据压成易记状态 | 高度借鉴低认知负担；不复制专有指数、公式、文案或数据 |
| [有知有行温度计](https://youzhiyouxing.cn/thermometer) | 0–100 状态温度、历史位置和指数横向比较 | 借鉴状态表达；本项目不使用概率化措辞，并扩展到个股交易结构 |
| [StockCharts SCTR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stockcharts-technical-rank) | 证券在明确同类总体内得到 0–99.99 相对排名 | 作为“分数是相对位置而非概率”的主要参考；不复制其技术强弱公式 |
| [TC2000 Volume Buzz](https://help.tc2000.com/m/69401/l/862838-how-to-use-today-s-volume-buzz) | 当前成交与历史相同进度比较 | 采用同钟点相对成交语义；不复制名称或把异常成交解释为方向 |
| [TradingView Technical Ratings](https://www.tradingview.com/support/solutions/43000614331-technical-ratings/) | 26 个指标压成多周期评级 | 借鉴分组/确认状态；拒绝 Buy/Sell 和高度相关指标多数投票 |
| [ChartMill Technical/Setup](https://www.chartmill.com/documentation/technical-analysis/indicators/87-Chartmill-Technical-Rating-and-Setup-Quality-scores) | 把趋势健康与当前位置/形态质量分开 | 采用“趋势组织与位置分开”；不复制专有分数和阈值 |
| [Simply Wall St Snowflake](https://support.simplywall.st/hc/en-us/articles/360001740916-How-does-the-Snowflake-work) | 五维摘要、快速比较、逐层下钻，并明确不是买卖建议 | 作为五分面与低认知负担参考；不复制专有检查项、权重和视觉资产 |
| [MarketSurge](https://marketsurge-files.investors.com/2024/07/StockGuide-Mid-year-2024.pdf) | 量价、延伸和吸筹/派发代理强调价格所处语境 | 借鉴“同一强信号也可能过度延伸”的双向解释；拒绝交易者身份化标签 |
| 同花顺、东方财富、雪球 | 筹码、资金、异动、新闻和社区热度覆盖广 | 仅借鉴信息入口；不抓取页面/付费数据，社交只作聚合情绪 |
| 萝卜投研、理杏仁 | 结构化指标、历史分位、同行比较和证据卡片 | 借鉴证据卡片和比较方式；不复制付费数据或专有规则 |
| TipRanks Smart Score | 多来源压缩成单分 | 证明聚合需求存在；拒绝黑箱权重和 outperform/underperform 标签 |

已调研产品中，没有一个同时提供“可追溯事件 evidence + 个股交易结构相对状态 + PIT 历史相似情景 +
变化提醒 + 本地/权利失败关闭 + 非概率表达”。本项目不靠新增更多技术指标差异化，而靠把这六件事放在
同一审计链上。

### 5.2 学术结论对产品的约束

- [Llorente 等价量研究](https://www.nber.org/papers/w8312)表明，量价关系在风险分担和信息交易语境下
  可能有不同后续含义，不能把放量上涨写成必跌规则；
- [中国市场价量关系研究](https://www.sciencedirect.com/science/article/pii/S0927538X24001410)
  提供中国样本证据，但机制与美国市场并不完全相同；
- [Barber–Odean 注意力研究](https://academic.oup.com/rfs/article-abstract/21/2/785/1607197)
  支持把新闻、异常成交和极端收益视为散户注意力代理，仍不能据此识别交易者身份；
- Amihud、Corwin–Schultz、Grinblatt–Han 和 Chen–Hong–Stein 分别提供低频流动性、价差、参考成本和
  负偏形态代理。它们是可检验特征，不是因果事实或当前方向概率。
- [Lo、Mamaysky 与 Wang](https://www.nber.org/papers/w7613)支持将图形客观化，但
  [Sullivan、Timmermann 与 White](https://ideas.repec.org/a/bla/jfinan/v54y1999i5p1647-1691.html)
  说明从大量技术规则中挑选历史最佳者会产生数据窥探；因此不自动导入数百指标。

### 5.3 开源实现与唯一采用路线

| 能力 | 候选 | 决定 | 边界 |
|---|---|---|---|
| 标准技术原语 | [TA-Lib Python](https://github.com/TA-Lib/ta-lib-python) | `adopt` planned | BSD-2-Clause；未来固定 release/hash，并以 golden vectors 验证，不把第三方类型泄漏到领域层 |
| 批量窗口/横截面/Parquet | [Polars](https://github.com/pola-rs/polars) | `adopt` existing optional | 继续用于 adapter/批处理；公式语义由项目拥有 |
| PIT 因子研究 | [Microsoft Qlib](https://github.com/microsoft/qlib) | `adapt` existing optional | 隔离离线研究，不进入 API/worker 在线关键路径 |
| 历史候选召回 | PostgreSQL/pgvector | `adapt` existing | 只召回，应用层精确距离和 PIT 复核；行为维度冻结后建表，不混入文本 1024 维表 |
| 精确近邻/基线 | scikit-learn | `adopt` existing optional | 只用于离线精确核验和研究基线 |
| 回归/HAC 核验 | [statsmodels](https://github.com/statsmodels/statsmodels) | `reference` | BSD-3；Rolling OLS/Newey–West 候选，本轮不新增依赖 |
| 路径 motif/discord | [STUMPY](https://github.com/stumpy-dev/stumpy) | `reference offline` | BSD-3；只有 PIT/性能/增量通过后才可进入相似情景 |
| 变化点 | [ruptures](https://github.com/deepcharles/ruptures) | `reference offline` | BSD-2；断点会随新数据修订，不进入在线 V1 |
| 宽特征发现 | [tsfresh](https://github.com/blue-yonder/tsfresh) | `reference offline` | MIT；只发现候选，多重检验后仍需人工语义/冗余闸门 |
| 财务报告 demo | [FinReport](https://github.com/frinkleko/FinReport) | `reference` | MIT 但已归档且说明为 demo，不作为生产基础 |
| 技术指标合集 | pandas-ta | `reject` current | 许可/发行状态需单独复核，且与 TA-Lib 角色重复 |
| Pandas 技术指标库 | [bukosabino/ta](https://github.com/bukosabino/ta) | `reject` default | MIT 但与 TA-Lib 重复，且默认预填 NaN 与项目 abstain 语义冲突 |
| 回测框架 | vectorbt | `reject` | Apache-2.0 + Commons Clause，source-available 且与 Qlib 角色重复 |
| 金融 ML 公式库 | mlfinlab | `reject` | 当前仓库为 all-rights-reserved，不能作为可商用开源依赖 |

项目自研的最小边界是 Amihud、Corwin–Schultz、CGO、NCSKEW、分粒度参照总体、mid-rank、十贡献簇、
六维盘面状态、历史情景 PIT 复核和分档状态机。原因不是通用数学库不存在，而是没有成熟库同时拥有本
项目的 A 股规则、rights、PIT、版本、abstain、去重复计票和非概率产品语义。通用滚动、存储、向量和
技术原语仍优先复用。

### 5.4 当前采用状态

本轮只做规划：没有把 TA-Lib 加入 `pyproject.toml`/`uv.lock`，没有复制第三方代码，也没有创建 adapter。
实际引入时必须重新核验官方 release、BSD 许可证、wheel/原生制品 hash、传递依赖、CVE 和 Python 3.12/
目标平台支持，并完成 contract test 与退出方案。

深层指标逐项公式、产品和拒绝清单见
[`technical-indicator-landscape.md`](technical-indicator-landscape.md)；角色、参照总体和状态只在
[`behavior-indicator-catalog.md`](../data/behavior-indicator-catalog.md) 维护，避免本总览成为第二套公式源。

## 6. 全球外部市场与跨境事件

跨市场研究支持观察海外完成时段和 A 股开盘反馈，但溢出短暂、行业异质并随危机状态变化；生产网络和
地缘风险研究支持一跳投入/产出、商品、金融条件、制裁、技术与物流渠道，却不支持用事件关键词直接
预测证券方向。专题证据和官方来源清单见
[`global-market-and-cross-border-events.md`](global-market-and-cross-border-events.md)。

| 能力 | 候选 | 决定 | 边界 |
|---|---|---|---|
| 跨交易所会话 | exchange_calendars | `adapt planned` | Apache-2.0；用于生成/校验，官方交易所日历仍是事实权威 |
| SDMX 协议 | sdmx1 | `adapt/reference planned` | Apache-2.0；只有端点权利允许时才引入 |
| 一跳事件关系 | PostgreSQL 双时态表 | `adopt existing` | 不引入 Neo4j；事实、推断和反馈独立 revision |
| 地缘候选发现 | GDELT | `reference offline` | 不确认公司事实，不进入生产推断的唯一证据 |
| 制裁聚合 | OpenSanctions | `reference` | 数据模型可参考；聚合数据企业许可另行核验 |
| 冲突事件集 | ACLED | `reject default` | 许可和前期单股票范围不满足默认采用条件 |
| 海外行情快捷源 | yfinance | `reject production` | 代码许可不授予上游行情的派生/展示权利 |
| 全球终端框架 | OpenBB | `reject default` | AGPL-3.0，重复 provider 边界且不解决数据许可 |

外部环境的唯一采用路线是：现有授权供应商行情＋国际官方事实＋公司披露暴露＋项目自建跨时区/PIT/
支持反证语义。六个观察块逐序列标准化，不形成第二总分，不进入交易结构压力。完整决策见
[`2026-07-22-external-context.md`](reuse-decisions/2026-07-22-external-context.md)。本轮不安装依赖、
不启用数据源，也不登记 planned 接口或指标。

## 7. 哪些能力必须自行实现

以下不是“重复造轮子”，而是没有成熟项目同时满足的项目领域不变量：

1. 来源 rights snapshot，以及 fetch/cache/retain/embed/train/export/delete 分动作失败关闭；
2. A 股证券、公司、行业和注册/经营地区的 point-in-time 主数据；
3. 交易所公告优先于研报/媒体/社交的证据等级、冲突与更正语义；
4. document → fact slot → canonical event → revision 的不可变证据链；
5. NEW/DUPLICATE/UPDATE/CORRECTION/CONTRADICTION 与实质变化阈值；
6. 盘前差异简报、通知幂等、冷却、重大变化升级和重复抑制；
7. 单标的 10 分钟反馈；weighted-fair sector queue、持仓板块常驻和 starvation limit 延后复审；
8. 政策文件对行业/地区/公司的时态映射；
9. paper-only 预测契约、purged rolling split、文本 OOF 和融合质量闸门；
10. 引用、数字、股票代码、日期、冲突和拒答的确定性校验。
11. 交易结构压力的 A 股 PIT 参照总体、五分面/十贡献簇、历史相似状态、条件化解释和非概率语义；
12. 六维盘面结构、同钟点 RVOL/VWAP 接受、连续两窗口分档状态及站内提醒抑制。
13. 跨时区相同截止窗口、长假/DST/休市/未定价事件、六块外部状态和非概率解释；
14. 官方事实→固定渠道→一跳公司暴露→目标影响→开盘反馈的独立修订链。

自研这些领域规则时仍必须复用 Docling、RapidFuzz、datasketch、pgvector、Celery、ms-swift 等通用能力。

## 8. 采用顺序与工程闸门

### R1 前必须处理

1. R0P Compose 已移除 Redis/MinIO，并锁定 Valkey/SeaweedFS 镜像 digest；
2. 完成 Valkey broker/cache/锁/TTL 与故障恢复 contract spike；
3. 完成 SeaweedFS S3/版本/校验和/备份恢复/对象锁 spike；
4. 不通过这些 spike 不进入授权全文采集，不能把控制面健康冒充证据存储可用。

### R1–R2

1. 引入 Docling；PaddleOCR 只在扫描件分类命中时加载；
2. 引入 RapidFuzz/datasketch；建立 1,000 对重复判断金标；
3. pgvector 与确定性事件修订共同验收，不能只测 recall；
4. 每个新 provider 单独建复用/权利记录和 contract suite。
5. 国际官方来源先用合成 fixture 贯通事实/影响 revision；真实海外行情需额外通过派生展示和模型用途权利。

### R3–R4

1. 金融 K 线/分时引入 Lightweight Charts，宽度/分布等通用图引入 ECharts，并引入 Apprise adapter；
2. 固定 LangGraph 节点、tool allowlist 和 checkpoint schema；
3. R4 先完成市场数据权利、PIT、指标目录/冗余门禁、TA-Lib/论文公式 spike 和确定性状态闭环；
4. TA-Lib 只有在固定版本/hash、golden vectors 和 adapter 边界通过后才从 planned 变为 present。

### R5–R6

1. R5 只保留 ms-swift 一个训练入口，DVC 管数据指针，MLflow 管实验和 alias；
2. lm-eval 只补充通用回归，本项目困难集决定发布；
3. R6 的 Qlib/LightGBM 离线运行，不被 API/worker 在线依赖；
4. 交易结构压力不等待 LoRA，也不因 R6 概率研究改变自身口径；
5. FinRL、vectorbt、Backtrader 和深度时序模型不进入第一轮实现。

每项采用前都要完成：固定版本/hash、LICENSE/NOTICE、传递依赖/SBOM、CVE、数据/模型权利、Port
边界、contract test、性能基准、故障降级、备份恢复和退出方案。未完成时状态只能是“选定待引入”。

## 9. 许可与数据权利红线

### 9.1 初始许可策略

- 优先：Apache-2.0、MIT、BSD、PostgreSQL License；
- 条件审查：GPL/AGPL/SSPL、Commons Clause、source-available、混合许可；
- 默认拒绝：无许可证、身份不唯一、禁止预期用途、归档的安全关键运行时、无法界定专有目录。

“可商用”在本调研中只表示许可证未明显禁止预期使用，不是法律保证。真正商业发布前仍要法律复核、
NOTICE/SBOM、商标、专利、出口、隐私、模型/数据和传递依赖审查。

### 9.2 数据与模型分离

- AKShare MIT 只覆盖代码，不覆盖东方财富等上游数据；
- Tushare client 与 Tushare Pro 服务权限分开；
- Docling/PaddleOCR 代码许可不自动覆盖下载的模型或训练数据；
- Qwen 基座、Embedding/Reranker、LoRA 数据和派生 adapter 分别登记；
- GitHub 项目中的截图、示例报告、prompt 和数据不能在没有明确授权时复制；
- 任何开源 Agent 默认的网页抓取、遥测、交易和第三方发送行为都必须先关闭再审计。

## 10. 维护与复审

实际引入时在 `docs/research/reuse-decisions/` 建立能力级记录，并更新 `THIRD_PARTY.md`。以下事件强制
复审：大版本、许可证/条款变化、仓库归档、重大 CVE、维护者/发行渠道变化、数据端点变化、模型卡变化、
连续 SLO 失败或退出成本显著增加。

本调研不会永久批准 `latest`。生产只接受固定版本、commit/revision、制品 SHA-256 或镜像 digest，且
依赖更新必须重复官方证据核验与 contract test。

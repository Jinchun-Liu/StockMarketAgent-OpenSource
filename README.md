# StockMarketAgent

面向 A 股研究与纸面验证的证据驱动助手源码：Python/FastAPI、Celery、PostgreSQL、Next.js，配有强类型契约、来源权限检查、证据追溯与 point-in-time 约束。

**本仓库是 Apache-2.0 公开源码分发版，不是已部署服务，也不代表真实数据研究链路或生产资格已验收。** 项目不接入真实下单、不自动交易、不承诺收益。

## 五分钟运行离线演示

需要 Python 3.12 和 uv；演示不需要 API 密钥、数据库、Docker、真实数据或外网调用。

```sh
git clone https://github.com/Jinchun-Liu/StockMarketAgent-OpenSource.git
cd StockMarketAgent-OpenSource
uv sync --frozen --extra dev
uv run make demo
```

演示输出一份明确标为合成数据的盘前更正简报，展示 `evidence_id`、历史可见时间、更正关联，以及真实来源的 `blocked_rights` 状态。合成样例不代表真实公司研究结果。

```sh
uv run make doctor        # 只读诊断；完整工具链还需要 Node 22 和 Docker
uv run make public-check  # 公开源码的静态、契约、权限和离线演示检查
```

## 源码与运行范围

| 目录 | 内容 |
| --- | --- |
| `apps/` | API、worker、scheduler 与 Next.js 页面入口 |
| `src/stock_assistant/` | contracts、ports、领域规则和基础设施适配器 |
| `schemas/` | Pydantic 生成的公共契约与 OpenAPI |
| `infra/migrations/` | 数据库迁移，历史迁移保持只增不改 |
| `deploy/` | 通用 Docker 和基础设施模板，使用者需独立配置与授权 |
| `tests/` | 保留的单元、契约、迁移、恢复和端到端测试源码 |
| `docs/` | 产品、架构、来源策略与工程设计参考 |

完整股票搜索和部分集成测试依赖证券目录快照。**该快照未随源码分发**，因为原权限仅覆盖私有、非商业研究中的下载和保留，不包含公开再分发。请先阅读 [数据说明](docs/public-source-distribution.md)，在自己的合法使用范围内显式生成本地目录。缺少目录时，不应把搜索失败或无法执行的测试说成能力已完成。

R1–R6 研究来源维持 `DEV_ONLY/UNVERIFIED` 标签；R7 来源授权失败关闭。事实必须关联证据；事实、系统推断与概率预测分别表达。模型权重、真实研究报告、原始数据和任何密钥均不分发。

## 验证与贡献

公开 CI 运行 `make public-check`，明确覆盖源码分发与离线入口。它不宣称完成依赖私人数据、真实来源、外部模型、云端部署或完整 Docker 产品链路的验收。原有测试与内部集成命令保留，完整执行需要另行准备其全部合法数据、运行环境与项目上下文；没有把它们删除、降阈值或伪报为通过。

贡献前请阅读 [AGENTS.md](AGENTS.md)、[CONTRIBUTING.md](CONTRIBUTING.md)、[接口规范](docs/architecture/interface-standards.md) 与 [安全政策](SECURITY.md)。公开仓库不继承原作者的账号、私有部署或来源授权。

## 许可证

项目代码采用 [Apache License 2.0](LICENSE)。第三方素材保留各自许可证，见 [NOTICE](NOTICE) 与 [THIRD_PARTY.md](THIRD_PARTY.md)。代码许可证不授予第三方数据、API、模型权重或商标的使用权。

English: Evidence-grounded A-share research software for research and paper validation only. Run `uv sync --frozen --extra dev` followed by `uv run make demo` for the deterministic offline example. Private datasets, credentials, deployment records and original Git history are excluded; full instrument search requires a separately authorized local catalog. No live trading or production-readiness claim.

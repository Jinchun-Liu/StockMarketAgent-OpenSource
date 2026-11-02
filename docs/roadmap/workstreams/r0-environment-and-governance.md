# R0：环境与治理实施规格

状态：已完成（当前基线）；完成事实仍以新鲜的 `make doctor`、`make demo` 和 `make verify` 结果为准。

## 用户结果

新开发环境运行 `make doctor` 后得到清晰的通过、警告或失败结果；运行 `make demo` 后得到一份完全合成、确定性、可重复的盘前更正简报，即使没有数据授权、Docker 服务或 LLM 也能理解首个闭环。

## 非目标

- 不连接真实来源，不读取密钥、`.env` 或持仓；
- 不启动容器、不下载模型、不修改系统配置；
- 不新增公共接口、Port、Schema、迁移或指标。

## 实现顺序

1. 先为 doctor 的版本解析、缺失工具和仓库内 uv 回退写失败测试；
2. 用 Python 标准库实现只读检查，失败输出同时给出原因和修复动作；
3. 先为合成简报的 PIT、`CORRECTION`、来源权利缺口和确定性写失败测试；
4. 复用现有 briefing/event 契约和事件比较规则生成 demo；
5. 把命令接入 Makefile 和 README/runbook；
6. 运行单元测试、治理检查和完整 `make verify`。

## 验收

- Python <3.12、Node <22、缺少 npm/Docker Compose/uv 时为失败；可选 GPU 不满足仅警告；
- doctor 不写文件、不发网络请求，退出码 0 表示必要条件都满足，退出码 1 表示至少一个必要条件失败；
- demo 连续运行输出一致，不包含 forecast，不调用 LLM；
- demo 中法定来源候选显示 `blocked_rights`，合成来源明确标记为测试数据；
- 生成的简报只引用 `first_seen_at <= as_of` 的证据。

## 复用与标准检查

标准库足以完成环境探测，现有契约和 `compare_event_revision` 足以完成合成演示，因此不引入第三方依赖或新复用决策。接口、指标、迁移、Schema、Dashboard 和告警均不受影响；文档、测试和 Makefile 受影响并同步更新。

## 降级与撤销

doctor 单项超时应报告失败而不是阻塞。demo 只依赖项目已锁定的核心依赖；若核心依赖未安装，先运行 doctor 并按提示安装。删除命令接线即可撤销，不涉及持久化数据迁移。

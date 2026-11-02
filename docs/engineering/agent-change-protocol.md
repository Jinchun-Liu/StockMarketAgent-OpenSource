<!-- governance-doc:agent-change-protocol:v1 -->

# Agent 修改协议

状态：`approved`

## 1. 修改前

1. 阅读根 `AGENTS.md`、工程索引、复用优先规范及本任务相关事实源；
2. 检查当前工作树，保留用户和其他工作包改动；
3. 检索现有实现、`governance/interfaces.json`、`governance/metrics.json` 和复用登记；
4. 写明 `User Capability`、`Runnable Path`、`Acceptance Evidence` 和 `Non-goals`；
5. 明确风险、依赖、精确写范围和聚焦验证命令；
6. 从一个干净的 `codex/<department>/<stage>-<feature>` 短分支开始；
7. 跨边界修改必须由相应单写区 Steward 冻结契约。

诊断不自动授权修复，修改不自动授权提交，提交不自动授权推送或发布。对应外部动作须有用户授权；必要 GitHub 同步沿用根 AGENTS.md 的持续授权，不逐次询问。

## 2. 修改中

- 先写失败测试，再做最小实现；
- 只在 effective capabilities 内写入；
- 外部 I/O 有超时、有限重试和幂等；
- rights gate、evidence、PIT 和双时态不因开发便利降级；
- 不在路由、任务、脚本或前端复制领域规则；
- 不修改已发布迁移、生成 Schema 或生成前端类型；
- 发现契约或范围不足时停止，创建依赖包；
- 不通过关闭 Hook、降低门槛、删除测试或增加忽略项通过检查。

## 3. 权威范围检查

Hook 只负责运行前拦截。执行结束必须检查：

```text
base..HEAD committed diff
staged diff
unstaged diff
untracked files
```

任何路径超出明确写范围都必须停止并修正；不得以 Agent 解释扩大范围，也不得创建 quarantine 分支。

## 4. 审查

聚焦测试先完成，独立 Reviewer 再只读审查最终 HEAD。Reviewer 只获得规格、base、最终 HEAD、累计
diff 和测试结果，不读取 implementer 推理。普通工作包只有一次审查和一次原分支修复机会；修复后
重新运行聚焦检查和审查。再次阻断时缩小范围或交回项目所有者，不创建 attempt 或新的审查层。

高风险边界可增加一次针对性的异构复核；数据授权、迁移、破坏性契约和正式发布由项目所有者批准。

## 5. 标准同步矩阵

修改任何接口、任务、事件、Schema、迁移或统一指标时，必须同时检查：

- 事实源和版本；
- 生产者、消费者与 adapter；
- 生成物；
- 注册表；
- 迁移/兼容/恢复；
- 测试；
- 文档、运行配置和告警。

只更新真正受影响的事实源；不得为无影响项生成收据、报告或机械同步。语义对应项存在时必须复用。

## 6. 完成

1. `make package-check BASE_REF=<base> WRITE_SCOPES="<paths>" TESTS="<pytest targets>"`；
2. 不同 Agent 用 `make agent-review BASE_REF=<base> TARGET_COMMIT=<final-head> \
   PROMPT_FILE=<task-file> MODEL=<model>` 做一次只读审查；
3. QIR 将该最终 HEAD 合入唯一 integration candidate；
4. QIR 在候选完成后运行一次 `make integration-check`；
5. 报告运行结果、验证、风险和恢复方式。

普通包不创建 control/attempt/seal/handoff/receipt/package tag，也不要求远端工作分支。QIR 串行
优先快进 integration；完整本地门禁通过且
所有者明确授权时，本地 `main` 可 `--ff-only` 前进为未发布里程碑。阶段验收、普通远端 CI 和正式
发布批准前不得推送远端 `main` 或创建阶段标签。发布后只用 revert 前向恢复。

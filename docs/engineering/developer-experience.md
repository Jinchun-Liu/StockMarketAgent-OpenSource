# 开发体验规范 v2

状态：`approved`

目标是让个人开发者和多个 Agent 在五分钟内知道“能否开始、在哪里写、怎样验证”，避免治理本身成为
产品。

## 1. 常用入口

```bash
make doctor
make demo
make verify-fast
make verify
```

工作包：

```bash
make agent-run PROMPT_FILE=<task-file> MODEL=<model>
make package-check BASE_REF=<base> WRITE_SCOPES="<paths>" TESTS="<pytest targets>"
make agent-review BASE_REF=<base> TARGET_COMMIT=<head> \
  PROMPT_FILE=<task-file> MODEL=<model>
make integration-merge SOURCE_BRANCH=<branch> REVIEWED_COMMIT=<head>
make integration-check
```

旧 `agent-doctor`、`agent-bundle`、seal、receipt 和 legacy release 状态机不再是活动入口。

## 2. 失败信息

所有入口必须给出：

- 哪个 branch/base/final HEAD 不匹配；
- 哪个路径越过明确写范围；
- 哪个依赖、测试或审批缺失；
- 当前状态能否停在 `locally_verified`；
- 若不能发布，明确说明 `main` 和 stage tag 未移动；
- 可执行的下一步，不输出内部密钥或原始模型日志。

## 3. 本地与远端解耦

远端离线或 CI 失败时，本地工作包仍可开发、测试和独立审查。远端只控制：

- `ci_verified`；
- 正式 `main --ff-only`；
- annotated stage tag；
- `released`。

不得因缺少远端发布证据阻断第一条产品纵向闭环，也不得把本地通过冒充正式发布。

## 4. 隔离

- 最多五个可写 worktree：三个后端功能域、一个 FEX、一个 QIR integration；
- 每个 worktree 独立虚拟环境、Node 输出、缓存、测试数据和 Compose project；
- 启动后输出实际本机 URL；
- 只销毁精确项目资源；
- `agent-run` 使用拒绝网络且只允许当前 worktree 写入的 sandbox；
- 聚焦测试后复检真实 Git diff；
- 写槽按真实 Git worktree 计数，integration 始终串行。

## 5. 新手路径

新工作包的标准路径只需：

1. 写明四个 Product-First 字段、base、范围和聚焦测试；
2. 运行 `agent-run`；
3. 运行 `package-check` 并查看最终 HEAD；
4. 用独立身份运行 `agent-review`；
5. 交给 QIR 串行 优先快进集成。

不要求开发者手工组合六个角色、维护 seal、推送功能分支或创建 package tag。

## 6. 质量

工作包只运行聚焦检查；QIR 在 integration 上执行 `make integration-check`，其等价于完整
`make verify`。不得以 README、skip 或占位测试替代真实 integration/E2E。

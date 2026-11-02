<!-- governance-doc:agent-invocation-protocol:v1 -->

# Product-First Agent 调用协议

状态：`approved`
机器事实源：`governance/delivery.json`

本协议只规定普通研发任务的最小调用路径。产品接口、Port、任务、事件、Schema、指标和迁移仍以既有
事实源为准；蓝图不能反向推断执行状态或权限。

## 0. 当前有效流程

普通任务只有一条路径：

```text
User Capability / Runnable Path / Acceptance Evidence / Non-goals
→ 一个干净短分支内实现
→ 聚焦测试、必要 lint/type
→ 一次独立只读审查最终 HEAD
→ 最多一次原分支修复并重检
→ QIR 优先快进合入 integration
→ candidate 完成后一次 make integration-check
```

普通任务不创建 manifest、receipt、control/attempt 分支、seal、handoff、package tag 或永久 review
worktree。`make agent-run` 是可选薄包装；人类或 Codex 也可以直接在干净短分支实现。

## 1. `make agent-run`

调用形式：

```bash
make agent-run \
  PROMPT_FILE=<task-file> \
  MODEL=<model>
```

包装器只检查：当前目录属于本仓库、工作树干净、当前分支不是 `main`/integration、任务文件存在；
随后启动一次 Codex。它不创建提交、worktree、分支、hash、receipt 或治理状态，也不运行完整门禁。
`PROMPT_FILE` 必须位于仓库内且不属于 protected path；本地临时任务可放在忽略的 `.runtime/tasks/`。

## 2. `make package-check`

调用形式：

```bash
make package-check \
  BASE_REF=<base> \
  WRITE_SCOPES="<path ...>" \
  TESTS="<pytest target ...>"
```

检查器只验证当前分支不是 `main`、`base..HEAD`/staged/unstaged/untracked 的真实路径均在明确范围内，
然后运行调用者指定的 pytest 目标。不得接受 shell 命令、完整 `make verify` 或网络测试作为普通包
聚焦命令。

## 3. `make agent-review`

Reviewer 必须是不同 Agent 会话，只获得任务四字段、base、最终 diff 和测试结果。调用前必须提交实现，
并确保 `TARGET_COMMIT` 同时等于当前 HEAD 和 source branch tip：

```bash
make agent-review BASE_REF=<base> TARGET_COMMIT=<final-head> \
  PROMPT_FILE=<task-file> MODEL=<model>
```

审查只读运行并输出 `APPROVED` 或 `CHANGES_REQUESTED` 及 findings。命令结束前后重新核对 HEAD；不生成
receipt 或永久 review worktree。修复后只允许在原分支追加一次，再重跑 package-check 和 review。

## 4. Integration 与发布

QIR 只接受最终 HEAD、聚焦检查结果和针对该 HEAD 的独立审查结论。按依赖顺序执行：

```bash
make integration-merge SOURCE_BRANCH=<branch> REVIEWED_COMMIT=<head>
make integration-check
```

`integration-merge` 只在干净的 `integration/**` 分支执行一次 `--ff` 合并；不 push、不创建 tag、
不移动 `main`。完整 `make verify` 只在 candidate 完成后运行一次。项目所有者明确授权后，`main`
仅以 `--ff-only` 前进；正式远端发布仍需普通 CI 成功、所有者批准和 annotated stage tag 回读。失败只用同一
分支追加修复、精确 `git revert` 或迁移前向修复，不重写共享历史。

### 私人真实资料预览的普通集成推送

当前R2真实资料私人预览授权允许非强推集成候选。现有agent_guard仅对此接受一条完整命令：
`git push origin <完整40位SHA>:refs/heads/<登记的integration分支>`，工具执行目录必须为加载策略的同仓库已登记worktree根；
宿主缺少工具cwd时使用精确绝对`git -C <worktree> push origin <SHA>:refs/heads/<integration>`。
来源分支tip及审核SHA、完整门禁SHA、当前干净integration分支、授权阶段/环境/锚点必须匹配既有delivery登记。
原origin URL与Git解析后的唯一推送URL都必须等于governance/standards.json的project.repository_url；
登记的是既有https://github.com/Jinchun-Liu/StockMarketAgent.git，重定向到其他仓库或多个推送地址仍拒绝。
允许门禁后的任务说明和delivery状态提交，但推送对象仍是登记的已验证SHA；其他未验证内容不能混入。
强推、其他remote/ref、main、标签、额外Git选项、组合shell及隐式mirror/followTags仍拒绝。未配置的这两项
Git选项按默认false处理；非法值失败关闭。此规则不扩展来源或研究样本授权，不替代宿主信任及远端检查。
仅修改目标worktree脚本不会改变宿主hook加载位置；应回读实际加载路径，只有当前任务明确授权后才将同一审核版本
同步既有入口，不改hooks定义或宿主信任。损坏worktree登记的单文件属性诊断限定见工程入口；不允许用诊断能力绕过
推送的登记或完整门禁。

## 5. Legacy

旧 H/S、manifest、receipt、handoff、tag 和工作分支保留在 Git 历史中，只能用 Git 读取。仓库不再
提供 legacy executor、parser、兼容 Make 目标或状态迁移引擎。

<!-- governance-doc:parallel-development-protocol:v1 -->

# 六部门并行开发与串行集成协议

状态：`approved`
机器事实源：`governance/delivery.json`

## 1. 基本模型

本项目保持模块化单体。“分布式研发”表示多个隔离工作包并行，不表示提前拆微服务。

- 六部门是责任标签，不是六条长期分支；
- 只有交付真实制品的部门建立工作包；
- 默认最多 `3 个后端功能域 + 1 个 FEX 前端域 + 1 个 QIR integration 域`；
- Reviewer 和只读调研不占写槽，也不创建实现分支；
- 公共契约、迁移、运行时组合、依赖和发布继续使用单写区；
- integration 永远串行，`main` 永远只做 `--ff-only`；
- 已共享历史只用 revert 前向恢复。

工作分支仍采用：

```text
codex/<department-lower>/<stage-lower>-<feature>
```

分支是短生命周期容器。普通包集成后删除普通远端分支；不创建 package tag。只有正式阶段发布保留
annotated tag。已存在 legacy 临时分支和标签按本页 Git 与云端精简规则封存清理；共享提交历史不改写。

## 2. 三类门禁

```text
development_gates → locally_runnable / implementing / locally_verified
integration_gates → integration_candidate
release_gates → ci_verified / released
```

Development gate 只要求本地可验证事实：冻结契约、精确范围、短分支、依赖和测试。
远端不可用不得阻断 R1 本地产品开发。

Integration gate 要求所有输入都是最终 HEAD，独立审查绑定最终 HEAD，QIR 按依赖顺序优先快进合入
唯一候选并在
候选上运行完整门禁。

最终不变量只有一个：Reviewer 审查的 commit 必须等于 source branch 的最终 HEAD。修复只允许在
同一分支追加一次，随后重新执行聚焦验证和审查；再次阻断时缩小范围或交回项目所有者。

Release gate 才要求普通远端 CI、远端回读和所有者批准。项目所有者可以在完整
本地门禁通过后，明确授权把**本地** `main` 以 `--ff-only` 前进到候选，作为未发布里程碑基线；该动作
不产生 `ci_verified`/`released` 状态，也不允许推送远端 `main` 或创建正式阶段标签。远端任一项失败时，
候选和本地基线可以保留，但远端 `main` 与正式阶段标签不得移动。

## 3. 工作包波次

阶段内按以下拓扑调度，而不是让六部门同时写：

```text
PAE 需求与契约包冻结
→ DMI / BAE / QMR 中最多三个后端功能域
→ FEX 基于同一契约 fixture 独立开发
→ QIR 运行时组合
→ QIR 独立 integration/E2E/recovery
→ 所有者批准后的正式发布
```

一个部门在一个阶段没有具体输出时不建包。纯解释性文档优先随拥有该事实源的实现包更新，不单独制造
永久分支。

## 4. 单写区

固定责任不变：

| 单写区 | 责任部门 |
|---|---|
| `contract_boundary` | PAE |
| `migration_boundary` | DMI |
| `frontend_boundary` | FEX |
| `runtime_release_boundary` | QIR |

活动权限不由部门名称自动获得，只由轻量工作包登记和规范化 capability 交集产生。发现共享边界缺口时，
当前工作包停止；责任部门创建新依赖包后再恢复。

## 5. Worktree 隔离

- 每个可写包使用唯一 worktree 和短生命周期工作分支；
- integration worktree 占一个写槽；
- Python、Node、缓存、测试数据库、Compose project 和宿主端口隔离；
- 销毁操作必须按真实路径和精确项目 label 定位；
- 禁止全局 prune、宽泛 glob 或删除其他包资源；
- 同一 worktree 同一时刻只有一个可写 Agent；
- Reviewer 在不同 Agent 会话中以只读 sandbox 审查最终 HEAD。

运行前核对槽位和分支，运行后 Git diff 是权威证据。Hook 是第一层拦截，不能代替最终
`base..HEAD + staged + unstaged + untracked` 检查。

## 6. 工作包交付

新轻量包的最小流程：

```text
写明 User Capability / Runnable Path / Acceptance Evidence / Non-goals
→ 在短分支实现
→ make package-check BASE_REF=<base> WRITE_SCOPES="<paths>" TESTS="<pytest targets>"
→ make agent-review BASE_REF=<base> TARGET_COMMIT=<final-head> \
    PROMPT_FILE=<task-file> MODEL=<model>
→ QIR 优先快进集成
```

不要求 manifest、control commit、attempt 分支、独立 Markdown handoff、seal commit、receipt、
package tag 或功能分支远端发布。最终范围、测试和审查结论由提交、命令输出与 integration 历史说明。

工作包门禁只证明该包的范围、聚焦测试和最终 HEAD 可审查。公共 HTTP 的静态发现、接口注册、
OpenAPI、生成前端类型和真实运行时挂载允许由不同单写部门形成相互依赖的提交；只有这些提交全部进入
同一 integration candidate 后，QIR 才运行跨包一致性与完整门禁。任何中间提交不得单独标记为阶段
已验证或发布。

高风险包必须通过异构审查；数据库迁移、破坏性契约、数据授权启用和正式发布还需项目所有者批准。

## 7. 串行 integration 与发布

QIR 从当前里程碑基线复用唯一 `integration/<stage>-<purpose>`（既有候选不为改名另建），按以下顺序逐个
合入：

1. 契约；
2. 迁移与存储；
3. 来源/领域/provider；
4. 应用与 Web；
5. 运行时；
6. integration/E2E/recovery；
7. 发布文档与执行账本。

每次集成记录 source branch、最终 HEAD 和审查结论。候选完整 `make verify` 通过后可以推送
远端接受 CI。若项目所有者另行明确授权，也可以先把本地 `main` 以 `--ff-only` 前进到同一候选，
作为不带发布标签的本地开发基线；远端发布状态保持 pending。

只有 exact candidate 的普通 CI 成功并获得所有者正式发布批准时：

```text
确认本地 main 已 ff-only 指向 candidate（未指向时先前进）
→ annotated stage tag on same SHA
→ atomic push remote main + tag
→ read back main, tag object, peeled SHA and ancestry
```

失败候选不移动 `main`，也不创建正式 tag。已合入问题在同一候选追加精确 revert：快进包按逆序
revert 该包引入的提交；分叉合并提交用 `git revert -m 1`。迁移问题使用前向修复。

## 8. R1 过渡

WP01–WP09 已形成的 v1/v2 Git 历史保持只读，不重做、不改写，也不保留活动兼容执行器。旧脏根工作区
和旧 BLOCKED 对话不得作为当前状态依据。Legacy H/S、远端工作分支和 handoff tag 仅通过 Git 读取。

## 9. 停止条件

以下情况立即停写：

- 依赖或 contract hash 变化；
- 需要修改其他单写区；
- 明确写范围为空或 contract SHA 不一致；
- 实际 diff 越界或出现未跟踪敏感文件；
- Reviewer 目标不是最终 HEAD；
- 写槽超过上限；
- 未获项目所有者明确授权却准备移动本地 `main`；
- 远端 CI 或 ancestry 未通过却准备推送远端 `main` 或创建正式阶段标签；
- 新 Port 没有同阶段 consumer，以及 provider 或紧邻 provider 依赖。

## Git 与云端精简

- 2026-10-17 所有者持续授权本仓库必要的 GitHub 同步与可恢复的历史引用清理；满足现有检查后无需
  逐次再询问。授权不扩展到部署、正式标签、生产发布、秘密、真实交易或共享历史改写；`main` 仍须
  `--ff-only` 并满足原有发布门禁，平台权限仍须遵守。
- 默认本地开发。只有异机备份、实际协作审查或已验收候选/发布需要时同步云端；同一用途合并为一次
  推送，不推送每次修补、状态快照或本地工作分支。必要的临时审查分支只保留一条，完成后清理。
- 分支、工作目录和人工报告使用简短语义名，不添加 Git 哈希。提交 SHA、证据校验、依赖锁定和制品
  digest 继续保存在 Git 或正文元数据中；不得移除安全锁定或改变已发布迁移、UUID/API 标识。
- 集成复用现有入口，使用 `--ff` 优先快进，仅实际分叉时产生合并提交；不得为线性包额外创建 merge、
  tag、收据或状态提交。共享历史保留，不 squash/rebase/force-push。
- `delivery.json` 和集中状态文档只在能力验收、授权、阶段或实际部署状态变化时更新一次；不逐次
  追加失败日志、旧快照或相同证据。历史记录通过 Git 查询。
- 云端保留 `main`、当前唯一候选、仍有消费者的协作分支及正式发布标签。清理前核对开放 PR、活动
  工作及引用依赖；先验证完整 Git bundle 可恢复，保留正式标签和仍有效的证据引用，才移除封存的
  临时分支及 handoff/integration 标签。文件删除仍须移入 macOS 废纸篓。
- CI 保留全部硬门禁，同分支新版本取消旧运行，不为重复缓存和重复上传增加占用；Actions 日志和
  临时制品默认保留 14 天，恢复包保存在仓库外，不重新上传云端。

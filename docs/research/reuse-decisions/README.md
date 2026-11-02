# 复用决策记录

本目录保存功能、依赖、服务、模型和算法的复用审计。规则见
[`docs/engineering/reuse-first-policy.md`](../../engineering/reuse-first-policy.md)。记录文件名使用
`YYYY-MM-DD-<capability>.md`，后续复审在原记录追加带日期的审计段，不静默改写旧依据。

每份记录还必须登记到 `governance/reuse-decisions.json`。治理门禁会核对记录 marker、可信候选数、
唯一最终决定、默认路线、自研缺口和 `THIRD_PARTY.md` 同步词；人工审查继续负责判断许可证、数据合同、
安全证据和基准是否真实充分。

## 模板

```markdown
# <能力名称> 复用决策

<!-- reuse-decision-id:<registry-id> -->

- 日期：YYYY-MM-DD
- 负责人：
- 状态：proposed / accepted / superseded
- 最终决定：adopt / adapt / reference / reject / build（只能选一个）
- 关联阶段 / 轻量工作包 / contract base：

## 问题与验收标准

写明输入、输出、规模、延迟、质量、安全、数据权利、运维和退出要求。

## 检索范围

列出官方代码托管、包注册表、模型仓库、官方文档及检索日期。若少于两个可信候选，说明原因。

## 硬门槛

| 候选 | 精确版本/commit | 许可证 | 维护/安全 | 数据/模型权利 | 结果 |
|---|---|---|---|---|---|

## 评分

| 候选 | 能力 40 | 成熟度 20 | 许可 15 | 权利/安全 15 | 集成 10 | 总分 |
|---|---:|---:|---:|---:|---:|---:|

## 决定与边界

给出唯一默认路线、Port/adapter 边界、不会复用的部分，以及被拒候选的具体原因。

## 验证

列出原型、contract test、基准、恢复演练、许可证/漏洞扫描和人工检查证据。

## 供应链与归属

记录制品 hash/digest、NOTICE、SBOM、复制文件及修改、模型/数据许可和 `THIRD_PARTY.md` 更新。

## 退出与复审触发条件

记录回滚/数据迁移方法，以及版本、许可证、维护、CVE、SLO 或需求变化时的复审条件。
```

JSON 登记的 `candidates` 必须逐项给出官方 URL、精确 snapshot、许可证、可信性、唯一决定和理由。
存在两个以上可信候选时不得少于两个；包含自行实现时，`contains_custom_build=true` 且
`custom_build_gaps` 必须列出通用候选无法满足的确切领域缺口。

复用调研由 PAE 产品与架构标准部、DMI 数据与市场情报部和 QIR 质量、运行与集成部协作。最终写入
责任由 `governance/delivery.json` 的轻量工作包和单写区决定。记录随短工作包进入最终 payload H，
并由脱敏执行或集成收据引用精确文件、注册表项和验证结果。普通包不创建 package tag，也不要求推送
远端；QIR 仍须串行 优先快进集成。部门名称不扩大写权限，也不能绕过五个可写槽、独立审查、
`main --ff-only` 或里程碑发布前的完整门禁。

# R1 功能闭环依赖复用决策

<!-- reuse-decision-id:r1-functional-dependencies-2026-08-02 -->

- 日期：2026-08-02
- 负责人：QIR
- 状态：accepted
- 最终决定：`adopt`
- 机器登记：`governance/reuse-decisions.json`
- 关联阶段：`R1_FUNCTIONAL`

## 问题与验收标准

R1 免费来源交叉核验需要一个不复制供应商类型的 Python SDK；公网静态演示需要可复现的桌面和
移动端浏览器验收。依赖只进入既有 data extra 和 Web 开发工具链，不新建 Port、来源实现、页面或
部署逻辑。普通测试不得登录 BaoStock、访问真实网络或下载浏览器。

## 检索范围与决定

本包采用项目已批准的两个精确依赖：

| 候选 | 精确版本 | 许可证 | 决定与边界 |
|---|---|---|---|
| BaoStock | `0.9.3` | BSD-3-Clause | `adopt`；仅作为现有来源边界后的开发期行情交叉核验 SDK，数据权利单独失败关闭 |
| AKShare | `1.18.70`（现有锁） | MIT code；上游数据权利独立 | `reference`；保留为主开发来源，不承担 BaoStock 的独立交叉核验角色 |
| Playwright | `@playwright/test@1.62.1` | Apache-2.0 | `adopt`；仅用于本地/集成浏览器验收，不进入生产浏览器运行时 |
| Cypress | 未引入 | MIT | `reject`；同阶段只需一套浏览器验收工具，避免重复 runner 和锁文件体积 |

唯一默认路线为 BaoStock `0.9.3` 加 Playwright `1.62.1`。第三方 SDK 类型不得穿过 contracts 或
Ports；浏览器二进制、trace、截图和报告不进入 Git。BaoStock 的免费 SDK 许可不等于其上游行情
数据可保留、再分发、训练或用于商业生产。

## 验证与供应链

- Python 与 npm 包均精确固定并由 `uv.lock`、`apps/web/package-lock.json` 的制品哈希约束；
- BaoStock 仅执行离线 import 验证，不调用 login 或网络；
- Playwright 仅执行 CLI 版本验证，本包不下载浏览器；
- `THIRD_PARTY.md` 同步 BSD-3-Clause 与 Apache-2.0 初审；没有复制第三方源代码、数据或模型。

版本、许可证、维护者、制品来源或重大 CVE 变化时重新审查。退出方式为移除各自的精确直接依赖并
重建锁文件；既有项目契约与来源/页面边界不受影响。

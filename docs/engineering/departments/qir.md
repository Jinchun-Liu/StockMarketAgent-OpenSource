# QIR 质量、运行与集成部

使命：把各部门最终提交拼成可运行、可验证、可恢复的里程碑。

负责 API 组合根、worker、scheduler、消息 relay、依赖锁、CI、integration/E2E、发布状态和集中蓝图。
QIR 不替功能部门编写其业务规则。

工作包只跑聚焦检查；QIR 在唯一 integration candidate 上运行完整 `make integration-check`。普通分支
本地保留即可，R 阶段通过后才同步 GitHub、以 `main --ff-only` 发布并创建一个 annotated stage tag。

R1 输出：PostgreSQL/API/worker/Web 合成 E2E、100 次重放、rights 失败关闭和恢复证据。

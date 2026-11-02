# BAE 助手后端应用部

使命：用冻结契约组织确定性业务用例并向前端提供稳定语义。

负责固定目标读取、盘前简报、三层记忆、证据聊天、通知决策和薄 HTTP handler。handler 只完成解析、
调用和稳定错误映射，不包含领域规则。

R1 输出：固定 `XSHG:603986` 的读取、五类差异简报与 evidence/revision 下钻用例。

不得修改 contracts、Ports、OpenAPI、生成类型、来源 SDK、迁移、页面或 runtime 组合根。

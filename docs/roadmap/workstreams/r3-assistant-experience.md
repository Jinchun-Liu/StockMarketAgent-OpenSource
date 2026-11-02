# R3：助手体验实施规格

状态：后续规划，尚未进入实现。R3 固定为研究会话内的证据问答、报告解释和有限历史比较；不实现
今日页、inbox、SSE、通知、持续跟踪或长期 watchlist，也不继承 R1 的 `active_target`。

## 用户结果

用户在一个仍处于七日生命周期内的 `ResearchSession` 中，对已发布报告和其 evidence bundle 提问，
查看主张解释、冲突、更正、未知项和最多有限个同证券历史会话比较。回答必须复用报告的事实/系统推断/
模型假设边界，不得扩展为新事实采集、自动跟踪、投资建议或跨会话长期个股记忆。

## 当前 R3 约束与验收方向

- 问答只能引用当前会话可读的 `evidence_id`；报告过期或提前删除后正文、问答上下文和 embedding 不可恢复。
- 比较只读取用户明确选择且仍在保留期内的同证券会话，逐会话保留各自 `as_of`，禁止以后来 revision 改写旧回答。
- 页面使用普通 HTTP 请求与显式加载/失败状态，不建立 inbox、SSE、通知、今日页或后台证券任务。
- 模型不可用时保留确定性 claim、证据和计算口径；证据不足时明确 abstain。
- 验收覆盖引用闭环、PIT、注入防护、七日清理、390px、键盘和屏幕阅读。

以下内容是重定界前的历史体验草案，不是当前实施规格。

<details>
<summary>历史今日页、收件箱与实时通知草案（不再活动）</summary>

## 设计事实源

信息架构、视觉语言、状态和无障碍要求统一引用 `docs/product/experience-design.md`。页面不得自行重复业务排序、影响规则或置信度计算。

## 前置与非目标

- R2 的研究 job/event outbox、relay 和 PIT 会话查询已经通过合成集成测试；R3 若引入通知必须发布
  独立兼容契约，不能把 report-ready 事件直接当用户通知；
- 当前事件专用 `Notification`/数据库表继续只表达 market-event revision，不扩义为 behavior inbox；
- 本阶段实现不含交易结构字段的通用今日/关注聚合读模型，R4a 才在真实调用方出现后兼容扩展；
- 不在前端计算事件关系、金融排序、分数或“总置信度”；不预建 behavior API/字段。
- R2 不存在用户长期关注标的；R3 不得在没有新产品授权和生命周期设计时恢复长期 watchlist。

## TDD 实施顺序

1. 为 loading、empty、stale、partial、rights-blocked、conflict、dependency-down 写组件测试；
2. 为来源权威度、抽取质量、暴露直接度、推断不确定性和预测概率的独立展示写契约测试；
3. 为政策事实/适用性推断分离写 API 与 UI 测试；
4. 为隔夜外部状态、目标传导路径、支持/反证、`STALE_MARKET`、`UNPRICED_EVENT` 和无暴露背景写测试；
5. 为通用 outbox → inbox projection 写 message-id 追踪、业务幂等、顺序、重复投递和崩溃恢复测试；
6. 为 SSE 的稳定事件 ID、`Last-Event-ID` 断线续传、心跳、越界游标和有界重连写集成测试；
7. 为站内消息确认/静音、跨重启持久化和从消息到证据下钻写测试；
8. 为聊天证据包、拒答、prompt injection 和 LLM 离线降级写测试；
9. 跑键盘、屏幕阅读、390px 手机宽度及通知到证据的 E2E；验证十秒内理解状态、路径与可信度。

## 通用聚合、收件箱与 SSE 边界

- R3 选择并登记一个通用“今日/关注”聚合资源及共享摘要 contract；首版不含压力字段；
- R3 以 add-only migration 建立不强绑 market-event revision 的通用 inbox projection，至少保存
  `message_id`、`idempotency_key`、`event_name`、`classification`、标题/最小引用、
  occurred/projected/read/silenced 时间；
- projection 以 `message_id` 唯一用于追踪，并以
  `(workspace_or_recipient, event_name, idempotency_key)` 唯一抑制逻辑重复；新 message ID 但相同业务
  幂等键只能形成一个 inbox item。输入是 R2 已发布的 `MessageEnvelope`；外部通知仍走现有
  `NotificationDeliveryPort`，两者职责不混合；
- SSE 的 `id` 使用可持久恢复的投影序列/消息 ID，支持 `Last-Event-ID`、心跳、权限校验和断线续传；
- `GET/PATCH /api/v1/notifications` 在 R3 出现真实调用方时绑定通用 inbox contract，而不是把现有
  event-only `Notification` 强行扩义；具体 persistence interface 必须先复用审查并与实现/测试同改，
  本规划轮不预声明第三个 R4 Port；
- R4 只发布 `system_inference` 事件，复用该 projection 与 SSE，不增加行为专用通知 Port。
- 外部环境前期没有独立 HTTP，作为未来 `Briefing 2.0` 和今日聚合读模型嵌套对象返回；
  `MarketEvent 1.0` 继续只承载事实。

## 验收

- 首页首屏先显示变化与数据状态，不以营销卡片代替信息；
- 每个事实项显示来源、发布时间、首次发现、延迟和 evidence；
- 冲突、更正、过期和无授权具有不同视觉与可读文本；
- 09:40 后能显示主动标的与相关行业对外部环境的确认、吸收、背离或无法判断；
- 通用聚合资源、inbox projection、outbox relay 与 SSE 在真实数据库/API/Web 链路通过；
- `Last-Event-ID` 可恢复断线期间消息；同 message ID 或新 message ID/同业务幂等键都不重复投影，
  确认/静音跨重启保留；
- LLM 或单一来源故障时界面不显示伪造总结；
- 所有关键动作可用键盘完成，窄屏不隐藏证据和风险说明。

## 复用、接口、指标与标准同步

复用 R2 的 `MessageEnvelope`、outbox/relay、`MessagePublisherPort` 与现有外部通知适配器。今日聚合、
inbox HTTP/SSE 只有在 producer/consumer/contract/adapter/E2E 同时存在时才登记。真实生产者落地时再
登记 SSE connection/reconnect、projection lag/failure、inbox duplicate 和 notification suppression
等有界聚合指标。实施必须同步 schema、add-only migration、注册表、CSP/CSRF/session、安全与恢复文档。

## 降级

SSE 断开时退回带退避的显式刷新；通知权限拒绝时保留站内收件箱；LLM 离线时展示确定性证据列表；图表不可用时保留表格和文本值。

</details>

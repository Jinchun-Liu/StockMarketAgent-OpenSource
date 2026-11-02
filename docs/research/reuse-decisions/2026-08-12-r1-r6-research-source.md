# R1–R6 五类研究来源复用决策

日期：2026-08-12；2026-08-26 更新
结论：复用现有来源边界与 `httpx`，以 QuantData 承担固定日行情，以 ProMax 承担其余四类；不增加 SDK。

## 候选

1. QuantData Tushare-compatible HTTPS 服务：用户给出的调用形状证明 `daily` 可按标准 Tushare POST
   使用；本项目只接固定 `603986.SH` 日行情，不推断宣传中的其他接口已经通过业务验证。
2. ProMax Tushare-compatible HTTPS 服务：2026-08-25 实际渲染的教程清单列出 `anns_d`、
   `income`、`daily`、`news`、`npr`，以及 `stk_auction_o`、`stk_auction_c`、`stk_auction`；
   采用固定 GET 路径和 `X-API-Key` 头，不采用教程示例中的 `verify=False`。
3. CNINFO Data Service：公告和披露候选质量高，但当前不是 R1–R6 完成门槛，保留至 R7 正式核验。
4. Choice Quant API：终端账户未开通量化 API，不能作为当前运行依赖，保留至 R7。
5. AKShare/BaoStock：已有开发能力和历史验证，但不能稳定覆盖五类，继续作为受控辅助或交叉检查。
6. 晴红财经快讯标准版：`/api/s` 可提供 `cninfo-latest`、`cls-telegraph`、`jin10` 三路
   元数据快讯，且无需增加 SDK；但 2026-08-20 的飞书与 Apifox 文档都只公布
   `http://124.223.180.129:3220`，无法安全传输 API Key，故适配器已实现、来源保持禁用，等待服务商
   提供固定 HTTPS 入口。

## 采用范围

- `anns_d`、`income`、`daily` 固定 `603986.SH`；
- `news` 固定单一频道并只保留兆易创新相关标题；
- `npr` 固定科技政策并只保留标题、机构和政府链接；
- 每项独立 `source_id`、cursor、水位和降级；
- 旧 `gov_cn_public_macro`、`miit_public_macro` 因持续抓取失败改为禁用，仅保留历史兼容；
- 复用 `httpx`，不引入 Tushare SDK、MCP 或浏览器直连；
- R1–R6 标记 `DEV_LIVE / DEV_ONLY / UNVERIFIED`，R7 才要求正式授权覆盖。
- 竞价接口只登记为已发现候选；当前 R1 压力公式排除集合竞价时段，因此不把竞价行混入完整
  10 分钟窗口，也不在没有公共契约和算法版本的情况下新增分数输入。
- 晴红只保留条目 id、标题和发布时间，不保存来源 URL、`extra`、全文或原始响应；三路均为
  `DISCOVERY / CONTEXT_ONLY`，不能独立建立公司事实。
- 2026-08-26 东京真实网络矩阵中 `fina_indicator` 持续上游失败，`income` 返回固定标的完整利润表
  结构，故财务能力切换为 `income`；`news` 缩至最近 6 小时并按 1 小时分段，`npr` 允许规范空结果。
  `anns_d` 仍独立降级，绝不以其他 endpoint 冒充公告。

## 缺口与保护

- 接口权限必须通过真实业务 smoke 逐项确认；某项无权限只降级该类别。
- token 只从本机 secret 或 AWS Secrets Manager 注入，不进入 Git、日志、Web 或快照。
- QuantData token 与 ProMax key 分开注入；Refresh Task 可读取两个精确 Secret ARN，API、Bootstrap
  和浏览器均不能读取。
- 账户许可不能替代传输安全：任何携带凭据的来源都必须使用固定 HTTPS 入口；明文 HTTP 不启用。
- 不保存全文、embedding、训练数据或供应商原始响应；请求有超时、一次额外重试、大小与行数上限。
- 保留固定标的、evidence、PIT、幂等和不可变 revision；模型不得生成或修改事实与分数。

# 全球市场与跨境事件研究

版本：2026-07-22
状态：规划研究基线；不代表数据源已获授权或功能已实现

## 1. 研究问题与结论

本研究回答三个问题：

1. 海外市场在 A 股开盘前是否包含值得观察的信息；
2. 战争、制裁、出口管制、商品和全球金融条件如何与一只 A 股建立可审计关系；
3. 哪些成熟开源能力可以复用，哪些领域语义必须由项目自行维护。

结论是：海外价格与跨境事件应成为**事件与基本面轴的外部上下文**，但不能直接进入交易结构压力
指数，也不能被包装成涨跌概率。产品只深度处理一个主动关注 A 股；全球市场、行业和参照证券只用于
发现外部重定价、验证传导路径和解释开盘后的吸收或背离。

目前未发现成熟项目同时覆盖：授权失败关闭、跨时区 PIT 快照、事实事件修订、一跳公司暴露、非概率
外部状态、单股票解释、开盘反馈和本地可审计部署。因此采用路线是“成熟通用组件适配＋项目领域规则
自建”，而不是 fork 一个完整交易 Agent。

## 2. 跨市场传导的研究约束

- Lin、Engle 与 Ito 的日内研究表明，一个市场交易时段的信息会首先反映到另一市场的隔夜或开盘价格，
  但稳定的滞后可预测关系有限。因此产品观察完成时段和开盘反馈，不宣称机械领先关系。
  [NBER](https://www.nber.org/papers/w3911)
- 美国市场到 A 股行业的溢出具有短暂、行业异质和危机状态依赖特征；统一的“美股涨跌映射 A 股”会
  丢失行业和事件语境。
  [International Review of Financial Analysis](https://www.sciencedirect.com/science/article/pii/S1057521923001606)
- BIS 对亚太市场的研究支持全球、区域和本地因子并存，且危机时期相关结构可能改变。
  [BIS Working Paper 579](https://www.bis.org/publ/work579.htm)

工程含义：

- 每个序列只与自身相同会话、相同截止窗口的历史比较；
- 美国宽基只是一个观察块，不能代表全部外部环境；
- 海外异常只能作为上下文，必须与当前公司的实际暴露、A 股行业反馈和数据完整性共同解释；
- 周末事件在主要市场尚未交易时是“未定价事件”，不能拿旧收盘价伪装成市场反应。

## 3. 地缘事件与生产网络

生产网络理论说明，冲击可沿投入产出关系传播，但结果取决于企业所处网络位置、投入可替代性和价格
传导能力。[Acemoglu 等](https://onlinelibrary.wiley.com/doi/10.3982/ECTA9623)
IMF 的地缘风险研究也表明，贸易限制、供应链、资本流动、金融条件和政策反应会产生异质影响。
[IMF](https://www.elibrary.imf.org/abstract/book/9798229003261/CH002.xml)

因此项目不把事件关键词直接贴到证券代码，而使用以下链条：

```text
事实事件
→ 国家、机构、商品、技术或物流节点
→ 传导渠道
→ 公司披露或有证据的一跳经营暴露
→ 对主动关注标的的条件化系统推断
→ 海外与 A 股市场反馈
```

前期只允许四种直接度：官方直接点名、公司披露确认、一跳客户/供应商证据、行业背景代理。两跳以上
供应链推断、新闻共同出现和无来源产业链图谱均不进入产品事实。

## 4. 典型冲击为何不能固定方向

### 4.1 原油与俄乌冲突

相同油价上涨可能来自供应中断、全球需求增长或预防性需求，经济含义不同。
[Kilian](https://pubs.aeaweb.org/doi/abs/10.1257/aer.99.3.1053)
产品必须先区分冲击来源，再区分公司角色：油气生产者、能源净采购者、航空、化工、化肥、镍或钯
下游不能共享一个方向标签。制裁、支付、保险和航线也应与物理供给分别建模。

### 4.2 半导体出口管制与产业政策

日美荷相关措施必须解析具体设备、软件、技术、许可、地区和生效范围。同一事件可能同时产生设备取得
受限、国产替代、下游扩产受阻、海外补贴、许可获批和产能迁移等路径。OECD 的半导体价值链研究说明
该产业高度专业化且跨境依赖明显，不能用“半导体利好/利空”概括。
[OECD 主题页](https://www.oecd.org/en/topics/semiconductors.html)、
[价值链报告](https://www.oecd.org/en/publications/mapping-the-semiconductor-value-chain_4154cdbf-en.html)

### 4.3 关键材料与贵金属

镓、锗、石墨等关键材料属于产业投入与出口管制语境；黄金、白银、铂、钯则还受真实利率、美元、央行
需求、工业用途和供应国暴露影响。两类资产不得因“稀缺”被混为一类，也不能建立“战争→黄金→相关
股票上涨”的固定链条。

## 5. 官方事实来源与发现来源

| 类型 | 候选来源 | 允许用途 | 禁止用途 |
|---|---|---|---|
| 制裁事实 | [OFAC](https://ofac.treasury.gov/sanctions-list-service)、[联合国安理会](https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list) | 确认名单、发布时间和修订 | 未经权利核验缓存受限全文；直接推断股票影响 |
| 出口管制 | [美国 BIS EAR](https://www.bis.gov/regulations/ear)、[日本 METI](https://www.meti.go.jp/policy/anpo/securityexportcontrol1.html) | 确认规则、对象、许可和生效时间 | 仅凭新闻标题建立技术或供应关系 |
| 央行政策 | [Federal Reserve](https://www.federalreserve.gov/feeds/feeds.htm)、[ECB](https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html)、[BOJ](https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm) | 确认决定和原始公告 | 把利率变化自动投票为公司利好/利空 |
| 地缘事件发现 | GDELT | 离线候选召回、语言和地区背景 | 作为公司事实或唯一证据 |
| 聚合制裁发现 | OpenSanctions | 数据模型参考、候选发现 | 未取得数据许可时进入生产事实库 |
| 冲突事件数据 | ACLED | 研究候选 | 默认生产依赖；其许可和产品匹配尚不满足闸门 |

官方机构用于确认事实；公司公告、年报和正式问询回复用于确认企业暴露；授权财经源只用于发现候选和
补充语境。任何来源仍需按 [`source-policy.md`](../data/source-policy.md) 逐操作通过权利检查。

## 6. 开源与数据组件调研

| 能力 | 候选 | 结论 | 边界 |
|---|---|---|---|
| 交易会话和节假日 | [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) | 未来适配候选 | Apache-2.0；用于生成/校验会话，交易所官方日历仍是事实权威 |
| SDMX 客户端 | [sdmx1](https://sdmx1.readthedocs.io/) | 未来参考/适配候选 | Apache-2.0；仅在数据方条款允许时访问 BIS/ECB/OECD 等端点 |
| 双时态关系 | PostgreSQL | 复用 | 一跳传导足够，不引入 Neo4j |
| 全球金融终端框架 | OpenBB | 拒绝默认 | AGPL-3.0，重复 provider 边界且不能解决数据授权 |
| 海外行情快捷源 | yfinance | 拒绝生产 | 项目和上游数据条款均不构成商业/派生使用授权 |
| 美国宏观数据 | FRED API | 拒绝默认 | 当前条款对软件/AI 开发及缓存存在限制；除非单独获准不得接入 |

本轮没有安装这些依赖、复制代码或启用外部连接器。详细采用记录见
[`2026-07-22-external-context.md`](reuse-decisions/2026-07-22-external-context.md)。

## 7. 与竞品和现有产品的差异

主流行情软件通常能显示全球指数、期货、汇率、资讯和产业链标签，但用户仍需自行完成跨时区比较、
事件核验、公司暴露判断与开盘反馈。StockMarketAgent 不追求覆盖更多终端页面，而把这四步压缩成：

1. 隔夜发生了哪些异常重定价；
2. 哪些事件有可审计路径连接到主动关注标的；
3. 哪些支持项与反证项同时存在；
4. A 股开盘后表现为同向确认、吸收、背离还是无法判断。

这一定位与交易结构压力的“低认知负担＋证据下钻”一致，但外部状态不形成第二个总分。

## 8. 研究到规划的唯一落点

- 正式窗口、状态和传导规则只在 [`overnight-external-context.md`](../data/overnight-external-context.md) 维护；
- 工程边界只在 [`external-context-framework.md`](../architecture/external-context-framework.md) 维护；
- 阶段、失败测试和退出条件只在 [`r2-external-context.md`](../roadmap/workstreams/r2-external-context.md) 维护；
- 当前接口、任务、指标和数据源注册表不因本研究自动获得批准。

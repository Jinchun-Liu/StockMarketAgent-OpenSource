import assert from "node:assert/strict";

import {
  attentionBandLabel,
  availabilityLabel,
  normalizeResearchScore,
  plainFactText,
  plainInterpretationText,
  plainLabel,
  pressureBandLabel,
  safeGapReason,
} from "../app/_components/presentation.ts";

assert.equal(normalizeResearchScore(0), 0);
assert.equal(normalizeResearchScore(100), 100);
assert.equal(normalizeResearchScore(null), null);
assert.equal(normalizeResearchScore(-3), 0);
assert.equal(normalizeResearchScore(104), 100);
assert.equal(attentionBandLabel("high_alert"), "高警觉");
assert.equal(pressureBandLabel("fragile"), "脆弱");
assert.equal(availabilityLabel("abstained"), "资料不足，暂不评分");
assert.equal(
  plainInterpretationText("关注风险整体为 abstained，mode=post_close，来源是 DEV_LIVE。"),
  "关注风险整体为 资料不足，暂不评分，mode=盘后确认，来源是 开发阶段更新。",
);
assert.equal(plainLabel({}, "future_value"), "暂未归类");
assert.equal(safeGapReason("RESEARCH_DATA_PERMISSION_BLOCKED"), "当前套餐暂未开放这类资料");
assert.equal(safeGapReason("RESEARCH_DATA_TIMEOUT"), "研究数据服务本次响应超时");
assert.equal(safeGapReason("RAW_VENDOR_DETAIL"), "原因正在整理，页面不会据此补写结果");
assert.equal(
  plainFactText(
    'market.snapshot={"amount":"18748833.166","change":"10.88","close":"413.17","high":"419.95","low":"401.68","pct_chg":"2.7045","vol":"456116.49"}',
    "market.snapshot",
  ),
  "收盘价 413.17，涨跌幅 +2.7045%，价格变化 +10.88，最高 419.95，最低 401.68，成交量 456,116.49",
);
assert.equal(
  plainFactText('{"internal":"raw"}'),
  "这条结构化信息已经完成整理，详细字段暂不直接展示。",
);
assert.equal(
  plainFactText(
    '{"close":413.17,"change":0,"high":420,"low":400,"pct_chg":-2.5,"vol":0}',
    "market.snapshot",
  ),
  "收盘价 413.17，涨跌幅 -2.5%，价格变化 0，最高 420，最低 400，成交量 0",
);
assert.equal(
  plainFactText('{"unrecognized":"value"}', "market.snapshot"),
  "这条结构化信息已经完成整理，详细字段暂不直接展示。",
);
assert.equal(
  plainFactText('{"close":{"raw":"secret"}}', "market.snapshot"),
  "这条结构化信息已经完成整理，详细字段暂不直接展示。",
);
for (const structuredValue of [
  '"raw-json-string"',
  "true",
  "123",
  "null",
  'financial.change={"secret":"raw"}',
]) {
  assert.equal(
    plainFactText(structuredValue),
    "这条结构化信息已经完成整理，详细字段暂不直接展示。",
  );
}
assert.equal(plainFactText("公司已发布季度报告"), "公司已发布季度报告");

console.log("research presentation smoke passed");

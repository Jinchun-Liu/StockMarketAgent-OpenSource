"use client";

import { useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AvailableResult, Claim, Report } from "@/lib/api/research-v2";

import { EvidenceLens, type EvidenceSourceSelection } from "./evidence-lens";
import { claimLabels, formatDate, formatTime } from "./presentation";

type ReportTab = "reader" | "professional" | "methods";

function downloadMarkdown(filename: string, markdown: string): void {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function EvidenceButton({ claim, onOpen }: { claim: Claim; onOpen: (claim: Claim) => void }) {
  return (
    <button className="claim-button" type="button" onClick={() => onOpen(claim)}>
      <span className="claim-head">
        <span className={`claim-kind is-${claim.claim_type}`}>{claimLabels[claim.claim_type]}</span>
        <span className="lens-prompt">查看来源与反证 ↗</span>
      </span>
      <span className="claim-statement">{claim.statement}</span>
      {claim.uncertainty && <span className="claim-uncertainty">不确定性：{claim.uncertainty}</span>}
    </button>
  );
}

function findEvidenceSource(
  claims: readonly Claim[], evidenceId: string, title: string, counter = false,
): EvidenceSourceSelection | null {
  const citation = claims.flatMap((claim) => [...(claim.evidence ?? []), ...(claim.counter_evidence ?? [])])
    .find((item) => item.evidence_id === evidenceId);
  if (!citation) return null;
  return { citation, title, counter, facts: claims.filter((claim) => claim.claim_type === "fact" &&
    [...(claim.evidence ?? []), ...(claim.counter_evidence ?? [])].some((item) => item.evidence_id === evidenceId)) };
}

function ResearchMarkdown({ markdown, claims, onOpen, onOpenSource, compact = false }: {
  markdown: string;
  claims: readonly Claim[];
  onOpen: (claim: Claim) => void;
  compact?: boolean;
  onOpenSource: (source: EvidenceSourceSelection) => void;
}) {
  const components = useMemo<Components>(() => ({
      img: () => null,
      h1: ({ children }) => <p className="reader-document-title">{children}</p>,
      h2: ({ children, node }) => <h2 id={compact ? undefined : `reader-heading-${node?.position?.start.line}`}>{children}</h2>,
      table: ({ children }) => <div className="table-scroll" tabIndex={0} role="region" aria-label="报告数据表格"><table>{children}</table></div>,
      a: ({ children, href }) => {
        const claim = href?.startsWith("#claim-") ? claims.find((item) => `#claim-${item.claim_id}` === href) : undefined;
        const source = href?.startsWith("#source-")
          ? findEvidenceSource(claims, href.slice("#source-".length), "引用来源") : null;
        if (source) return <button className="inline-evidence" type="button" onClick={() => onOpenSource(source)}>{children}</button>;
        return claim ? <button className="inline-evidence" type="button" onClick={() => onOpen(claim)}>{children}</button> : <span className="blocked-markdown-link">{children}</span>;
      },
    }), [claims, compact, onOpen, onOpenSource]);
  return <div className={compact ? "research-prose" : "markdown-reader"}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components}>{markdown}</ReactMarkdown>
  </div>;
}

export function AvailableResultView({ result, instrumentName, instrumentId, qualityLabel }: {
  result: AvailableResult;
  instrumentName: string;
  instrumentId: string;
  qualityLabel: string;
}) {
  if (result.reader_edition) {
    return <div className="available-result has-reader">
      <ResearchDocumentView result={result} instrumentName={instrumentName} instrumentId={instrumentId} qualityLabel={qualityLabel} />
    </div>;
  }
  return <RetainedEvidenceView result={result} />;
}

function RetainedEvidenceView({ result }: { result: AvailableResult }) {
  const [selectedClaim, setSelectedClaim] = useState<Claim | null>(null);
  const [selectedSource, setSelectedSource] = useState<EvidenceSourceSelection | null>(null);
  const claims = result.claims ?? [];
  return <article className="available-result reader-paper" aria-labelledby="available-result-title">
    <header className="plain-takeaway">
      <p>未完成 · 已保留的研究内容</p>
      <h2 id="available-result-title">可用结果</h2>
      <p>{result.source_mode === "synthetic_development_only" ? "合成测试数据 · " : ""}{result.non_production_notice}</p>
      <p>截至 {formatTime(result.as_of)}。以下内容已验证并保存，尚未形成完整 Bull / Bear 与综合报告。</p>
    </header>
    {result.scorecard && <ScorecardView scorecard={result.scorecard} isSynthetic={result.source_mode === "synthetic_development_only"} onOpenEvidence={(id, title, counter) => setSelectedSource(findEvidenceSource(claims, id, title, counter))} />}
    {(result.sections ?? []).map((section) => <section className="available-section" key={section.kind}>
      <h3>{section.title}</h3>
      <ResearchMarkdown markdown={section.summary} claims={claims} onOpen={setSelectedClaim} onOpenSource={setSelectedSource} compact />
    </section>)}
    <section className="reader-evidence" aria-label="已保留的证据记录">
      {claims.map((claim) => <EvidenceButton claim={claim} onOpen={setSelectedClaim} key={claim.claim_id} />)}
      {claims.length === 0 && <p>暂未保留可发布的论点。</p>}
    </section>
    <section className="reader-coverage-gaps" aria-label="尚缺哪些证据">
      <h3>尚缺哪些证据</h3>
      {(result.coverage_gaps ?? []).map((gap, index) => <p key={index}>{gap.detail}</p>)}
    </section>
    <EvidenceLens claim={selectedClaim} source={selectedSource} onClose={() => { setSelectedClaim(null); setSelectedSource(null); }} />
  </article>;
}

export function ReportView({ report }: { report: Report }) {
  return <ResearchDocumentView result={report} instrumentName={report.instrument_name} instrumentId={report.instrument_id} qualityLabel="完整" completeReport={report} />;
}

function ScorecardView({ scorecard, isSynthetic, onOpenEvidence }: {
  scorecard: Report["scorecard"];
  isSynthetic: boolean;
  onOpenEvidence: (evidenceId: string, title: string, counter: boolean) => void;
}) {
  const isSimulated = scorecard.scoring_basis === "synthetic_scenario";
  const canShowNumeric = !isSynthetic || isSimulated;
  const showTotal = canShowNumeric && scorecard.status === "scored" && scorecard.overall_score != null;
  return <div className="scorecard-view">
    <section className={`score-overview${showTotal ? "" : " is-unscored"}${isSynthetic ? " is-synthetic" : ""}`} aria-label={isSimulated ? "模拟评分" : "综合质量评分"}>
      <div className="score-primary">
        <p>{isSimulated ? "模拟总分" : "综合质量分"}</p>
        <strong>{showTotal ? scorecard.overall_score : "暂不评分"}</strong>
        {showTotal && <span>/ 100 · {scorecard.score_band}</span>}
        <small>{scorecard.score_meaning}</small>
      </div>
      <div className="score-explanation">
        <p>{isSimulated
          ? "这是合成场景内的评分，不评价真实公司，也不代表推荐概率。风险维度分数越高，表示场景中的韧性越强。"
          : isSynthetic
            ? "这份历史合成报告没有保存模拟评分，继续保留原有结果。"
            : "模型按证据规则分档，程序复算结果。50 分表示优劣大致平衡，分数不代表是否推荐。"}</p>
        {!showTotal && canShowNumeric && <p>总分暂不可用。已完成的维度保留分数，缺失维度显示未评分，不补值或重分权重。</p>}
        {isSynthetic && <p className="synthetic-verification">真实来源核验：不适用（合成）</p>}
      </div>
      {!isSynthetic && <div className="confidence-score">
        <p>证据可信度</p><strong>{scorecard.evidence_confidence}</strong><span>/ 100</span>
        <small>{scorecard.evidence_confidence_meaning}</small>
      </div>}
    </section>
    <section className="dimension-grid" aria-label={canShowNumeric ? "六项评分" : "六项说明"}>
      {scorecard.dimensions.map((dimension) => {
        const scored = canShowNumeric && dimension.adjusted_score != null;
        const content = <>
          <span><strong>{dimension.display_name}</strong><small>权重 {Math.round(Number(dimension.weight) * 100)}%</small></span>
          {scored && <span className="dimension-track" aria-hidden="true"><i style={{ width: `${dimension.adjusted_score}%` }} /></span>}
          {canShowNumeric && <b>{scored ? dimension.adjusted_score : "未评分"}</b>}
          <span className="dimension-reasons">{(dimension.reasons ?? []).join("；") || dimension.unscored_reason}</span>
        </>;
        return <div className={`dimension-row${scored ? "" : " is-unscored"}`} key={dimension.dimension}>
          {content}
          <div className="dimension-evidence">
            {(dimension.evidence_ids ?? []).map((id, index) => <button className="button-quiet" type="button" key={id} onClick={() => onOpenEvidence(id, `${dimension.display_name} · 支持来源`, false)}>支持依据 {index + 1}</button>)}
            {(dimension.counter_evidence_ids ?? []).map((id, index) => <button className="button-quiet" type="button" key={id} onClick={() => onOpenEvidence(id, `${dimension.display_name} · 反证来源`, true)}>反证依据 {index + 1}</button>)}
          </div>
        </div>;
      })}
    </section>
  </div>;
}

function ResearchDocumentView({ result: report, instrumentName, instrumentId, qualityLabel, completeReport }: {
  result: AvailableResult | Report;
  instrumentName: string;
  instrumentId: string;
  qualityLabel: string;
  completeReport?: Report;
}) {
  const isReadableReport = Boolean(report.reader_edition);
  const [selectedClaim, setSelectedClaim] = useState<Claim | null>(null);
  const [selectedSource, setSelectedSource] = useState<EvidenceSourceSelection | null>(null);
  const [tocOpen, setTocOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<ReportTab>(
    isReadableReport ? "reader" : "professional",
  );
  const reportClaims = useMemo(() => report.claims ?? [], [report.claims]);
  const scorecard = report.scorecard;
  const reader = report.reader_edition;
  const isSynthetic = report.source_mode === "synthetic_development_only";

  const openEvidence = (evidenceId: string, title: string, counter: boolean): void => {
    setSelectedSource(findEvidenceSource(reportClaims, evidenceId, title, counter));
  };

  return (
    <div className="reader-report page-enter">
      <header className="reader-masthead">
        <div>
          <p className="eyebrow">{isSynthetic ? "研究报告 · 合成模拟" : "公司研究 · PAPER ONLY"}</p>
          <h1 id="report-title">{instrumentName}</h1>
          <p>{instrumentId} · 分析截点 {formatTime(report.as_of)}</p>
        </div>
        <div className="report-actions" aria-label="报告操作">
          {reader && (
            <button
              className="button-secondary"
              type="button"
              onClick={() => downloadMarkdown(`${instrumentId}-research.md`, reader.safe_markdown)}
            >
              下载 .md
            </button>
          )}
          <button className="button-secondary" type="button" onClick={() => globalThis.print()}>
            打印
          </button>
        </div>
      </header>

      <div className="report-notice"><strong>研究质量：{qualityLabel}</strong><span>{isSynthetic ? "合成测试数据 · " : ""}{report.non_production_notice}</span></div>


      {completeReport?.web_retrieval_disclosure &&
        completeReport?.web_retrieval_disclosure.model_assisted_item_count > 0 && (
          <aside className="web-risk-notice" aria-label="模型辅助联网提示">
            <strong>联网信息提示</strong>
            <span>{completeReport?.web_retrieval_disclosure.warning}</span>
          </aside>
        )}

      <nav className="report-tabs" aria-label="报告阅读方式">
        {isReadableReport && (
          <button
            type="button"
            aria-pressed={activeTab === "reader"}
            onClick={() => setActiveTab("reader")}
          >
            易读版
          </button>
        )}
        <button
          type="button"
          aria-pressed={activeTab === "professional"}
          onClick={() => setActiveTab("professional")}
        >
          专业十区块
        </button>
        <button
          type="button"
          aria-pressed={activeTab === "methods"}
          onClick={() => setActiveTab("methods")}
        >
          方法与来源
        </button>
      </nav>

      {reader && (
        <div className="reader-layout" hidden={activeTab !== "reader"}>
          <article className="reader-paper" aria-labelledby="report-title">
            {!reader.safe_markdown.includes(reader.one_sentence_takeaway) && <section className="plain-takeaway">
              <p>研究质量：{qualityLabel}</p>
              <h2 id="reader-title">{reader.one_sentence_takeaway}</h2>
            </section>}
            <ResearchMarkdown markdown={reader.safe_markdown} claims={reportClaims} onOpen={setSelectedClaim} onOpenSource={setSelectedSource} />

            <section
              className="reader-coverage-gaps"
              aria-labelledby="reader-coverage-gaps-title"
            >
              <h2 id="reader-coverage-gaps-title">证据缺口与补充状态</h2>
              {(report.coverage_gaps ?? []).length === 0 ? (
                <p className="reader-gap-empty">当前未记录额外证据缺口</p>
              ) : (
                <ol className="reader-gap-list">
                  {(report.coverage_gaps ?? []).map((gap, index) => (
                    <li
                      className="reader-gap-item"
                      key={`${gap.area}-${gap.reason_code}-${gap.detail}-${index}`}
                    >
                      <p>{gap.detail}</p>
                      <small>{gap.area} · {gap.reason_code}</small>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section className="reader-evidence" aria-labelledby="reader-evidence-title">
              <h2 id="reader-evidence-title">可核对的证据记录</h2>
              <p>下面按报告顺序列出全部结论。打开任意一项，可核对来源、时间、反证与计算口径。</p>
              {reportClaims.map((claim) => (
                <EvidenceButton claim={claim} onOpen={setSelectedClaim} key={claim.claim_id} />
              ))}
            </section>

            {scorecard && <p className="recommendation-boundary">{scorecard.recommendation_notice}</p>}
          </article>
          <nav className="reader-guide" aria-label="易读报告目录">
            <strong>本文目录</strong>
            <ol>{reader.safe_markdown.split("\n").flatMap((line, index) =>
              line.startsWith("## ") ? <li key={index}><a href={`#reader-heading-${index + 1}`}>{line.slice(3)}</a></li> : [],
            )}</ol>
            <a href="#reader-coverage-gaps-title">证据缺口与补充状态</a>
            <a href="#reader-evidence-title">逐条核对证据</a>
            <p>同一份报告，切换阅读方式不会重新生成内容。</p>
          </nav>
        </div>
      )}

      {activeTab === "professional" && (
        <div className="report-shell professional-report">
          <nav className="report-toc" aria-label="报告十区块目录">
            <p>报告目录 · {(report.sections ?? []).length}</p>
            <button
              className="toc-toggle"
              type="button"
              aria-expanded={tocOpen}
              aria-controls="report-toc-list"
              onClick={() => setTocOpen((open) => !open)}
            >
              <span>报告目录</span><span>{tocOpen ? "收起" : "展开十区块"}</span>
            </button>
            <ol id="report-toc-list" className={tocOpen ? "is-open" : undefined}>
              {(report.sections ?? []).map((section, index) => (
                <li key={section.kind}>
                  <a href={`#section-${section.kind}`} onClick={() => setTocOpen(false)}>
                    <span>{String(index + 1).padStart(2, "0")}</span>{section.title}
                  </a>
                </li>
              ))}
            </ol>
          </nav>
          <article className="report-paper">
            {scorecard && <ScorecardView scorecard={scorecard} isSynthetic={isSynthetic} onOpenEvidence={openEvidence} />}
            {completeReport && <dl className="report-meta">
              <div><dt>历史覆盖</dt><dd>{formatDate(completeReport.coverage_start)} — {formatDate(completeReport.coverage_end)}</dd></div>
              <div><dt>自动删除</dt><dd>{formatTime(completeReport.expires_at)}</dd></div>
              <div><dt>模型与生成器</dt><dd>{completeReport.model_provider} / {completeReport.model_name} · {completeReport.generator_version}</dd></div>
              <div><dt>规则与校验</dt><dd>{completeReport.rule_version} · {completeReport.validator_version}</dd></div>
            </dl>}
            {(report.coverage_gaps ?? []).length > 0 && (
              <section className="coverage-gaps" aria-labelledby="gap-title">
                <h2 id="gap-title">覆盖缺口与未知项</h2>
                {(report.coverage_gaps ?? []).map((gap) => (
                  <p key={`${gap.area}-${gap.reason_code}`}><strong>{gap.reason_code}</strong> · {gap.detail}</p>
                ))}
              </section>
            )}
            {(report.sections ?? []).map((section, index) => {
              const sectionClaims = reportClaims.filter(
                (claim) => claim.section === section.kind,
              );
              return (
                <section className="report-section" id={`section-${section.kind}`} key={section.kind}>
                  <span className="section-number">{String(index + 1).padStart(2, "0")}</span>
                  <div className="section-body">
                    {section.kind === "major_events_and_macro" && (
                      <span className="international-marker">INTERNATIONAL EVENT PATHS</span>
                    )}
                    <h2>{section.title}</h2>
                    <ResearchMarkdown markdown={section.summary} claims={reportClaims} onOpen={setSelectedClaim} onOpenSource={setSelectedSource} compact />
                    {sectionClaims.length === 0 ? (
                      <p className="professional-empty-state">
                        本区块暂无可核对的独立证据记录；当前摘要仅说明范围或方法。
                      </p>
                    ) : (
                      sectionClaims.map((claim) => (
                        <EvidenceButton
                          claim={claim}
                          onOpen={setSelectedClaim}
                          key={claim.claim_id}
                        />
                      ))
                    )}
                  </div>
                </section>
              );
            })}
          </article>
        </div>
      )}

      {activeTab === "methods" && (
        <div className="methods-report">
          {scorecard && <ScorecardView scorecard={scorecard} isSynthetic={isSynthetic} onOpenEvidence={openEvidence} />}

          {reader && <section className="score-rationale" aria-labelledby="score-rationale-title">
            <h2 id="score-rationale-title">结论与评分说明</h2>
            <p>解读来源：系统根据已验证报告整理</p>
            <ul>{reader.why_this_score.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            <h3>公开分析步骤</h3>
            <ol>{reader.public_analysis_steps.map((step) => <li key={step}>{step}</li>)}</ol>
          </section>}

          {completeReport && <section>
            <p className="eyebrow">METHOD RECEIPTS</p>
            <h2>本次用了哪些研究方法</h2>
            <p>每项方法都记录采用、证据不足、不适用或因产品边界被阻止。跳过不会被伪装成已完成。</p>
            <div className="method-receipts">
              {(completeReport.skill_executions ?? []).map((execution) => (
                <article key={execution.skill_id}>
                  <span>{execution.status}</span>
                  <h3>{execution.skill_id}</h3>
                  <p>{execution.detail ?? "本次未提供补充说明。"}</p>
                  <small>{execution.template_version} · 关联证据 {(execution.evidence_ids ?? []).length} 条</small>
                </article>
              ))}
            </div>
          </section>}
          <section className="source-summary">
            <h2>来源与版本</h2>
            <dl className="report-meta">
              {completeReport && <div><dt>数据 manifest</dt><dd className="mono">{completeReport.data_manifest_sha256}</dd></div>}
              <div><dt>来源模式</dt><dd>{report.source_mode}</dd></div>
              {completeReport && <>
                <div><dt>联网补全</dt><dd>{completeReport.web_retrieval_disclosure.model_assisted_item_count} 条</dd></div>
                <div><dt>未独立交叉验证</dt><dd>{completeReport.web_retrieval_disclosure.not_independently_verified_count} 条</dd></div>
              </>}
            </dl>
            <p>在易读版或专业报告中点击依据，可查看来源、版本、截点时间、反证和计算口径。</p>
          </section>
        </div>
      )}

      <EvidenceLens claim={selectedClaim} source={selectedSource} onClose={() => { setSelectedClaim(null); setSelectedSource(null); }} />
    </div>
  );
}

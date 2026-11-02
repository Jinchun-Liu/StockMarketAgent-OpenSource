"use client";

import { useEffect, useRef } from "react";

import type { Citation, Claim } from "@/lib/api/research-v2";

import { claimLabels, formatTime } from "./presentation";

const acquisitionLabels: Record<Citation["acquisition_mode"], string> = {
  built_in_api: "内置数据源",
  model_assisted_web: "模型辅助联网",
};

const sourceLevelLabels: Record<Citation["source_level"], string> = {
  regulatory_official: "监管、交易所或政府原始记录",
  company_official: "公司公告或投资者关系单一来源",
  trusted_media_data: "已登记媒体或数据来源",
  synthetic: "合成验证来源",
};

const verificationLabels: Record<Citation["verification_status"], string> = {
  independently_verified: "已独立交叉验证",
  single_source: "尚未独立交叉验证",
  unverified: "未验证，不应参与评分",
};

function CitationBlock({ citation, counter = false }: { citation: Citation; counter?: boolean }) {
  const modelAssisted = citation.acquisition_mode === "model_assisted_web";
  return (
    <div className={`citation${counter ? " is-counter" : ""}`}>
      <strong>{citation.source_label}</strong>
      <span>获取方式：{acquisitionLabels[citation.acquisition_mode]}</span>
      <span>来源等级：{sourceLevelLabels[citation.source_level]}</span>
      <span>核验状态：{verificationLabels[citation.verification_status]}</span>
      <span>评分可靠性系数：{citation.reliability_factor}</span>
      {modelAssisted && (
        <div className="citation-risk" role="note">
          <strong>这条信息由模型辅助联网取得</strong>
          <span>模型只负责选取已抓取正文中的原句；信息仍可能存在页面版本或单一来源误差。</span>
        </div>
      )}
      <span>{citation.source_role} · revision 可见于 {formatTime(citation.revision_known_at)}</span>
      <span>首次获取 {formatTime(citation.first_seen_at)} · 发布 {formatTime(citation.published_at)}</span>
      <code>{citation.evidence_id}</code>
      <small>{citation.locator}</small>
      {citation.formal_document && <section className="lens-section">
        <h3>正式披露原文</h3>
        <p>{citation.formal_document.verification.identity.title}</p>
        <a href={citation.formal_document.verification.identity.canonical_url} target="_blank" rel="noopener noreferrer">打开正式文件</a>
        <p>发行人：{citation.formal_document.verification.identity.issuer_name}</p>
        <p>文件标识：{citation.formal_document.verification.identity.native_document_id}</p>
        <p>提取范围：{citation.formal_document.extraction_scope === "complete" ? "已完整提取" : "部分提取或范围未确认"}</p>
        {Object.entries(citation.formal_document.fact_locations).map(([key, location]) => <small key={key}>
          {key}：{location.page_number ? `第 ${location.page_number} 页，` : ""}原文字符 {location.start}–{location.end}
        </small>)}
        {citation.formal_document.verification.publication_evidence.map((proof, index) => <p key={index}>
          披露日期依据：{proof.kind === "official_index" ? "官方公告索引" : proof.kind === "document_disclosure" ? "文件明确披露字段" : "官方结构化日期"} · {proof.raw_value} · {proof.location.field}
        </p>)}
        <small>文档标识：{citation.formal_document.document_id}</small>
        <small>获取记录：{citation.formal_document.retrieval_receipt_id}</small>
        <small>正文哈希对应规范化原文，不代表原 PDF 文件字节。</small>
      </section>}
      <small>独立来源组：{citation.independent_source_group}</small>
      {citation.content_sha256 && <small>正文哈希：{citation.content_sha256}</small>}
      {citation.rights_snapshot_sha256 && <small>权限快照哈希：{citation.rights_snapshot_sha256}</small>}
    </div>
  );
}

export type EvidenceSourceSelection = {
  citation: Citation;
  title: string;
  counter: boolean;
  facts: readonly Claim[];
};

export function EvidenceLens({ claim, source = null, onClose }: {
  claim: Claim | null;
  source?: EvidenceSourceSelection | null;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const lensRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!claim && !source) return;
    const prior = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const scrollY = window.scrollY;
    const priorOverflow = document.body.style.overflow;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code === "Escape") {
        onClose();
        return;
      }
      if (event.code !== "Tab" || !lensRef.current) return;
      const focusable = Array.from(
        lensRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.style.overflow = "hidden";
    closeRef.current?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = priorOverflow;
      prior?.focus({ preventScroll: true });
      window.scrollTo({ top: scrollY, behavior: "instant" });
    };
  }, [claim, source, onClose]);

  if (!claim && !source) return null;
  const support = claim?.evidence ?? [];
  const counter = claim?.counter_evidence ?? [];
  const falsification = claim?.falsification_conditions ?? [];

  return (
    <>
      <button className="evidence-lens-backdrop" type="button" aria-label="关闭证据透镜" onClick={onClose} />
      <aside ref={lensRef} className="evidence-lens" role="dialog" aria-modal="true" aria-labelledby="lens-title">
        <header className="lens-header">
          <strong>证据透镜</strong>
          <button ref={closeRef} className="lens-close" type="button" aria-label="关闭" onClick={onClose}>×</button>
        </header>
        <div className="lens-content">
          {source && <>
            <h2 id="lens-title">{source.title}</h2>
            <CitationBlock citation={source.citation} counter={source.counter} />
            <section className="lens-section">
              <h3>同源事实（供核对）</h3>
              <p>以下列出本报告引用该来源的事实。各项评分的理由见报告正文。</p>
              {source.facts.length === 0
                ? <p className="muted">本报告未单独保留该来源的事实摘录。</p>
                : source.facts.map((fact) => <p key={fact.claim_id}>{fact.statement}</p>)}
            </section>
          </>}
          {claim && <>
          <span className={`claim-kind is-${claim.claim_type}`}>{claimLabels[claim.claim_type]}</span>
          <h2 id="lens-title">{claim.statement}</h2>
          {claim.uncertainty && <p>不确定性：{claim.uncertainty}</p>}

          <section className="lens-section">
            <h3>支持证据 · {support.length}</h3>
            {support.length === 0 ? <p className="muted">当前主张没有可读取的支持证据。</p> : support.map((item) => <CitationBlock key={item.evidence_id} citation={item} />)}
          </section>

          <section className="lens-section">
            <h3>反证 · {counter.length}</h3>
            {counter.length === 0 ? <p className="muted">当前资料未找到直接反证；这不表示不存在风险或反对意见。</p> : counter.map((item) => <CitationBlock key={item.evidence_id} citation={item} counter />)}
          </section>

          {claim.calculation && (
            <section className="lens-section">
              <h3>计算与规则口径</h3>
              <div className="calculation">
                <code>{claim.calculation.formula}</code>
                <small>规则 {claim.calculation.rule_version} · 截点 {formatTime(claim.calculation.cutoff)}</small>
                <dl>
                  {claim.calculation.inputs.map((input) => (
                    <div key={input.name}>
                      <dt>{input.name}</dt>
                      <dd>{input.value}{input.unit ? ` ${input.unit}` : ""}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </section>
          )}

          <section className="lens-section">
            <h3>可证伪条件</h3>
            {falsification.length === 0 ? <p className="muted">事实型主张不要求模型假设式的可证伪条件。</p> : <ul>{falsification.map((item) => <li key={item}>{item}</li>)}</ul>}
          </section>
          </>}
        </div>
      </aside>
    </>
  );
}

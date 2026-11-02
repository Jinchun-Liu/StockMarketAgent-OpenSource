import Link from "next/link";

export function LegacyRoute({ label }: { label: string }) {
  return (
    <div className="content-page page-enter">
      <div className="empty-state">
        <p className="eyebrow">R1 COMPATIBILITY ROUTE</p>
        <h1>{label}已迁移</h1>
        <p>旧页面不再属于 R2 产品路径。请从证券搜索创建独立研究，并在报告中使用证据透镜下钻。</p>
        <Link className="button-primary" href="/">前往新研究</Link>
      </div>
    </div>
  );
}

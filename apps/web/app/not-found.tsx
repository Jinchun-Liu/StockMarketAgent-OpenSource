import Link from "next/link";

export default function NotFound() {
  return (
    <div className="content-page page-enter">
      <div className="empty-state">
        <p className="eyebrow">404 · NOT FOUND</p>
        <h1>这里没有可读取的研究</h1>
        <p>链接可能已过期、被提前删除，或不属于当前登录主体。</p>
        <Link className="button-primary" href="/">返回证券搜索</Link>
      </div>
    </div>
  );
}

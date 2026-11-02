import type { Metadata } from "next";

import { AuthCallback } from "../../_instrument/auth-callback";

export const metadata: Metadata = {
  title: "完成安全登录",
  robots: { index: false, follow: false },
};

export default function CallbackPage() {
  return <AuthCallback />;
}

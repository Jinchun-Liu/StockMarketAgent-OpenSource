import type { NextConfig } from "next";
import { isAbsolute } from "node:path";

import { serverConfig } from "./lib/config";

const publicStaticBuild = process.env.STOCK_ASSISTANT_PUBLIC_STATIC === "1";
const r2PreviewStaticBuild = process.env.STOCK_ASSISTANT_R2_PREVIEW_STATIC === "1";
const staticBuild = publicStaticBuild || r2PreviewStaticBuild;
const runtimeMode = process.env.NEXT_PUBLIC_RUNTIME_MODE;
const snapshotDir = process.env.STOCK_ASSISTANT_PUBLIC_SNAPSHOT_DIR;
const publicAssetRelease = process.env.STOCK_ASSISTANT_PUBLIC_ASSET_RELEASE;

if (publicStaticBuild && runtimeMode !== "PUBLIC_STATIC") {
  throw new Error("PUBLIC_STATIC_MODE_REQUIRED");
}
if (!publicStaticBuild && runtimeMode === "PUBLIC_STATIC") {
  throw new Error("PUBLIC_STATIC_BUILD_REQUIRED");
}
if (publicStaticBuild && (!snapshotDir || !isAbsolute(snapshotDir))) {
  throw new Error("PUBLIC_STATIC_SNAPSHOT_DIR_ABSOLUTE_REQUIRED");
}
if (publicStaticBuild && !/^[a-f0-9]{40}$/.test(publicAssetRelease ?? "")) {
  throw new Error("PUBLIC_STATIC_ASSET_RELEASE_REQUIRED");
}
if (!staticBuild && publicAssetRelease) {
  throw new Error("PUBLIC_STATIC_ASSET_RELEASE_FORBIDDEN");
}
if (publicStaticBuild && r2PreviewStaticBuild) {
  throw new Error("STATIC_BUILD_MODES_MUTUALLY_EXCLUSIVE");
}
if (r2PreviewStaticBuild && runtimeMode !== "R2_PRIVATE_PREVIEW") {
  throw new Error("R2_PRIVATE_PREVIEW_MODE_REQUIRED");
}
if (r2PreviewStaticBuild && !/^[a-f0-9]{40}$/.test(publicAssetRelease ?? "")) {
  throw new Error("R2_PREVIEW_ASSET_RELEASE_REQUIRED");
}

const nextConfig: NextConfig = {
  output: staticBuild ? "export" : "standalone",
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  reactStrictMode: true,
  ...(staticBuild
    ? {
        assetPrefix: r2PreviewStaticBuild
          ? `/assets/releases/${publicAssetRelease}`
          : `/_next/static/releases/${publicAssetRelease}`,
        trailingSlash: true,
        images: { unoptimized: true },
      }
    : {}),
  turbopack: {
    // Prevent a parent-directory lockfile from expanding the build root.
    root: process.cwd(),
  },
  ...(staticBuild
    ? {}
    : {
        async rewrites() {
          return [
            {
              source: "/api/v1/:path*",
              destination: `${serverConfig.apiInternalUrl}/api/v1/:path*`,
            },
            {
              source: "/api/v2/:path*",
              destination: `${serverConfig.apiInternalUrl}/api/v2/:path*`,
            },
          ];
        },
      }),
};

export default nextConfig;

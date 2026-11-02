import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";

const root = resolve("out");
const mediaTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".woff2", "font/woff2"],
]);

async function regularFile(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url ?? "/", "http://127.0.0.1").pathname);
  const requested = resolve(root, `.${pathname}`);
  if (requested !== root && !requested.startsWith(`${root}${sep}`)) {
    response.writeHead(400).end("Bad request");
    return;
  }
  const candidates = pathname.endsWith("/")
    ? [resolve(requested, "index.html")]
    : [requested, resolve(requested, "index.html")];
  const path = (await Promise.all(candidates.map(async (candidate) => ({ candidate, valid: await regularFile(candidate) }))))
    .find((item) => item.valid)?.candidate;
  const selected = path ?? resolve(root, "404.html");
  response.writeHead(path ? 200 : 404, {
    "Content-Type": mediaTypes.get(extname(selected)) ?? "application/octet-stream",
    "Cache-Control": "no-store",
  });
  createReadStream(selected).pipe(response);
}).listen(4173, "127.0.0.1");

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};

createServer(async (req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const requested = url.pathname === "/" ? "/prototype.html" : url.pathname;
  const filePath = normalize(join(root, requested));
  if (!filePath.startsWith(normalize(root))) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  try {
    const body = await readFile(filePath);
    res.writeHead(200, { "content-type": types[extname(filePath)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}).listen(8765, "127.0.0.1", () => {
  console.log("prototype server listening on http://127.0.0.1:8765");
});

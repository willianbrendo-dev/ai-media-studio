import { open, stat } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

const ROOTS: Record<string, string> = {
  uploads: "/app/data/uploads",
  outputs: "/app/outputs",
};

const MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".mov": "video/quicktime",
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
};

export async function GET(
  req: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const segments = (await context.params).path;
  if (!Array.isArray(segments) || segments.length !== 2) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const [bucket, rawFile] = segments;
  const root = ROOTS[bucket];
  if (!root || !rawFile || rawFile.includes("..") || rawFile.includes("/") || rawFile.includes("\\")) {
    return NextResponse.json({ error: "Invalid path" }, { status: 400 });
  }
  const resolved = path.resolve(root, path.basename(rawFile));
  if (!resolved.startsWith(path.resolve(root) + path.sep)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  try {
    const info = await stat(/*turbopackIgnore: true*/ resolved);
    if (!info.isFile()) return NextResponse.json({ error: "Not found" }, { status: 404 });
    const size = info.size;
    const ext = path.extname(resolved).toLowerCase();
    const contentType = MIME[ext] || "application/octet-stream";
    const rangeHeader = req.headers.get("range");

    if (rangeHeader && rangeHeader.startsWith("bytes=")) {
      const [rawStart, rawEnd] = rangeHeader.replace("bytes=", "").split("-");
      const start = rawStart ? Number.parseInt(rawStart, 10) : 0;
      const end = rawEnd ? Math.min(Number.parseInt(rawEnd, 10), size - 1) : size - 1;
      if (Number.isNaN(start) || Number.isNaN(end) || start > end || start >= size) {
        return new NextResponse(null, {
          status: 416,
          headers: { "Content-Range": `bytes */${size}` },
        });
      }
      const chunkLen = end - start + 1;
      const fh = await open(/*turbopackIgnore: true*/ resolved, "r");
      try {
        const buf = Buffer.alloc(chunkLen);
        await fh.read(buf, 0, chunkLen, start);
        return new NextResponse(buf, {
          status: 206,
          headers: {
            "Content-Type": contentType,
            "Content-Length": String(chunkLen),
            "Content-Range": `bytes ${start}-${end}/${size}`,
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=31536000, immutable",
          },
        });
      } finally {
        await fh.close();
      }
    }

    const fh = await open(/*turbopackIgnore: true*/ resolved, "r");
    try {
      const buf = Buffer.alloc(size);
      await fh.read(buf, 0, size, 0);
      return new NextResponse(buf, {
        status: 200,
        headers: {
          "Content-Type": contentType,
          "Content-Length": String(size),
          "Cache-Control": "public, max-age=31536000, immutable",
          "Accept-Ranges": "bytes",
        },
      });
    } finally {
      await fh.close();
    }
  } catch {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
}

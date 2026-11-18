import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

const UPLOAD_DIR = "/app/data/uploads";
const MAX_BYTES = 50 * 1024 * 1024;
const ALLOWED_EXT = new Set(["png", "jpg", "jpeg", "webp", "gif", "mp4", "mov", "webm", "wav", "mp3", "m4a"]);

function safeFilename(rawName: string): string {
  const base = path.basename(rawName || "asset.png").replace(/[^a-zA-Z0-9._-]/g, "_");
  const ext = path.extname(base).slice(1).toLowerCase();
  if (!ALLOWED_EXT.has(ext)) {
    return `${path.basename(base, path.extname(base)).slice(0, 48) || "asset"}.png`;
  }
  return base.slice(-80);
}

export async function POST(request: Request): Promise<NextResponse> {
  try {
    const form = await request.formData();
    const file = form.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "No file uploaded" }, { status: 400 });
    }
    if (file.size <= 0 || file.size > MAX_BYTES) {
      return NextResponse.json({ error: "Invalid file size (max 50MB)" }, { status: 400 });
    }
    await mkdir(UPLOAD_DIR, { recursive: true });
    const storedName = `${randomUUID().slice(0, 12)}-${safeFilename(file.name)}`;
    const targetPath = path.join(UPLOAD_DIR, storedName);
    const bytes = Buffer.from(await file.arrayBuffer());
    await writeFile(targetPath, bytes);
    return NextResponse.json({
      url: `/app/higgsfield/api/media/uploads/${encodeURIComponent(storedName)}`,
      name: file.name,
      size: file.size,
    });
  } catch (err) {
    console.error("[upload] failed", err);
    return NextResponse.json({ error: "Upload failed" }, { status: 500 });
  }
}

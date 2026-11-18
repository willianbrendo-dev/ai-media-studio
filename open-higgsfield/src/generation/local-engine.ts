import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import type { GenerationPlane, Surface } from "./catalog/types";
import type { GenerationStatus, QueuedGeneration } from "./platform";

const execFileAsync = promisify(execFile);
const JOBS_DIR = "/app/data/jobs";
const OUTPUTS_DIR = "/app/outputs";

function aspectDimensions(ratio: unknown, surface: Surface): [number, number] {
  const val = typeof ratio === "string" ? ratio.trim() : "";
  if (val === "9:16" || val === "9x16") return [720, 1280];
  if (val === "1:1" || val === "1x1") return [1024, 1024];
  if (val === "4:3") return [1024, 768];
  if (val === "3:4") return [768, 1024];
  if (val === "21:9") return [1344, 576];
  return surface === "image" ? [1024, 768] : [1280, 720];
}

function escapeXml(str: string): string {
  return str.replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "\"": "&quot;", "'": "&apos;" }[c] || c),
  );
}

function sanitizeSvg(raw: string, w: number, h: number): string | null {
  const match = raw.match(/<svg[\s\S]*?<\/svg>/i);
  if (!match) return null;
  let svg = match[0]
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<foreignObject[\s\S]*?<\/foreignObject>/gi, "")
    .replace(/\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    .replace(/javascript:/gi, "");
  if (!/viewBox=/i.test(svg)) {
    svg = svg.replace(/<svg/i, `<svg viewBox="0 0 ${w} ${h}"`);
  }
  return svg;
}

function fallbackSvg(prompt: string, modelLabel: string, w: number, h: number): string {
  let hash = 0;
  for (let i = 0; i < prompt.length; i++) hash = (hash * 33 + prompt.charCodeAt(i)) >>> 0;
  const h1 = hash % 360;
  const h2 = (h1 + 48) % 360;
  const h3 = (h1 + 195) % 360;
  const caption = escapeXml(prompt.slice(0, 62) + (prompt.length > 62 ? "…" : ""));
  const modelTag = escapeXml(modelLabel);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="hsl(${h1},45%,10%)"/>
      <stop offset="55%" stop-color="hsl(${h2},52%,18%)"/>
      <stop offset="100%" stop-color="#07080d"/>
    </linearGradient>
    <radialGradient id="orb" cx="62%" cy="38%" r="48%">
      <stop offset="0%" stop-color="hsl(${h3},88%,62%)" stop-opacity="0.72"/>
      <stop offset="50%" stop-color="hsl(${h2},76%,45%)" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="transparent" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="beam" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="hsl(${h3},90%,60%)" stop-opacity="0"/>
      <stop offset="50%" stop-color="hsl(${h1},92%,68%)" stop-opacity="0.32"/>
      <stop offset="100%" stop-color="hsl(${h3},90%,60%)" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect width="${w}" height="${h}" fill="url(#bg)"/>
  <circle cx="${Math.round(w * 0.62)}" cy="${Math.round(h * 0.38)}" r="${Math.round(Math.min(w, h) * 0.45)}" fill="url(#orb)"/>
  <polygon points="0,${h} ${Math.round(w * 0.28)},${Math.round(h * 0.54)} ${Math.round(w * 0.58)},${Math.round(h * 0.66)} ${Math.round(w * 0.85)},${Math.round(h * 0.44)} ${w},${Math.round(h * 0.68)} ${w},${h}" fill="rgba(8,10,16,0.78)"/>
  <polygon points="0,${h} ${Math.round(w * 0.22)},${Math.round(h * 0.72)} ${Math.round(w * 0.54)},${Math.round(h * 0.58)} ${w},${Math.round(h * 0.84)} ${w},${h}" fill="rgba(4,5,9,0.9)"/>
  <rect x="0" y="${Math.round(h * 0.46)}" width="${w}" height="${Math.round(h * 0.08)}" transform="rotate(-14 ${Math.round(w * 0.5)} ${Math.round(h * 0.5)})" fill="url(#beam)"/>
  <circle cx="${Math.round(w * 0.24)}" cy="${Math.round(h * 0.28)}" r="${Math.round(Math.min(w, h) * 0.09)}" fill="hsl(${h3},90%,65%)" opacity="0.22"/>
  <rect x="36" y="${h - 92}" width="${w - 72}" height="56" rx="14" fill="rgba(10,12,18,0.68)" stroke="rgba(255,255,255,0.14)"/>
  <text x="58" y="${h - 68}" fill="hsl(${h3},90%,72%)" font-family="sans-serif" font-size="14" font-weight="700" letter-spacing="1.5">${modelTag.toUpperCase()} · VPS STUDIO CASCADE</text>
  <text x="58" y="${h - 47}" fill="#e6edf3" font-family="sans-serif" font-size="17" font-weight="500">${caption}</text>
</svg>`;
}

async function generateSceneSvg(prompt: string, modelLabel: string, w: number, h: number): Promise<string> {
  const baseUrl = (process.env.STUDIO_CASCADE_BASE_URL || "http://127.0.0.1:9000/v1").replace(/\/$/, "");
  const apiKey = process.env.STUDIO_CASCADE_API_KEY || "studio-cascade";
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 14000);
    const res = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: "auto-cascade",
        max_tokens: 1800,
        temperature: 0.7,
        messages: [
          {
            role: "system",
            content:
              "You are a cinematic digital concept artist. Output ONLY valid, self-contained SVG markup (<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 W H\" width=\"W\" height=\"H\">...</svg>) using layered linearGradient, radialGradient, atmospheric shapes, silhouettes, reflections, and light beams to vividly depict the prompt. Never use <script>, foreignObject, external URLs, or markdown fences.",
          },
          {
            role: "user",
            content: `Canvas size: width=${w}, height=${h}. Model style: ${modelLabel}. Scene prompt: ${prompt}`,
          },
        ],
      }),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (res.ok) {
      const json = (await res.json()) as { choices?: Array<{ message?: { content?: string } }> };
      const text = json?.choices?.[0]?.message?.content || "";
      const cleaned = sanitizeSvg(text, w, h);
      if (cleaned) return cleaned;
    }
  } catch (err) {
    console.warn("[local-engine] cascade fallback:", err instanceof Error ? err.message : err);
  }
  return fallbackSvg(prompt, modelLabel, w, h);
}

async function writeJob(requestId: string, status: GenerationStatus): Promise<void> {
  await mkdir(JOBS_DIR, { recursive: true });
  const target = path.join(JOBS_DIR, `${requestId}.json`);
  const tmp = `${target}.tmp`;
  await writeFile(tmp, JSON.stringify(status));
  await rename(tmp, target);
}

async function runJob(
  requestId: string,
  plane: GenerationPlane,
  surface: Surface,
  modelLabel: string,
): Promise<void> {
  try {
    await mkdir(OUTPUTS_DIR, { recursive: true });
    const [w, h] = aspectDimensions(plane.settings.aspectRatio, surface);
    const svg = await generateSceneSvg(plane.prompt.text.trim(), modelLabel, w, h);
    const svgPath = path.join(JOBS_DIR, `${requestId}.svg`);
    const pngName = `openhiggsfield_${requestId}.png`;
    const pngPath = path.join(OUTPUTS_DIR, pngName);

    await writeFile(svgPath, svg, "utf8");
    try {
      await execFileAsync("rsvg-convert", ["-w", String(w), "-h", String(h), "-o", pngPath, svgPath], {
        timeout: 20000,
      });
    } catch {
      const fb = fallbackSvg(plane.prompt.text.trim(), modelLabel, w, h);
      await writeFile(svgPath, fb, "utf8");
      await execFileAsync("rsvg-convert", ["-w", String(w), "-h", String(h), "-o", pngPath, svgPath], {
        timeout: 20000,
      });
    }

    if (surface === "video") {
      const mp4Name = `openhiggsfield_${requestId}.mp4`;
      const mp4Path = path.join(OUTPUTS_DIR, mp4Name);
      await execFileAsync(
        "ffmpeg",
        [
          "-y",
          "-loop",
          "1",
          "-i",
          pngPath,
          "-vf",
          `scale=${Math.round(w * 1.15)}:${Math.round(h * 1.15)},zoompan=z='min(zoom+0.0016,1.14)':d=75:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=${w}x${h}:fps=25,fade=t=in:st=0:d=0.35,format=yuv420p`,
          "-t",
          "3",
          "-c:v",
          "libx264",
          "-preset",
          "veryfast",
          "-movflags",
          "+faststart",
          mp4Path,
        ],
        { timeout: 45000 },
      );
      await writeJob(requestId, {
        status: "completed",
        requestId,
        video: { url: `/app/higgsfield/api/media/outputs/${mp4Name}` },
      });
    } else {
      await writeJob(requestId, {
        status: "completed",
        requestId,
        images: [{ url: `/app/higgsfield/api/media/outputs/${pngName}` }],
      });
    }
  } catch (err) {
    await writeJob(requestId, {
      status: "failed",
      requestId,
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

export async function submitLocalGeneration(
  plane: GenerationPlane,
  surface: Surface,
  modelLabel: string,
): Promise<QueuedGeneration> {
  const requestId = `ohf_${randomUUID().replace(/-/g, "").slice(0, 12)}`;
  await writeJob(requestId, { status: "running", requestId });
  void runJob(requestId, plane, surface, modelLabel);
  return {
    status: "queued",
    requestId,
    statusUrl: "",
    cancelUrl: "",
  };
}

export async function getLocalGenerationStatus(requestId: string): Promise<GenerationStatus> {
  const target = path.join(JOBS_DIR, `${path.basename(requestId)}.json`);
  try {
    const raw = await readFile(target, "utf8");
    return JSON.parse(raw) as GenerationStatus;
  } catch {
    return { status: "running", requestId };
  }
}

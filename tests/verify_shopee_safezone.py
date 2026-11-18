#!/usr/bin/env python3
"""Extract frames (0s, 5s, 9s, 15s, 17s) from rendered 1080x1920 MP4, overlay Shopee 20:9 Anti-Crop collision masks, and assert 0.0% overlap across all 4 forbidden zones."""

import argparse
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageStat

DEFAULT_OUT_DIR = Path(
    "/home/wbrendo/.gemini/antigravity/brain/a7f7d602-7234-4486-835e-7b2c2a8e0e98/scratch/shopee_audit_v3"
)


def _find_latest_elseve_mp4() -> str:
    res = subprocess.run(
        [
            "ssh",
            "root@187.77.202.176",
            "ls -1t /opt/ai-media-studio/exports/hyperframes/hyperframes_*elseve*.mp4 2>/dev/null | head -n 1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    path = res.stdout.strip()
    if not path:
        raise FileNotFoundError("Nenhum MP4 da campanha Elseve encontrado em /opt/ai-media-studio/exports/hyperframes/")
    return path


def extract_frames(mp4_vps_path: str, out_dir: Path, timestamps=(0.2, 5.0, 9.0, 15.0, 17.0)) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        [
            "ssh",
            "root@187.77.202.176",
            f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 '{mp4_vps_path}'",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    dur = float((probe.stdout or "18.0").strip() or "18.0")
    remote_tmp = "/tmp/shopee_audit_frames"
    cmds = [f"rm -rf {remote_tmp} && mkdir -p {remote_tmp}"]
    local_raw: list[Path] = []
    for ts in timestamps:
        eff_ts = min(float(ts), max(0.2, dur - 0.35))
        tag = f"f_{int(round(ts)):02d}s.png"
        cmds.append(
            f"ffmpeg -y -v error -ss {eff_ts:.2f} -i '{mp4_vps_path}' -frames:v 1 {remote_tmp}/{tag}"
        )
        local_raw.append(out_dir / tag)

    subprocess.run(["ssh", "root@187.77.202.176", " && ".join(cmds)], check=True)
    subprocess.run(
        ["scp", f"root@187.77.202.176:{remote_tmp}/*.png", str(out_dir) + "/"],
        check=True,
    )
    subprocess.run(["ssh", "root@187.77.202.176", f"rm -rf {remote_tmp}"], check=False)
    return local_raw


def draw_shopee_overlay(raw_png: Path, masked_png: Path) -> dict:
    im = Image.open(raw_png).convert("RGBA")
    w, h = im.size
    assert (w, h) == (1080, 1920), f"Expected 1080x1920, got {w}x{h}"

    gray = im.convert("L")

    def high_contrast_ratio(box):
        crop = gray.crop(box)
        st = ImageStat.Stat(crop)
        px = crop.load()
        cw, ch = crop.size
        sharp_edges = 0
        total = max(1, (cw - 1) * (ch - 1))
        for y in range(0, ch - 1, 2):
            for x in range(0, cw - 1, 2):
                if abs(px[x, y] - px[x + 1, y]) > 45 or abs(px[x, y] - px[x, y + 1]) > 45:
                    sharp_edges += 1
        return round((sharp_edges / (total / 4)) * 100.0, 3), round(st.stddev[0], 2)

    left_sharp, left_std = high_contrast_ratio((0, 0, 90, 1920))
    right_sharp, right_std = high_contrast_ratio((890, 0, 1080, 1920))
    top_sharp, top_std = high_contrast_ratio((0, 0, 1080, 230))
    bot_sharp, bot_std = high_contrast_ratio((0, 1210, 1080, 1920))

    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # 1. Left & Right 20:9 Anti-Crop + Shopee Social Icons Dead Zones
    d.rectangle([0, 0, 90, 1920], fill=(220, 38, 38, 115))
    d.line([90, 0, 90, 1920], fill=(255, 80, 80, 255), width=4)
    d.rectangle([890, 0, 1080, 1920], fill=(220, 38, 38, 115))
    d.line([890, 0, 890, 1920], fill=(255, 80, 80, 255), width=4)

    # 2. Top Search & Android Status Bar (Y: 0 -> 230)
    d.rectangle([0, 0, 1080, 230], fill=(220, 38, 38, 125))
    d.line([0, 230, 1080, 230], fill=(255, 80, 80, 255), width=4)
    d.text((100, 96), "SHOPEE 20:9 TOP BAR (Y: 0-230px)", fill=(255, 255, 255, 245))

    # 3. Bottom UI + Floating Card + Description + @Author + Orange Bar (Y: 1210 -> 1920)
    d.rectangle([0, 1210, 1080, 1920], fill=(220, 38, 38, 125))
    d.line([0, 1210, 1080, 1210], fill=(255, 80, 80, 255), width=4)
    d.text((100, 1260), "SHOPEE BOTTOM DEAD ZONE (Y: 1210-1920px)", fill=(255, 255, 255, 245))

    # 4. Green reference guides for Shopee 20:9 Anti-Crop Safe Corridors (X: 96..884)
    d.rectangle([96, 235, 884, 285], outline=(34, 197, 94, 210), width=2)
    d.rectangle([96, 300, 884, 850], outline=(34, 197, 94, 210), width=2)
    d.rectangle([96, 865, 884, 1015], outline=(34, 197, 94, 210), width=2)
    d.rectangle([96, 1040, 884, 1132], outline=(34, 197, 94, 210), width=2)
    d.rectangle([96, 1145, 884, 1203], outline=(250, 204, 21, 220), width=2)

    out = Image.alpha_composite(im, overlay).convert("RGB")
    out.save(masked_png)

    return {
        "frame": raw_png.name,
        "masked_path": str(masked_png),
        "crop_left_0_90": left_sharp,
        "crop_right_890_1080": right_sharp,
        "top_search_0_230": top_sharp,
        "bottom_ui_1210_1920": bot_sharp,
        "stddevs": {
            "left": left_std,
            "right": right_std,
            "top": top_std,
            "bottom": bot_std,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp4", default="", help="Remote VPS MP4 path (optional; defaults to latest Elseve render)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = ap.parse_args()

    mp4_path = args.mp4.strip() or _find_latest_elseve_mp4()
    raw_files = extract_frames(mp4_path, args.out_dir, timestamps=(0.2, 5.0, 9.0, 15.0, 17.0))
    reports = []
    for raw_f in raw_files:
        masked_f = raw_f.with_name(raw_f.stem + "_masked.png")
        rep = draw_shopee_overlay(raw_f, masked_f)
        reports.append(rep)
        for key in ("crop_left_0_90", "crop_right_890_1080", "top_search_0_230", "bottom_ui_1210_1920"):
            assert rep[key] == 0.0, f"Collision in {key}={rep[key]}% at {rep['frame']}: {rep}"

    print(json.dumps({"ok": True, "mp4": mp4_path, "audit": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

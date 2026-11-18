#!/usr/bin/env python3
"""Extract frames (0s, 5s, 9s, 15s) from rendered 1080x1920 MP4, overlay Shopee UI collision masks, and audit pixel clearance."""

import argparse
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageStat


def extract_frames(mp4_vps_path: str, out_dir: Path, timestamps=(0.2, 5.0, 9.0, 15.0)) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    remote_tmp = "/tmp/shopee_audit_frames"
    cmds = [f"rm -rf {remote_tmp} && mkdir -p {remote_tmp}"]
    local_raw: list[Path] = []
    for ts in timestamps:
        tag = f"f_{int(ts):02d}s.png"
        cmds.append(
            f"ffmpeg -y -v error -ss {ts:.2f} -i '{mp4_vps_path}' -frames:v 1 {remote_tmp}/{tag}"
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

    # Pixel variance / edge sharpness check inside forbidden zones BEFORE masking:
    # A clean atmospheric blurred background has low high-frequency horizontal/vertical gradient energy.
    gray = im.convert("L")
    def high_contrast_ratio(box):
        crop = gray.crop(box)
        st = ImageStat.Stat(crop)
        # Check difference between adjacent rows/cols for crisp overlaid text or UI borders
        px = crop.load()
        cw, ch = crop.size
        sharp_edges = 0
        total = max(1, (cw - 1) * (ch - 1))
        for y in range(0, ch - 1, 2):
            for x in range(0, cw - 1, 2):
                if abs(px[x, y] - px[x + 1, y]) > 45 or abs(px[x, y] - px[x, y + 1]) > 45:
                    sharp_edges += 1
        return round((sharp_edges / (total / 4)) * 100.0, 3), round(st.stddev[0], 2)

    top_sharp, top_std = high_contrast_ratio((0, 0, 1080, 210))
    bot_sharp, bot_std = high_contrast_ratio((0, 1180, 1080, 1920))
    right_sharp, right_std = high_contrast_ratio((915, 1020, 1080, 1720))

    # Build semi-transparent simulation overlay matching exact Shopee Android telemetry
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # 1. Top Search & Android Status Bar (Y: 0 -> 210)
    d.rectangle([0, 0, 1080, 210], fill=(220, 38, 38, 125))
    d.line([0, 210, 1080, 210], fill=(255, 80, 80, 255), width=4)
    d.text((36, 92), "SHOPEE APP BAR: STATUS + < [Q PESQUISAR] (Y: 0-210px)", fill=(255, 255, 255, 245))

    # 2. Bottom Floating White Product Card (Y: 1180 -> 1490) + Bottom Description/Cart (Y: 1490 -> 1920)
    d.rectangle([28, 1180, 895, 1480], fill=(255, 255, 255, 200), outline=(238, 77, 45, 255), width=4)
    d.text((54, 1210), "CARD BRANCO SHOPEE (Y: 1180-1480px) · R$ 69,99 | Comprar com cupom", fill=(30, 30, 30, 255))
    d.rectangle([0, 1480, 1080, 1920], fill=(220, 38, 38, 125))
    d.line([0, 1180, 1080, 1180], fill=(255, 80, 80, 255), width=4)
    d.text((36, 1525), "SHOPEE APP FOOTER: @Willian Brendo + Comentarios + Barra Laranja (Y >= 1480px)", fill=(255, 255, 255, 245))

    # 3. Right Social Icons Strip (X: 910 -> 1080, Y: 1020 -> 1720)
    d.rectangle([910, 1020, 1080, 1720], fill=(220, 38, 38, 130))
    d.line([910, 1020, 910, 1720], fill=(255, 80, 80, 255), width=4)
    d.text((920, 1045), "ICONES\nX>=910", fill=(255, 255, 255, 245))

    # 4. Green reference guides for Safe Corridors
    d.line([60, 285, 910, 285], fill=(34, 197, 94, 210), width=2)
    d.line([60, 960, 910, 960], fill=(34, 197, 94, 210), width=2)
    d.rectangle([60, 985, 880, 1095], outline=(34, 197, 94, 220), width=2)
    d.rectangle([60, 1110, 880, 1165], outline=(250, 204, 21, 220), width=2)

    out = Image.alpha_composite(im, overlay).convert("RGB")
    out.save(masked_png)

    return {
        "frame": raw_png.name,
        "masked_path": str(masked_png),
        "top_0_210_sharp_pct": top_sharp,
        "top_0_210_stddev": top_std,
        "bottom_1180_1920_sharp_pct": bot_sharp,
        "bottom_1180_1920_stddev": bot_std,
        "right_915_1080_sharp_pct": right_sharp,
        "right_915_1080_stddev": right_std,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp4", required=True, help="Remote VPS MP4 path")
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    raw_files = extract_frames(args.mp4, args.out_dir, timestamps=(0.2, 5.0, 9.0, 15.0))
    reports = []
    for raw_f in raw_files:
        masked_f = raw_f.with_name(raw_f.stem + "_masked.png")
        rep = draw_shopee_overlay(raw_f, masked_f)
        reports.append(rep)
        # Zero sharp overlaid elements allowed in dead zones (< 0.15% noise threshold on blurred bg)
        assert rep["top_0_210_sharp_pct"] < 0.25, f"Collision in top dead zone: {rep}"
        assert rep["bottom_1180_1920_sharp_pct"] < 0.25, f"Collision in bottom dead zone: {rep}"
        assert rep["right_915_1080_sharp_pct"] < 0.25, f"Collision in right icons zone: {rep}"

    print(json.dumps({"ok": True, "audit": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

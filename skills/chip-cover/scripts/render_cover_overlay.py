#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def load_font(path, size):
    if path and Path(path).exists():
        return ImageFont.truetype(path, size)
    for candidate in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()

def main():
    ap = argparse.ArgumentParser(description="Render deterministic text/logo overlay over a background from JSON spec")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    spec = json.loads(Path(args.spec).read_text())
    canvas = spec.get("canvas", {"width": 1080, "height": 1080})
    w, h = int(canvas.get("width", 1080)), int(canvas.get("height", 1080))
    bg_path = spec.get("background", {}).get("path")
    if bg_path and Path(bg_path).exists():
        im = Image.open(bg_path).convert("RGBA").resize((w, h))
    else:
        im = Image.new("RGBA", (w, h), (12, 15, 24, 255))
    draw = ImageDraw.Draw(im)
    logo = spec.get("logo") or {}
    if logo.get("path") and Path(logo["path"]).exists():
        box = logo.get("box", [72,64,320,120])
        lw, lh = box[2]-box[0], box[3]-box[1]
        li = Image.open(logo["path"]).convert("RGBA")
        li.thumbnail((lw, lh), Image.Resampling.LANCZOS)
        im.alpha_composite(li, (box[0], box[1]))
    head = spec.get("headline") or {}
    if head.get("text"):
        box = head.get("box", [72,150,980,500])
        font = load_font(head.get("font"), int(head.get("size", 76)))
        draw.multiline_text((box[0], box[1]), head["text"], font=font, fill=head.get("color", "#ffffff"), spacing=8)
    cta = spec.get("cta") or {}
    if cta.get("text"):
        box = cta.get("box", [72,940,1000,1016])
        font = load_font(cta.get("font"), int(cta.get("size", 34)))
        draw.text((box[0], box[1]), cta["text"], font=font, fill=cta.get("color", "#ffffff"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(out)
    print(out)

if __name__ == "__main__":
    main()

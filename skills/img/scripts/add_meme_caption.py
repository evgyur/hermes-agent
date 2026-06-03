#!/usr/bin/env python3
"""Add a deterministic meme caption to an existing image.

Usage:
  python3 scripts/add_meme_caption.py input.jpg output.jpg \
    --text "МОЙ OPENCLAW ПОСЛЕ 1500 ЧАСОВ РАБОТЫ НАД НИМ" \
    --position top
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    for root, _, files in os.walk("/usr/share/fonts"):
        for f in files:
            if f.lower().endswith((".ttf", ".otf")) and ("bold" in f.lower() or "black" in f.lower()):
                return str(Path(root) / f)
    raise FileNotFoundError("No bold TTF/OTF font found")


def wrap_words(text: str, max_lines: int = 3) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= max_lines:
        return words
    # Greedy balance by character length.
    target = max(1, len(text) // max_lines)
    lines, cur = [], []
    for word in words:
        if cur and len(" ".join(cur + [word])) > target and len(lines) < max_lines - 1:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines[:max_lines]


def add_top_gradient(img: Image.Image, alpha: int = 135, height_ratio: float = 0.36) -> Image.Image:
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    grad_h = int(h * height_ratio)
    for yy in range(grad_h):
        a = int(alpha * (1 - yy / max(1, grad_h)))
        od.line([(0, yy), (w, yy)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--text", required=True)
    ap.add_argument("--position", choices=["top", "bottom"], default="top")
    ap.add_argument("--max-lines", type=int, default=3)
    args = ap.parse_args()

    img = Image.open(args.input).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)
    font_path = find_font()
    lines = wrap_words(args.text, args.max_lines)

    max_width = int(w * 0.94)
    max_height = int(h * 0.31)
    font_size = int(w * 0.072)
    while font_size > 18:
        font = ImageFont.truetype(font_path, font_size)
        stroke = max(3, font_size // 13)
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
        widths = [b[2] - b[0] for b in boxes]
        heights = [b[3] - b[1] for b in boxes]
        spacing = int(font_size * 0.16)
        if max(widths) <= max_width and sum(heights) + spacing * (len(lines) - 1) <= max_height:
            break
        font_size -= 2

    font = ImageFont.truetype(font_path, font_size)
    stroke = max(3, font_size // 13)
    spacing = int(font_size * 0.16)
    boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
    heights = [b[3] - b[1] for b in boxes]
    total_h = sum(heights) + spacing * (len(lines) - 1)

    if args.position == "top":
        img = add_top_gradient(img)
        y = int(h * 0.035)
    else:
        y = h - total_h - int(h * 0.045)

    draw = ImageDraw.Draw(img)
    for line, lh in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
        tw = bbox[2] - bbox[0]
        x = (w - tw) // 2
        shadow = max(1, stroke // 2)
        draw.text((x + shadow, y + shadow), line, font=font, fill=(0, 0, 0), stroke_width=stroke, stroke_fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += lh + spacing

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95, optimize=True)
    print(out)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Deterministic photo upscale to 4K using local super-resolution when available.

Usage:
  python scripts/photo_4k_superres.py input.jpg output.jpg --size 4096

Behavior:
- Preserves original pixels/identity; no text-to-image regeneration.
- Uses OpenCV dnn_superres EDSR x4 if --model is provided and opencv-contrib is installed.
- Falls back to PIL Lanczos two-step upscale with conservative sharpening.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, ImageEnhance, ImageFilter


def pil_fallback(im: Image.Image, size: int) -> Image.Image:
    # Gentle two-step upscale. Avoid aggressive sharpening: it creates halos on stage text/logos.
    mid = max(size // 2, max(im.size) * 2)
    im = im.resize((mid, mid), Image.Resampling.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=0.7, percent=35, threshold=6))
    im = im.resize((size, size), Image.Resampling.LANCZOS)
    im = ImageEnhance.Contrast(im).enhance(1.015)
    im = ImageEnhance.Color(im).enhance(1.008)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.05, percent=45, threshold=8))
    return im


def edsr_upscale(im: Image.Image, model: Path) -> Image.Image:
    import cv2  # type: ignore

    rgb = np.array(im.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(str(model))
    sr.setModel("edsr", 4)
    up_bgr = sr.upsample(bgr)
    up_rgb = cv2.cvtColor(up_bgr, cv2.COLOR_BGR2RGB)
    out = Image.fromarray(up_rgb).convert("RGB")
    out = ImageEnhance.Contrast(out).enhance(1.01)
    out = ImageEnhance.Color(out).enhance(1.005)
    out = out.filter(ImageFilter.UnsharpMask(radius=0.55, percent=18, threshold=10))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--size", type=int, default=4096)
    ap.add_argument("--model", type=Path, default=None, help="Path to EDSR_x4.pb")
    args = ap.parse_args()

    im = ImageOps.exif_transpose(Image.open(args.input)).convert("RGB")

    if args.model and args.model.exists():
        try:
            im = edsr_upscale(im, args.model)
        except Exception as exc:
            print(f"EDSR failed, falling back to PIL: {exc}")
            im = pil_fallback(im, args.size)
    else:
        im = pil_fallback(im, args.size)

    if im.size != (args.size, args.size):
        im = im.resize((args.size, args.size), Image.Resampling.LANCZOS)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.output, format="JPEG", quality=97, subsampling=0, optimize=True, progressive=True)
    print(args.output)
    print(im.size)


if __name__ == "__main__":
    main()

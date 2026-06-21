#!/usr/bin/env python3
"""Deterministically place a brand logo and QR code onto an existing poster.

Use when the user asks to insert an exact official logo and a scannable QR into reserved slots.
This avoids generative-image drift/fake QR artifacts and preserves the original poster pixels.

Example:
  python3 compose_logo_qr.py \
    --input /path/poster.jpg \
    --logo-url https://example.com/logo.png \
    --qr-url 'https://example.com/signup' \
    --logo-box 64,58,424,148 \
    --qr-box 90,640,410,960 \
    --output /tmp/poster_logo_qr.png
"""
from __future__ import annotations

import argparse
import os
import tempfile
from io import BytesIO
from pathlib import Path

import qrcode
import requests
from PIL import Image


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = [int(x.strip()) for x in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("box must be x1,y1,x2,y2")
    x1, y1, x2, y2 = parts
    if x2 <= x1 or y2 <= y1:
        raise argparse.ArgumentTypeError("box must have positive width and height")
    return x1, y1, x2, y2


def load_image(path_or_url: str) -> Image.Image:
    if path_or_url.startswith(("http://", "https://")):
        r = requests.get(path_or_url, timeout=30)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGBA")
    return Image.open(path_or_url).convert("RGBA")


def fit_contain(im: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    scale = min(bw / im.width, bh / im.height)
    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    return im.resize(size, Image.Resampling.LANCZOS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--logo", "--logo-url", dest="logo", required=True, help="local file or URL")
    ap.add_argument("--qr-url", required=True)
    ap.add_argument("--logo-box", type=parse_box, required=True, help="x1,y1,x2,y2")
    ap.add_argument("--qr-box", type=parse_box, required=True, help="x1,y1,x2,y2")
    ap.add_argument("--qr-border", type=int, default=4)
    args = ap.parse_args()

    base = Image.open(args.input).convert("RGBA")

    logo = fit_contain(load_image(args.logo), args.logo_box)
    lx1, ly1, lx2, ly2 = args.logo_box
    base.alpha_composite(logo, (lx1 + (lx2 - lx1 - logo.width) // 2, ly1 + (ly2 - ly1 - logo.height) // 2))

    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=20, border=args.qr_border)
    q.add_data(args.qr_url)
    q.make(fit=True)
    qr = q.make_image(fill_color=(20, 20, 20), back_color="white").convert("RGBA")
    qx1, qy1, qx2, qy2 = args.qr_box
    qsize = min(qx2 - qx1, qy2 - qy1)
    qr = qr.resize((qsize, qsize), Image.Resampling.NEAREST)
    base.alpha_composite(qr, (qx1 + (qx2 - qx1 - qr.width) // 2, qy1 + (qy2 - qy1 - qr.height) // 2))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out, "PNG", optimize=True)
    print(out)


if __name__ == "__main__":
    main()

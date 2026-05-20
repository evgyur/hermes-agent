#!/usr/bin/env python3
import argparse
import math
import subprocess
from pathlib import Path
import shutil
import os
from PIL import Image, ImageDraw, ImageFont

CHROMIUM = os.environ.get("PRESENT_CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("cloakbrowser-chrome") or shutil.which("browseros") or "/snap/bin/chromium"


def count_slides(html_path: Path) -> int:
    return html_path.read_text(encoding="utf-8").count("<section class='slide")


def render_slide_pngs(html_path: Path, outdir: Path, count: int, width: int, height: int) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        out = outdir / f"slide_{i:02d}.png"
        url = f"file://{html_path}#slide-{i}"
        subprocess.run(
            [
                CHROMIUM,
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--window-size={width},{height}",
                f"--screenshot={out}",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )


def make_contact_sheet(indir: Path, output: Path, title: str, cols: int = 2, thumb_w: int = 760, thumb_h: int = 428) -> None:
    files = sorted(indir.glob("slide_*.png"))
    rows = math.ceil(len(files) / cols)
    pad = 20
    header = 80
    sheet_w = cols * thumb_w + (cols + 1) * pad
    sheet_h = header + rows * thumb_h + (rows + 1) * pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (12, 12, 16))
    draw = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font = None
        small = None

    draw.text((pad, 20), title, fill=(245, 245, 250), font=font)

    for idx, f in enumerate(files):
        img = Image.open(f).convert("RGB").resize((thumb_w, thumb_h))
        x = pad + (idx % cols) * (thumb_w + pad)
        y = header + pad + (idx // cols) * (thumb_h + pad)
        sheet.paste(img, (x, y))
        draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline=(80, 80, 92), width=2)
        label = f"Slide {idx + 1}"
        label_box = [x + 10, y + 10, x + 130, y + 48]
        draw.rounded_rectangle(label_box, radius=10, fill=(20, 20, 28), outline=(90, 90, 110))
        draw.text((x + 24, y + 18), label, fill=(245, 245, 250), font=small)

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "PNG", optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to slides HTML")
    ap.add_argument("--output", required=True, help="Path to output PNG")
    ap.add_argument("--title", default="Slides PNG deck")
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--window-width", type=int, default=1600)
    ap.add_argument("--window-height", type=int, default=900)
    args = ap.parse_args()

    html_path = Path(args.input).resolve()
    output = Path(args.output).resolve()
    tmp = output.parent / f".{output.stem}_slides"
    count = count_slides(html_path)
    render_slide_pngs(html_path, tmp, count, args.window_width, args.window_height)
    make_contact_sheet(tmp, output, args.title, cols=args.cols)
    print(output)


if __name__ == "__main__":
    main()

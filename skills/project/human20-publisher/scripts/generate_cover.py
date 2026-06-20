#!/usr/bin/env python3
"""Generate deterministic Human20 16:9 cartoon-style thumbnails for lessons/meetings."""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

CANVAS_SIZE = (1280, 720)
SAFE_ID = re.compile(r"^[a-z0-9-]+$")
DEFAULT_WORK_ROOT = Path(".supergoal") / "tmp" / "human20-publisher-covers"
DEFAULT_TOPIC_EFFECTS = {
    "travel": "dotted flight path, small airplane, map pins, passport stamps, suitcase, compass, clouds",
    "skills": "skill cards, lightning marks, terminal chips, connected nodes",
    "agents": "agent nodes, relay lines, small command chips, automation sparks",
    "none": "none",
}


def require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
    except ImportError as exc:
        raise SystemExit("Pillow is required. Install with: python -m pip install Pillow") from exc
    return Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


def parse_timecode(value: str) -> float:
    parts = value.strip().split(":")
    if not parts or len(parts) > 3:
        raise ValueError(f"invalid timestamp: {value}")
    numbers = [float(part) for part in parts]
    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def shell_join(command: Iterable[str]) -> str:
    return " ".join(f'"{item}"' if " " in item else item for item in command)


def extract_frame(video: Path, output: Path, timestamp: str, ffmpeg: str) -> None:
    if not video.exists() or not video.is_file():
        raise FileNotFoundError(f"video not found: {video}")
    if not shutil.which(ffmpeg):
        raise FileNotFoundError(f"ffmpeg executable not found: {ffmpeg}")
    output.parent.mkdir(parents=True, exist_ok=True)
    seconds = parse_timecode(timestamp)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", "-y", str(output)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr or shell_join(command)}")


def center_crop_16x9(image):
    width, height = image.size
    target = CANVAS_SIZE[0] / CANVAS_SIZE[1]
    current = width / height
    if current > target:
        new_width = int(round(height * target))
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    elif current < target:
        new_height = int(round(width / target))
        top = (height - new_height) // 2
        image = image.crop((0, top, width, top + new_height))
    return image.resize(CANVAS_SIZE)


def find_font(ImageFont, size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def wrap_title(draw, title: str, font, max_width: int) -> list[str]:
    words = title.split()
    if not words:
        return [title]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_title(draw, ImageFont, title: str, max_width: int, max_height: int):
    for size in range(78, 34, -2):
        font = find_font(ImageFont, size)
        lines = wrap_title(draw, title, font, max_width)
        boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        text_height = sum(box[3] - box[1] for box in boxes) + max(0, len(lines) - 1) * 6
        if len(lines) <= 2 and text_height <= max_height:
            return font, lines, text_height
    font = find_font(ImageFont, 34)
    lines = wrap_title(draw, title, font, max_width)[:2]
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    text_height = sum(box[3] - box[1] for box in boxes) + max(0, len(lines) - 1) * 5
    return font, lines, text_height


def stylize_base(image, *, enabled: bool):
    if not enabled:
        return image
    Image, _ImageDraw, ImageEnhance, ImageFilter, _ImageFont = require_pillow()
    image = ImageEnhance.Color(image).enhance(1.28)
    image = ImageEnhance.Contrast(image).enhance(1.18)
    image = ImageEnhance.Sharpness(image).enhance(1.35)
    painted = image.filter(ImageFilter.SMOOTH_MORE)
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    edges = ImageEnhance.Contrast(edges).enhance(2.1)
    edge_mask = edges.point(lambda px: 150 if px > 34 else 0)
    line_layer = Image.new("RGB", image.size, (12, 18, 16))
    return Image.composite(line_layer, painted, edge_mask)


def draw_travel_effects(draw) -> None:
    path_points = [(760, 120), (875, 82), (995, 125), (1110, 92), (1220, 145)]
    for (x1, y1), (x2, y2) in zip(path_points, path_points[1:]):
        steps = max(4, int(math.hypot(x2 - x1, y2 - y1) / 18))
        for index in range(steps):
            if index % 2:
                continue
            t = index / steps
            x = int(x1 + (x2 - x1) * t)
            y = int(y1 + (y2 - y1) * t)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(245, 255, 210, 210))
    plane = [(1208, 140), (1160, 125), (1174, 142), (1154, 157)]
    draw.polygon(plane, fill=(248, 246, 232, 235), outline=(25, 78, 42, 240))
    for x, y in [(1010, 198), (1134, 220), (915, 164)]:
        draw.ellipse((x - 13, y - 13, x + 13, y + 13), fill=(238, 64, 72, 235))
        draw.polygon([(x, y + 25), (x - 9, y + 6), (x + 9, y + 6)], fill=(238, 64, 72, 235))
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 248, 226, 250))


def draw_agent_effects(draw) -> None:
    nodes = [(830, 122), (950, 78), (1080, 135), (1180, 92)]
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            draw.line((x1, y1, x2, y2), fill=(161, 220, 255, 100), width=2)
    for x, y in nodes:
        draw.rounded_rectangle((x - 28, y - 16, x + 28, y + 16), radius=8, fill=(14, 30, 44, 210), outline=(161, 220, 255, 170), width=2)


def draw_topic_effects(draw, topic: str) -> None:
    normalized = topic.lower().strip()
    if normalized == "travel":
        draw_travel_effects(draw)
    elif normalized in {"skills", "agents"}:
        draw_agent_effects(draw)


def draw_title_band(image, title: str):
    _Image, ImageDraw, _ImageEnhance, _ImageFilter, ImageFont = require_pillow()
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    band_top = int(height * 0.78)
    draw.rectangle((0, band_top, width, height), fill=(12, 92, 45, 238))
    draw.rectangle((0, band_top, width, band_top + 5), fill=(187, 236, 94, 230))
    max_width = width - 88
    max_height = height - band_top - 26
    font, lines, text_height = fit_title(draw, ImageFont, title, max_width, max_height)
    y = band_top + (height - band_top - text_height) // 2 - 5
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        line_width = box[2] - box[0]
        line_height = box[3] - box[1]
        x = (width - line_width) // 2
        draw.text((x + 4, y + 5), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font, fill=(248, 246, 232, 255))
        y += line_height + 6


def build_prompt(topic: str, title: str, source_path: Path | None) -> str:
    effects = DEFAULT_TOPIC_EFFECTS.get(topic, DEFAULT_TOPIC_EFFECTS["none"])
    source_note = f"Source image/frame: {source_path}" if source_path else "Source image/frame: extract the best lesson/meeting frame first."
    return "\n".join([
        "Edit the provided lesson/video frame into a 50% cartoon / 50% photo hybrid cover thumbnail.",
        "",
        "Use case: style-transfer",
        "Asset type: Human20 lesson/meeting cover thumbnail",
        f"Primary request: create a high-energy cover for the topic: {topic}",
        "Style/medium: hybrid photo-cartoon, high-end YouTube thumbnail look, visible ink-like contour lines, smooth painted shading, realistic photo depth, saturated neon or studio lighting, crisp graphic highlights.",
        "Composition/framing: preserve the original person identity, pose, room, important props, and webcam framing; make the face and gesture the main focal point.",
        f"Topic effects: {effects}",
        "Lighting/mood: punchy, readable at small size, poster-like contrast.",
        "Constraints: keep the same person recognizable; keep important signs readable; keep enough clean space for a lower-third title band.",
        "Avoid: full anime conversion, face distortion, hand distortion, extra people, clutter, watermark, logo, unreadable text.",
        f"Exact local overlay title after generation: {title}",
        source_note,
        "",
    ])


def generate_cover(args: argparse.Namespace) -> dict[str, str | int | bool]:
    Image, _ImageDraw, _ImageEnhance, _ImageFilter, _ImageFont = require_pillow()
    item_id = args.item_id or args.meeting_id
    if not item_id or not SAFE_ID.fullmatch(item_id):
        raise ValueError("--item-id must contain only lowercase letters, digits, and hyphens")
    if not args.video and not args.source_image:
        raise ValueError("provide --video or --source-image")

    site_root = Path(args.site_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else site_root / "public" / "files" / "thumbnails" / f"{item_id}.jpg"
    work_dir = Path(args.work_dir or DEFAULT_WORK_ROOT / item_id).expanduser().resolve()
    source_image = Path(args.source_image).expanduser().resolve() if args.source_image else work_dir / "source-frame.jpg"

    if args.video and not args.source_image:
        extract_frame(Path(args.video).expanduser().resolve(), source_image, args.timestamp, args.ffmpeg)
    if not source_image.exists():
        raise FileNotFoundError(f"source image not found: {source_image}")

    image = Image.open(source_image).convert("RGB")
    image = center_crop_16x9(image)
    image = stylize_base(image, enabled=not args.no_stylize)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    _Image, ImageDraw, _ImageEnhance, _ImageFilter, _ImageFont = require_pillow()
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw_topic_effects(draw, args.topic_effects)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw_title_band(image, args.title)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "JPEG", quality=args.quality, optimize=True, progressive=True)

    prompt_path = Path(args.prompt_output).expanduser().resolve() if args.prompt_output else work_dir / "imagegen-prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(build_prompt(args.topic_effects, args.title, source_image), encoding="utf-8")

    return {
        "ok": True,
        "itemId": item_id,
        "sourceImage": str(source_image),
        "output": str(output),
        "siteThumbnail": f"/files/thumbnails/{item_id}.jpg",
        "promptOutput": str(prompt_path),
        "width": image.width,
        "height": image.height,
        "quality": args.quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Human20 lesson/meeting cover thumbnail from a video frame or image.")
    parser.add_argument("--item-id", help="Safe item id; output defaults to public/files/thumbnails/<id>.jpg.")
    parser.add_argument("--meeting-id", help="Backward-compatible alias for --item-id.")
    parser.add_argument("--title", required=True, help="Exact title text for the lower-third band.")
    parser.add_argument("--video", help="Source video. Used to extract a frame when --source-image is absent.")
    parser.add_argument("--source-image", help="Existing frame/generated image to use as the cover base.")
    parser.add_argument("--timestamp", default="00:00:10", help="Video timestamp for frame extraction. Default: 00:00:10.")
    parser.add_argument("--topic-effects", default="skills", choices=sorted(DEFAULT_TOPIC_EFFECTS), help="Topic effect preset.")
    parser.add_argument("--site-root", default="frontend-v2", help="frontend-v2 root. Default: frontend-v2.")
    parser.add_argument("--output", help="Output JPEG path. Default: <site-root>/public/files/thumbnails/<id>.jpg.")
    parser.add_argument("--work-dir", help="Private working directory for extracted frame and prompt.")
    parser.add_argument("--prompt-output", help="Write the image-generation/edit prompt here. Default: <work-dir>/imagegen-prompt.txt.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable. Default: ffmpeg.")
    parser.add_argument("--quality", type=int, default=92, help="JPEG quality. Default: 92.")
    parser.add_argument("--no-stylize", action="store_true", help="Skip deterministic photo-cartoon styling and only crop/overlay.")
    args = parser.parse_args()
    try:
        print(json.dumps(generate_cover(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"generate cover failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main())

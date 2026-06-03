#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, random, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W = H = 1080
SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_DIR = SKILL_DIR / 'assets/hyperliquid_ru'
ASSET_DIR = Path(os.environ.get('HLRU_ASSET_DIR', DEFAULT_ASSET_DIR))
MANIFEST = ASSET_DIR / 'backgrounds/manifest.json'
LOGO = Path(os.environ.get('HLRU_LOGO_PATH', str(ASSET_DIR / 'hlru_logo.png')))
F_BOLD = os.environ.get('HLRU_FONT_BOLD', '/tmp/hyper_fonts/Unbounded-Black.ttf')
F_UI = os.environ.get('HLRU_FONT_UI', '/tmp/hyper_fonts/GolosText.ttf')
F_METRIC = os.environ.get('HLRU_FONT_METRIC', '/tmp/hyper_fonts/IBM-Plex-Sans-Bold.ttf')
GREEN = (0, 255, 178)
WHITE = (245, 255, 252)


def font(path: str, size: int):
    if path and Path(path).exists():
        return ImageFont.truetype(path, size)
    for candidate in ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def fit(draw: ImageDraw.ImageDraw, text: str, path: str, max_w: int, start: int, min_size: int = 18):
    size = start
    while size >= min_size:
        f = font(path, size)
        b = draw.textbbox((0, 0), text, font=f)
        if b[2] - b[0] <= max_w:
            return f
        size -= 1
    return font(path, min_size)


def rounded_gradient_rect(base, box, radius, left_color, right_color, outline=None, outline_width=0):
    x1, y1, x2, y2 = map(int, box)
    w, h = x2 - x1, y2 - y1
    grad = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for xx in range(w):
        t = xx / max(1, w - 1)
        c = tuple(int(left_color[i] * (1 - t) + right_color[i] * t) for i in range(4))
        gd.line((xx, 0, xx, h), fill=c)
    mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    base.paste(grad, (x1, y1), mask)
    if outline and outline_width:
        ImageDraw.Draw(base).rounded_rectangle((x1, y1, x2, y2), radius=radius, outline=outline, width=outline_width)


def load_background(bg_arg: str | None, seed: str | None):
    if not MANIFEST.exists():
        raise SystemExit('Missing HLRU background manifest. Powerpack does not bundle ready-made cover backgrounds; set HLRU_ASSET_DIR to a directory containing backgrounds/manifest.json.')
    manifest = json.loads(MANIFEST.read_text())
    bgs = manifest['backgrounds']
    for bg in bgs:
        path = Path(bg['path'])
        if not path.is_absolute():
            bg['path'] = str((MANIFEST.parent / path).resolve())
    if bg_arg and bg_arg != 'random':
        wanted = bg_arg.zfill(2) if bg_arg.isdigit() else bg_arg
        for bg in bgs:
            if bg['id'] == wanted or bg['name'] == wanted or Path(bg['path']).name == wanted:
                return bg
        raise SystemExit(f'Background not found: {bg_arg}')
    rng = random.Random(seed) if seed else random.SystemRandom()
    return rng.choice(bgs)


def parse_facts(raw: str):
    if not raw:
        return []
    out = []
    for item in raw.split('|'):
        item = item.strip()
        if not item:
            continue
        if '=' in item:
            left, right = item.split('=', 1)
        elif '—' in item:
            left, right = item.split('—', 1)
        else:
            left, right = item, ''
        out.append((left.strip(), right.strip()))
    return out[:4]


def main():
    p = argparse.ArgumentParser(description='Render @hyperliquid_ru_news cover with random prepared background')
    p.add_argument('--title', required=True, help='Title lines separated by |, e.g. Cabal|запускает|фонды|на HyperEVM')
    p.add_argument('--highlight', type=int, default=2, help='0-based title line index to render emerald')
    p.add_argument('--facts', default='', help='Rows separated by |, each left=right')
    p.add_argument('--background', default='random', help='random, id 01..20, or background name')
    p.add_argument('--seed', default=None, help='Optional deterministic random seed')
    p.add_argument('--out', default='/tmp/hlru_cover.png')
    args = p.parse_args()

    bg = load_background(args.background, args.seed)
    bg_path = Path(bg['path'])
    if not bg_path.exists():
        raise SystemExit(f'Missing background file: {bg_path}')

    img = Image.open(bg_path).convert('RGB').resize((W, H), Image.LANCZOS)
    veil = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    for x in range(0, 760):
        vd.line((x, 0, x, H), fill=(0, 0, 0, int(190 * (1 - x / 760))))
    for y in range(860, H):
        vd.line((0, y, W, y), fill=(0, 0, 0, int((y - 860) / 220 * 190)))
    img = Image.alpha_composite(img.convert('RGBA'), veil)
    d = ImageDraw.Draw(img)

    # full logo top-right, no generated-logo fakery
    if not LOGO.exists():
        raise SystemExit('Missing approved HLRU logo. Powerpack does not bundle private/channel logos; set HLRU_LOGO_PATH.')
    logo = Image.open(LOGO).convert('RGBA')
    logo.thumbnail((230, 210), Image.LANCZOS)
    img.paste(logo, (810, 54), logo)
    d = ImageDraw.Draw(img)

    pill_text = 'РУССКОЯЗЫЧНОЕ КОМЬЮНИТИ HYPERLIQUID'
    top_f = fit(d, pill_text, F_UI, 668, 31, 24)
    tb = d.textbbox((0, 0), pill_text, font=top_f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    x = 78
    brand = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(brand)
    px1, py1, px2, py2 = x - 20, 70, x + 690, 138
    bd.rounded_rectangle((px1 - 3, py1 - 3, px2 + 3, py2 + 3), radius=37, fill=(0, 255, 178, 35))
    brand = brand.filter(ImageFilter.GaussianBlur(5))
    rounded_gradient_rect(brand, (px1, py1, px2, py2), 34, (0, 50, 38, 242), (0, 156, 116, 242), outline=(108, 255, 208, 135), outline_width=2)
    img = Image.alpha_composite(img, brand)
    d = ImageDraw.Draw(img)
    d.text((px1 + (px2 - px1 - tw) / 2, py1 + (py2 - py1 - th) / 2 - 4), pill_text, font=top_f, fill=WHITE)

    lines = [s.strip() for s in args.title.split('|') if s.strip()]
    y = 220 if len(lines) >= 4 else 250
    max_w = 690
    for i, txt in enumerate(lines[:5]):
        start = 104 if i == 0 else (96 if i == args.highlight else 74)
        f = fit(d, txt, F_BOLD, max_w, start, 28)
        col = GREEN if i == args.highlight else WHITE
        d.text((78, y), txt, font=f, fill=col)
        b = d.textbbox((78, y), txt, font=f)
        y += max(76, b[3] - b[1] + 16)

    facts = parse_facts(args.facts)
    fy = max(680, min(720, y + 28))
    for val, label in facts:
        d.line((78, fy + 8, 638, fy + 8), fill=(0, 255, 178, 105), width=1)
        vf = fit(d, val, F_METRIC, 220, 36, 22)
        lf = fit(d, label, F_METRIC, 300, 25, 18)
        d.text((78, fy + 24), val, font=vf, fill=WHITE)
        if label:
            d.text((315, fy + 29), label, font=lf, fill=(222, 255, 242))
        fy += 72
        if fy > 875:
            break

    footer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(footer)
    fd.rectangle((0, 910, W, H), fill=(0, 0, 0, 238))
    img = Image.alpha_composite(img, footer)
    d = ImageDraw.Draw(img)
    cta = font(F_UI, 29)
    d.text((78, 934), 'Подписаться:', font=cta, fill=WHITE)
    w = d.textbbox((0, 0), 'Подписаться:', font=cta)[2]
    d.text((78 + w, 934), ' @hyperliquid_ru_news', font=cta, fill=GREEN)
    d.text((78, 980), 'Чат:', font=cta, fill=WHITE)
    w = d.textbbox((0, 0), 'Чат:', font=cta)[2]
    d.text((78 + w, 980), ' @hyperliquid_ru', font=cta, fill=GREEN)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert('RGB').save(out, quality=96)
    print(json.dumps({'ok': True, 'out': str(out), 'background_id': bg['id'], 'background_name': bg['name'], 'background_path': bg['path']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

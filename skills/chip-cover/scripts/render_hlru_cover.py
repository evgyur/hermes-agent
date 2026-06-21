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


def load_background(bg_arg: str | None, seed: str | None, manifest_path: Path):
    if bg_arg and Path(bg_arg).exists():
        return {'id': 'custom', 'name': Path(bg_arg).name, 'path': str(Path(bg_arg).resolve())}
    if bg_arg in {'gradient', 'fallback'} or not manifest_path.exists():
        return {'id': 'gradient', 'name': 'generated gradient fallback', 'path': ''}
    manifest = json.loads(manifest_path.read_text())
    bgs = manifest['backgrounds']
    for bg in bgs:
        path = Path(bg['path'])
        if not path.is_absolute():
            bg['path'] = str((manifest_path.parent / path).resolve())
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
    p = argparse.ArgumentParser(description='Render HLRU-style market/community cover with configurable assets and CTA')
    p.add_argument('--title', required=True, help='Title lines separated by |, e.g. Cabal|запускает|фонды|на HyperEVM')
    p.add_argument('--highlight', type=int, default=2, help='0-based title line index to render emerald')
    p.add_argument('--facts', default='', help='Rows separated by |, each left=right')
    p.add_argument('--background', default='auto', help='auto/random, gradient/fallback, id/name from manifest, or explicit PNG path')
    p.add_argument('--asset-dir', default=os.environ.get('HLRU_ASSET_DIR', str(DEFAULT_ASSET_DIR)), help='Optional brand asset directory')
    p.add_argument('--logo', default=os.environ.get('HLRU_LOGO_PATH', ''), help='Optional logo PNG path; falls back to text brand label')
    p.add_argument('--brand-label', default='РУССКОЯЗЫЧНОЕ КОМЬЮНИТИ HYPERLIQUID')
    p.add_argument('--cta', default='Подписаться: @hyperliquid_ru_news')
    p.add_argument('--secondary-cta', default='Чат: @hyperliquid_ru')
    p.add_argument('--seed', default=None, help='Optional deterministic random seed')
    p.add_argument('--out', default='/tmp/hlru_cover.png')
    args = p.parse_args()

    asset_dir = Path(args.asset_dir)
    manifest_path = asset_dir / 'backgrounds/manifest.json'
    bg_arg = 'random' if args.background == 'auto' else args.background
    bg = load_background(bg_arg, args.seed, manifest_path)
    bg_path = Path(bg['path']) if bg.get('path') else None
    if bg_path and bg_path.exists():
        img = Image.open(bg_path).convert('RGB').resize((W, H), Image.LANCZOS)
    else:
        img = Image.new('RGB', (W, H), '#020806')
        gd = ImageDraw.Draw(img)
        for y0 in range(H):
            t = y0 / H
            gd.line((0, y0, W, y0), fill=(2, 8 + int(18*t), 6 + int(28*t)))
        glow = Image.new('RGBA', (W, H), (0,0,0,0))
        g = ImageDraw.Draw(glow)
        g.ellipse((-220, 80, 620, 840), fill=(0,255,178,58))
        g.ellipse((580, -160, 1280, 580), fill=(0,110,86,72))
        g.ellipse((400, 620, 1280, 1300), fill=(12,255,187,36))
        img = Image.alpha_composite(img.convert('RGBA'), glow.filter(ImageFilter.GaussianBlur(82))).convert('RGB')
    veil = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    for x in range(0, 760):
        vd.line((x, 0, x, H), fill=(0, 0, 0, int(190 * (1 - x / 760))))
    for y in range(860, H):
        vd.line((0, y, W, y), fill=(0, 0, 0, int((y - 860) / 220 * 190)))
    img = Image.alpha_composite(img.convert('RGBA'), veil)
    d = ImageDraw.Draw(img)

    # full logo top-right when supplied; text fallback keeps the template usable after install
    logo_path = Path(args.logo) if args.logo else asset_dir / 'hlru_logo.png'
    if logo_path.exists():
        logo = Image.open(logo_path).convert('RGBA')
        logo.thumbnail((230, 210), Image.LANCZOS)
        img.paste(logo, (810, 54), logo)
    else:
        fallback = fit(d, 'HLRU', F_BOLD, 190, 64, 34)
        d.text((845, 74), 'HLRU', font=fallback, fill=GREEN)
    d = ImageDraw.Draw(img)

    pill_text = args.brand_label
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
    cta1 = args.cta
    cta2 = args.secondary_cta
    cta1_font = fit(d, cta1, F_UI, 900, 29, 20)
    cta2_font = fit(d, cta2, F_UI, 900, 29, 20)
    d.text((78, 934), cta1, font=cta1_font, fill=GREEN if '@' in cta1 else WHITE)
    d.text((78, 980), cta2, font=cta2_font, fill=GREEN if '@' in cta2 or '.' in cta2 else WHITE)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert('RGB').save(out, quality=96)
    print(json.dumps({'ok': True, 'out': str(out), 'background_id': bg['id'], 'background_name': bg['name'], 'background_path': bg.get('path', '')}, ensure_ascii=False))


if __name__ == '__main__':
    main()

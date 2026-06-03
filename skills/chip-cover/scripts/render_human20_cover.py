#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W = H = 1080
import os
BRAND = Path(os.environ.get('HUMAN20_BRAND_DIR', ''))
LOGO = BRAND / 'logos/png/h20-lockup-light-720.png'
MARK = BRAND / 'logos/png/h20-mark-512.png'
FONTS = BRAND / 'fonts'


def font(path: Path, size: int):
    return ImageFont.truetype(str(path), size)


def fallback_font(name: str, size: int):
    p = Path('/usr/share/fonts/truetype/dejavu') / name
    return ImageFont.truetype(str(p), size)


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt) -> int:
    bb = draw.textbbox((0, 0), text, font=fnt)
    return bb[2] - bb[0]


def fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: Path, start_size: int, max_width: int, min_size: int = 24):
    size = start_size
    while size > min_size:
        f = font(font_path, size)
        if text_width(draw, text, f) <= max_width:
            return f
        size -= 2
    return font(font_path, min_size)


def render(args: argparse.Namespace) -> Path:
    geologica_path = FONTS / 'Geologica[CRSV,SHRP,slnt,wght].ttf'
    onest_path = FONTS / 'Onest[wght].ttf'
    Geo = lambda s: font(geologica_path, s) if geologica_path.exists() else fallback_font('DejaVuSans-Bold.ttf', s)
    Onest = lambda s: font(onest_path, s) if onest_path.exists() else fallback_font('DejaVuSans.ttf', s)
    Mono = lambda s: fallback_font('DejaVuSansMono.ttf', s)
    MonoB = lambda s: fallback_font('DejaVuSansMono-Bold.ttf', s)

    img = Image.new('RGBA', (W, H), '#070B16')
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line((0, y, W, y), fill=(7 + int(8 * t), 11 + int(9 * t), 22 + int(24 * t), 255))

    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.ellipse((-260, -160, 550, 520), fill=(99, 102, 241, 105))
    od.ellipse((610, 150, 1240, 880), fill=(46, 68, 245, 58))
    od.ellipse((520, 760, 1300, 1320), fill=(201, 162, 63, 42))
    img = Image.alpha_composite(img, ov.filter(ImageFilter.GaussianBlur(76)))
    d = ImageDraw.Draw(img)

    if LOGO.exists():
        logo = Image.open(LOGO).convert('RGBA')
        logo.thumbnail((430, 108), Image.LANCZOS)
        img.alpha_composite(logo, (58, 48))
    else:
        d.text((58, 58), 'Человек 2.0', font=Geo(40), fill='#FFFFFF')
        d.text((58, 104), 'СРЕДА ВНЕДРЕНИЯ ИИ', font=Onest(18), fill='#D9DFF2')

    badge_box = (720, 60, 1018, 118)
    d.rounded_rectangle(badge_box, radius=29, fill=(17, 24, 39, 220), outline='#334155', width=2)
    badge_font = fit_font(d, args.badge, onest_path if onest_path.exists() else Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), 27, 250, 18)
    badge_bb = d.textbbox((0, 0), args.badge, font=badge_font)
    badge_w = badge_bb[2] - badge_bb[0]
    badge_h = badge_bb[3] - badge_bb[1]
    badge_x = badge_box[0] + ((badge_box[2] - badge_box[0]) - badge_w) // 2
    badge_y = badge_box[1] + ((badge_box[3] - badge_box[1]) - badge_h) // 2 - badge_bb[1] + 2
    d.text((badge_x, badge_y), args.badge, font=badge_font, fill='#F8FAFC')

    y = 174
    headline_bottom = y
    headline_lines = args.headline.split('\n')[:3]
    for line in headline_lines:
        title_font = fit_font(d, line, geologica_path if geologica_path.exists() else Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'), 72, 960, 42)
        d.text((58, y), line, font=title_font, fill='#FFFFFF')
        headline_bottom = max(headline_bottom, d.textbbox((58, y), line, font=title_font)[3])
        y += int(title_font.size * 1.22)

    subtitle_bottom = headline_bottom
    if args.subtitle:
        subtitle_font = fit_font(d, args.subtitle, onest_path if onest_path.exists() else Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), 30, 960, 20)
        subtitle_y = max(342, headline_bottom + 10)
        d.text((58, subtitle_y), args.subtitle, font=subtitle_font, fill='#CBD5E1')
        subtitle_bottom = d.textbbox((58, subtitle_y), args.subtitle, font=subtitle_font)[3]

    # Keep the product card below the headline/subtitle. 3-line headlines with
    # mixed Latin/Russian glyph metrics can otherwise visually collide with the
    # white card even when the old fixed coordinates technically do not overlap.
    card_top = max(420, subtitle_bottom + 34)
    card_top = min(card_top, 452)
    card_box = (58, card_top, 1022, card_top + 380)
    d.rounded_rectangle(card_box, radius=36, fill='#F8FAFC', outline='#7B8FFF', width=4)
    if MARK.exists():
        mark = Image.open(MARK).convert('RGBA')
        mark.thumbnail((86, 54), Image.LANCZOS)
        img.alpha_composite(mark, (88, card_top + 32))
        title_x = 190
    else:
        title_x = 88

    card_font = fit_font(d, args.card_title, geologica_path if geologica_path.exists() else Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'), 38, 790, 24)
    d.text((title_x, card_top + 32), args.card_title, font=card_font, fill='#0F172A')

    chip_box = (88, card_top + 105, 992, card_top + 162)
    d.rounded_rectangle(chip_box, radius=20, fill='#EEF2FF')
    chip_x = 118
    colors = ['#8A5A00', '#4F46E5', '#0F172A', '#64748B']
    for i, chip in enumerate([c.strip() for c in args.chips.split('|') if c.strip()][:4]):
        f = Onest(24)
        d.text((chip_x, card_top + 119), chip, font=f, fill=colors[i % len(colors)])
        chip_x += text_width(d, chip, f) + 58

    code_box = (88, card_top + 195, 992, card_top + 340)
    d.rounded_rectangle(code_box, radius=20, fill='#0B1220')
    tone_color = {'error': '#FB7185', 'success': '#34D399', 'neutral': '#E2E8F0', 'muted': '#94A3B8'}
    code_lines = args.code[:4]
    if code_lines:
        inner_x = code_box[0] + 30
        max_text_w = code_box[2] - code_box[0] - 60
        n = len(code_lines)
        line_step = 38 if n <= 3 else 29
        first_size = 27 if n <= 3 else 22
        rest_size = 23 if n <= 3 else 19
        block_h = (n - 1) * line_step + first_size
        y = code_box[1] + ((code_box[3] - code_box[1]) - block_h) // 2 - 2
        for i, raw in enumerate(code_lines):
            if '|' in raw:
                text, tone = raw.rsplit('|', 1)
            else:
                text, tone = raw, 'neutral'
            f = MonoB(first_size) if i == 0 else Mono(rest_size)
            while text_width(d, text, f) > max_text_w and getattr(f, 'size', 18) > 16:
                next_size = getattr(f, 'size', 18) - 1
                f = MonoB(next_size) if i == 0 else Mono(next_size)
            d.text((inner_x, y), text, font=f, fill=tone_color.get(tone, '#E2E8F0'))
            y += line_step

    d.rounded_rectangle((58, 858, 1022, 960), radius=34, fill='#6366F1')
    cta = 'Подписаться: @human20'
    cta_font = Geo(46)
    d.text(((W - text_width(d, cta, cta_font)) // 2, 882), cta, font=cta_font, fill='#FFFFFF')

    d.text((58, 1002), 'Человек 2.0 · Среда внедрения ИИ', font=Onest(28), fill='#D9DFF2')
    d.text((783, 1002), 'human20.app', font=Onest(28), fill='#D9DFF2')

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert('RGB').save(out, quality=96)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description='Render Human20 TG cover in approved dark GitHub/code-card format')
    p.add_argument('--headline', required=True, help='Headline, use literal newlines or $ quoting')
    p.add_argument('--badge', default='HUMAN20')
    p.add_argument('--subtitle', default='')
    p.add_argument('--card-title', required=True)
    p.add_argument('--chips', default='Human20|AI|Практика')
    p.add_argument('--code', action='append', default=[], help='Line text optionally suffixed with |error|success|neutral|muted')
    p.add_argument('--output', default='/tmp/tg_human20_cover.png')
    args = p.parse_args()
    out = render(args)
    print(out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

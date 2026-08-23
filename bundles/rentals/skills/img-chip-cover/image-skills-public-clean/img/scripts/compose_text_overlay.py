#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def font(path: str|None, size: int):
    candidates=[path, '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for c in candidates:
        if c and Path(c).exists(): return ImageFont.truetype(c, size)
    return ImageFont.load_default()

def main():
    ap=argparse.ArgumentParser(description='Render exact text overlays from JSON spec')
    ap.add_argument('--input', required=True)
    ap.add_argument('--spec', required=True, help='JSON: {items:[{text,box:[x1,y1,x2,y2],size,color,font}]}')
    ap.add_argument('--output', required=True)
    args=ap.parse_args()
    im=Image.open(args.input).convert('RGBA')
    draw=ImageDraw.Draw(im)
    spec=json.loads(Path(args.spec).read_text())
    for item in spec.get('items', []):
        x1,y1,x2,y2=item.get('box',[40,40,im.width-40,im.height-40])
        f=font(item.get('font'), int(item.get('size',48)))
        color=item.get('color','#ffffff')
        text=item.get('text','')
        max_chars=max(8, int((x2-x1)/(item.get('size',48)*0.55)))
        wrapped='\n'.join(textwrap.wrap(text, width=max_chars))
        draw.multiline_text((x1,y1), wrapped, font=f, fill=color, spacing=item.get('spacing',8))
    out=Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    print(out)
if __name__=='__main__': main()

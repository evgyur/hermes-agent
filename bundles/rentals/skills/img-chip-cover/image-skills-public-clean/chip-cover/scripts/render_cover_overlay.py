#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def load_font(path, size):
    for candidate in [path, '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if candidate and Path(candidate).exists(): return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()

def fit(im, box):
    x1,y1,x2,y2=box; bw,bh=x2-x1,y2-y1; s=min(bw/im.width,bh/im.height)
    return im.resize((max(1,round(im.width*s)), max(1,round(im.height*s))), Image.Resampling.LANCZOS)

def main():
    ap=argparse.ArgumentParser(description='Render deterministic cover overlay over background from JSON spec')
    ap.add_argument('--spec', required=True); ap.add_argument('--out', required=True)
    args=ap.parse_args(); spec=json.loads(Path(args.spec).read_text())
    canvas=spec.get('canvas', {'width':1080,'height':1080}); w,h=int(canvas.get('width',1080)), int(canvas.get('height',1080))
    bg=spec.get('background',{}).get('path')
    im=Image.open(bg).convert('RGBA').resize((w,h)) if bg and Path(bg).exists() else Image.new('RGBA',(w,h),(14,18,28,255))
    if spec.get('logo',{}).get('path') and Path(spec['logo']['path']).exists():
        box=spec['logo'].get('box',[64,64,300,128]); logo=fit(Image.open(spec['logo']['path']).convert('RGBA'), box)
        im.alpha_composite(logo,(box[0]+(box[2]-box[0]-logo.width)//2, box[1]+(box[3]-box[1]-logo.height)//2))
    draw=ImageDraw.Draw(im)
    for key, default_size, default_box in [('headline',76,[72,170,1000,560]), ('badge',34,[72,72,520,130]), ('cta',34,[72,940,1000,1016])]:
        item=spec.get(key,{})
        if not item.get('text'): continue
        box=item.get('box', default_box); size=int(item.get('size', default_size)); f=load_font(item.get('font'), size)
        max_chars=max(8, int((box[2]-box[0])/(size*0.55)))
        text='\n'.join(textwrap.wrap(item['text'], width=max_chars))
        draw.multiline_text((box[0],box[1]), text, font=f, fill=item.get('color','#ffffff'), spacing=item.get('spacing',8))
    out=Path(args.out); out.parent.mkdir(parents=True, exist_ok=True); im.convert('RGB').save(out); print(out)
if __name__=='__main__': main()

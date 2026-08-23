#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from io import BytesIO
import requests, qrcode
from PIL import Image

def box(v):
    p=[int(x.strip()) for x in v.split(',')]
    if len(p)!=4 or p[2]<=p[0] or p[3]<=p[1]: raise argparse.ArgumentTypeError('box must be x1,y1,x2,y2')
    return p

def load(path_or_url):
    if path_or_url.startswith(('http://','https://')):
        r=requests.get(path_or_url, timeout=30); r.raise_for_status(); return Image.open(BytesIO(r.content)).convert('RGBA')
    return Image.open(path_or_url).convert('RGBA')

def contain(im, b):
    x1,y1,x2,y2=b; bw,bh=x2-x1,y2-y1
    s=min(bw/im.width,bh/im.height)
    return im.resize((max(1,round(im.width*s)), max(1,round(im.height*s))), Image.Resampling.LANCZOS)

def main():
    ap=argparse.ArgumentParser(description='Place an official logo and local QR code into reserved boxes')
    ap.add_argument('--input', required=True); ap.add_argument('--output', required=True)
    ap.add_argument('--logo', required=True, help='local file or URL')
    ap.add_argument('--qr-url', required=True)
    ap.add_argument('--logo-box', type=box, required=True); ap.add_argument('--qr-box', type=box, required=True)
    args=ap.parse_args()
    base=Image.open(args.input).convert('RGBA')
    logo=contain(load(args.logo), args.logo_box)
    x1,y1,x2,y2=args.logo_box; base.alpha_composite(logo,(x1+(x2-x1-logo.width)//2,y1+(y2-y1-logo.height)//2))
    q=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=20, border=4); q.add_data(args.qr_url); q.make(fit=True)
    qr=q.make_image(fill_color=(20,20,20), back_color='white').convert('RGBA')
    qx1,qy1,qx2,qy2=args.qr_box; size=min(qx2-qx1,qy2-qy1)
    qr=qr.resize((size,size), Image.Resampling.NEAREST); base.alpha_composite(qr,(qx1+(qx2-qx1-size)//2,qy1+(qy2-qy1-size)//2))
    out=Path(args.output); out.parent.mkdir(parents=True, exist_ok=True); base.save(out); print(out)
if __name__=='__main__': main()

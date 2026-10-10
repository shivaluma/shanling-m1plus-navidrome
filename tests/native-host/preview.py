#!/usr/bin/env python3
"""Approximate device render of native-harness widget trees (development aid).

Run the native harness with ND_PREVIEW_DIR set, then render each exported tree
with the user's own extracted V2.1 theme, images and a (merged) UI font:

    ND_PREVIEW_DIR=/tmp/nd python3 tests/native-host/run.py
    python3 tests/native-host/preview.py /tmp/nd --font dist/rootfs/.../default.ttf

The output is a labelled approximation, not a device screenshot: it reuses
stock colours, icons and the stb-style baseline, but not AWTK's renderer.
Requires Pillow. Outputs contain proprietary firmware images; keep them local.
"""
import argparse
import json
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / '.work/analysis-v2.1/rootfs/release/assets/default/raw'
STOCK_FONT = ASSETS / 'fonts/default.ttf'


def parse_theme(path):
    data = path.read_bytes()
    magic, _, count = struct.unpack_from('<III', data, 0)
    assert magic == 0xFAFBFCFD
    styles = {}
    for i in range(count):
        base = 12 + i * 100
        offset, = struct.unpack_from('<I', data, base)
        state, name, kind = (data[base + 4 + 32 * j:base + 36 + 32 * j].split(b'\0')[0].decode() for j in range(3))
        n, = struct.unpack_from('<I', data, offset)
        p, props = offset + 4, {}
        for _ in range(n):
            vtype, name_len, value_len = struct.unpack_from('<BBH', data, p)
            p += 4
            key = data[p:p + name_len - 1].decode()
            p += name_len
            raw = data[p:p + value_len]
            p += value_len
            if vtype in (6, 7) and value_len == 4:
                props[key] = '#%02X%02X%02X%02X' % tuple(raw) if 'color' in key else struct.unpack('<i', raw)[0]
            else:
                props[key] = raw.rstrip(b'\0').decode('utf-8', 'replace')
        styles[(kind, name, state)] = props
    return styles


def color(value):
    if not isinstance(value, str) or not value.startswith('#'):
        return None
    value = value[1:]
    if len(value) == 6:
        value += 'FF'
    r, g, b, a = (int(value[i:i + 2], 16) for i in range(0, 8, 2))
    return None if a == 0 else (r, g, b, a)


class Renderer:
    def __init__(self, font_path):
        self.theme = parse_theme(ASSETS / 'styles/default.bin')
        self.theme.update(parse_theme(ASSETS / 'styles/home_page.bin'))
        self.font_path = str(font_path)
        self.fonts, self.images = {}, {}

    def font(self, size):
        # AWTK/stb scale pixel height to ascent - descent (299 of 256 units).
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, max(1, round(size * 256 / 299)))
        return self.fonts[size]

    def image(self, name):
        if name not in self.images:
            path = ASSETS / 'images/xx' / (name + '.png')
            self.images[name] = Image.open(path).convert('RGBA') if path.exists() else None
        return self.images[name]

    def style(self, node):
        kind = node['kind']
        name = node['props'].get('style', 'default')
        props = dict(self.theme.get((kind, 'default', 'normal'), {}))
        props.update(self.theme.get((kind, name, 'normal'), {}))
        for key, value in node['props'].items():
            if key.startswith('style:normal:'):
                props[key[13:]] = value
        return props, name

    def paste(self, canvas, img, box, mode, dx=0):
        x, y, w, h = box
        x += dx
        if mode in ('scale', 'scale_auto') and img.width and img.height:
            ratio = min(w / img.width, h / img.height) if mode == 'scale_auto' else None
            size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio))) if ratio else (w, h)
            img = img.resize(size, Image.LANCZOS)
        canvas.alpha_composite(img, (int(x + (w - img.width) / 2), int(y + (h - img.height) / 2)))

    def text(self, draw, node, props, name, box):
        text = node['text']
        if not text and node['kind'] == 'edit':
            text, props = node['props'].get('tips', ''), dict(props, text_color=props.get('tips_text_color'))
        if not text:
            return
        x, y, w, h = box
        size = int(props.get('font_size', 18) or 18)
        font = self.font(size)
        fill = color(props.get('text_color')) or (0x44, 0x44, 0x44, 255)
        align = props.get('text_align_h')
        left = align == 2 if isinstance(align, int) and align else node['kind'] in ('hscroll_label', 'edit')
        if node['kind'] == 'hscroll_label' or node['props'].get('ellipses') == 'true':
            while text and font.getlength(text) > w - 2:
                text = text[:-2] + '…' if len(text) > 1 else ''
        width = font.getlength(text)
        tx = x + (int(props.get('margin_left', 2) or 2) if left else (w - width) / 2)
        top = y + (h - size) // 2
        baseline = top + round(230 * size / 299)
        draw.text((tx, baseline), text, font=font, fill=fill, anchor='ls')

    def node(self, canvas, node, ox, oy):
        if not node['visible']:
            return
        x, y, w, h = node['rect']
        x, y = ox + x, oy + y
        props, name = self.style(node)
        layer = Image.new('RGBA', canvas.size)
        draw = ImageDraw.Draw(layer)
        bg = color(props.get('bg_color'))
        if bg and node['kind'] not in ('window',):
            radius = int(props.get('round_radius', 0) or 0)
            bottom = h - (int(props.get('border_width', 0) or 0) if node['kind'] == 'list_item' else 0)
            draw.rounded_rectangle((x, y, x + w - 1, y + bottom - 1), radius=radius, fill=bg)
        canvas.alpha_composite(layer)
        if props.get('bg_image') and self.image(props['bg_image']):
            mode = node['props'].get('style:normal:bg_image_draw_type', 'center')
            self.paste(canvas, self.image(props['bg_image']), (x, y, w, h), mode)
        if props.get('icon') and self.image(props['icon']):
            # icon_at=1 (left) centres the icon in a square at the left edge.
            square = (x, y, h, h) if props.get('icon_at') == 1 else (x, y, w, h)
            self.paste(canvas, self.image(props['icon']), square, 'center', int(props.get('x_offset', 0) or 0))
        if node['kind'] == 'image' and self.image(node['props'].get('image', '')):
            self.paste(canvas, self.image(node['props']['image']), (x, y, w, h), node['props'].get('draw_type', 'default'))
        self.text(ImageDraw.Draw(canvas), node, props, name, (x, y, w, h))
        stack = 0
        for child in node['children']:
            if node['kind'] == 'scroll_view':
                self.node(canvas, child, x - child['rect'][0], y + stack - child['rect'][1])
                stack += child['rect'][3]
            else:
                self.node(canvas, child, x, y)

    def status_bar(self, canvas):
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 319, 29), fill=(0, 0, 0, 255))
        self.paste(canvas, self.image('bar_play'), (14, 0, 14, 30), 'center')
        self.paste(canvas, self.image('bar_vol3'), (32, 6, 24, 24), 'center')
        draw.text((58, 22), '40', font=self.font(20), fill='white', anchor='ls')
        draw.text((160, 22), '09:41', font=self.font(20), fill='white', anchor='ms')
        self.paste(canvas, self.image('bar_power'), (296, 5, 14, 20), 'scale_auto')

    def render(self, tree, home=False):
        canvas = Image.new('RGBA', (320, 480), (0, 0, 0, 255))
        self.status_bar(canvas)
        self.node(canvas, tree, 0, 30)
        if home:
            draw = ImageDraw.Draw(canvas)
            for i in range(3):
                draw.ellipse((148 + i * 10, 397, 152 + i * 10, 401), fill='#FFFFFF' if i == 2 else '#666666')
            draw.rectangle((0, 414, 319, 479), fill='#131313')
            self.paste(canvas, self.image('defaultcover'), (0, 414, 66, 66), 'default')
            draw.text((76, 438), 'Life is short,Play more.', font=self.font(20), fill='white', anchor='ls')
            draw.text((76, 462), 'Shanling', font=self.font(16), fill='white', anchor='ls')
        return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('trees', type=Path, help='ND_PREVIEW_DIR used for the harness run')
    parser.add_argument('--font', type=Path, default=STOCK_FONT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    renderer = Renderer(args.font)
    names = ['home', 'artists', 'album', 'search']
    shots = [renderer.render(json.loads((args.trees / (n + '.json')).read_text()), n == 'home') for n in names]
    sheet = Image.new('RGBA', (len(shots) * 340 - 20, 506), (40, 40, 40, 255))
    draw = ImageDraw.Draw(sheet)
    for i, shot in enumerate(shots):
        sheet.paste(shot, (i * 340, 26))
        draw.text((i * 340, 18), names[i] + ' — approximate render, not a device screenshot',
                  font=renderer.font(12), fill='#FFCC77', anchor='ls')
    output = args.output or args.trees / 'preview.png'
    sheet.convert('RGB').save(output)
    print(output)


if __name__ == '__main__':
    main()

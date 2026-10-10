#!/usr/bin/env python3
"""Merge Be Vietnam Pro Latin/Vietnamese glyphs into the stock V2.1 UI font.

The stock `default.ttf` (Microsoft YaHei + Arial, 256 units/em) has no
Vietnamese letters such as ơ, ư or ạ. The firmware renders it with
stb_truetype, which performs no shaping. This tool therefore:

* replaces every code point Be Vietnam Pro covers (Latin, Latin Extended,
  Vietnamese, punctuation) with decomposed, scaled Be Vietnam Pro outlines;
* keeps stock CJK, Cyrillic, Greek, Thai and other glyphs unchanged;
* keeps the stock vertical metrics, so existing stock layouts keep their baseline;
* gives combining marks a negative offset so decomposed text sits over its base;
* drops tables stb_truetype never reads (glyph names, OpenType layout, DSIG).

The result is built locally from the user's own firmware and is never published.
"""
import argparse
import hashlib
from pathlib import Path
import sys

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / '.work/analysis-v2.1/rootfs/release/assets/default/raw/fonts/default.ttf'
STOCK_SHA256 = '9a16af0aead992a2ad64f86d0de8d38bea60d6f605d42ffd0501684666161872'
WEIGHTS = {
    'regular': ('vendor/fonts/BeVietnamPro-Regular.ttf',
                'cd1ef6e9d7db28ad5cdb88a65ccbe693870e60d340b791f349d248342b4fe4c3'),
    'medium': ('vendor/fonts/BeVietnamPro-Medium.ttf',
               'b60832bfa0fcd015158112c64d7e3fdad3b0c6287d1823f85a3103636e845268'),
}
# Letters that must render after merging; checked against the output cmap.
VIETNAMESE = ('aàảãáạăằẳẵắặâầẩẫấậbcdđeèẻẽéẹêềểễếệghiìỉĩíịklmnoòỏõóọôồổỗốộơờởỡớợ'
              'pqrstuùủũúụưừửữứựvxyỳỷỹýỵ')
UNUSED_TABLES = ('GPOS', 'GSUB', 'GDEF', 'DSIG', 'kern')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def outline(glyph_set, name, scale, dx):
    pen = TTGlyphPen(None)
    glyph_set[name].draw(TransformPen(pen, (scale, 0, 0, scale, dx, 0)))
    return pen.glyph()


def merge(stock_path, latin_path, output):
    # Keep the stock head timestamp so identical inputs give identical bytes.
    stock, latin = TTFont(stock_path, recalcTimestamp=False), TTFont(latin_path)
    upem = stock['head'].unitsPerEm
    scale = upem / latin['head'].unitsPerEm
    latin_cmap, latin_glyphs = latin.getBestCmap(), latin.getGlyphSet()
    stock_cmap = stock.getBestCmap()
    order = list(stock.getGlyphOrder())
    users = {}
    for code, name in stock_cmap.items():
        users.setdefault(name, set()).add(code)
    # Without shaping, a zero-width mark must already sit over the preceding glyph.
    reference = latin_glyphs[latin_cmap[ord('o')]].width / 2
    glyf, hmtx = stock['glyf'], stock['hmtx']
    remap = {}
    for code in sorted(latin_cmap):
        source = latin_cmap[code]
        dx = 0
        if 0x300 <= code <= 0x36f and latin_glyphs[source].width == 0:
            bounds = BoundsPen(latin_glyphs)
            latin_glyphs[source].draw(bounds)
            if bounds.bounds:
                dx = -(reference + (bounds.bounds[0] + bounds.bounds[2]) / 2) * scale
        glyph = outline(latin_glyphs, source, scale, dx)
        target = stock_cmap.get(code)
        # Replace in place only when no unrelated code point shares the stock glyph.
        if target is None or not users[target] <= set(latin_cmap):
            target = 'bvp%04X' % code
            if target not in glyf:
                order.append(target)
        glyf[target] = glyph
        glyph.recalcBounds(glyf)
        hmtx[target] = (round(latin_glyphs[source].width * scale), getattr(glyph, 'xMin', 0))
        remap[code] = target
    stock.setGlyphOrder(order)
    glyf.glyphOrder = order
    for table in stock['cmap'].tables:
        if table.isUnicode():
            for code, name in remap.items():
                if code <= 0xffff or table.format in (12, 13):
                    table.cmap[code] = name
    for tag in UNUSED_TABLES:
        if tag in stock:
            del stock[tag]
    stock['post'].formatType = 3.0
    stock['maxp'].numGlyphs = len(order)
    stock.save(output)
    merged = TTFont(output).getBestCmap()
    missing = [c for c in VIETNAMESE + VIETNAMESE.upper() if ord(c) not in merged]
    if missing:
        raise SystemExit('Merged font is missing: ' + ''.join(missing))
    return len(remap), len(order)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--stock', type=Path, default=STOCK)
    parser.add_argument('--weight', choices=sorted(WEIGHTS), default='regular')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    latin, digest = WEIGHTS[args.weight]
    if sha256(args.stock) != STOCK_SHA256:
        raise SystemExit('Stock font does not match firmware V2.1: ' + str(args.stock))
    if sha256(ROOT / latin) != digest:
        raise SystemExit('Pinned font mismatch: ' + latin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    replaced, glyphs = merge(args.stock, ROOT / latin, args.output)
    print(f'Merged {replaced} Be Vietnam Pro {args.weight} code points; '
          f'{glyphs} glyphs, {args.output.stat().st_size} bytes -> {args.output}')


if __name__ == '__main__':
    sys.exit(main())

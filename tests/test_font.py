"""Merge logic of scripts/build_font.py against a synthetic stand-in stock font."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_font', ROOT / 'scripts/build_font.py')
build_font = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_font)
LATIN = ROOT / build_font.WEIGHTS['medium'][0]


def box(width, height):
    pen = TTGlyphPen(None)
    pen.moveTo((10, 0)); pen.lineTo((10, height)); pen.lineTo((width - 10, height)); pen.lineTo((width - 10, 0))
    pen.closePath()
    return pen.glyph()


def stand_in(path):
    """256 units/em like the stock font, with a CJK glyph and a shared glyph."""
    names = ['.notdef', 'A', 'uni5B87', 'periodcentered', 'uni0416']
    fb = FontBuilder(256, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({0x41: 'A', 0x5B87: 'uni5B87', 0xB7: 'periodcentered',
                          0x30FB: 'periodcentered', 0x416: 'uni0416'})
    fb.setupGlyf({name: box(200, 180) for name in names})
    fb.setupHorizontalMetrics({name: (220, 10) for name in names})
    fb.setupHorizontalHeader(ascent=230, descent=-69)
    fb.setupOS2(sTypoAscender=230, sTypoDescender=-69, usWinAscent=230, usWinDescent=69)
    fb.setupNameTable({'familyName': 'Stand-in', 'styleName': 'Regular'})
    fb.setupPost()
    fb.save(str(path))


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stock = Path(self.temp.name) / 'stock.ttf'
        self.output = Path(self.temp.name) / 'merged.ttf'
        stand_in(self.stock)
        build_font.merge(self.stock, LATIN, self.output)
        self.font = TTFont(self.output)
        self.cmap = self.font.getBestCmap()

    def test_pinned_weights_match(self):
        for path, digest in build_font.WEIGHTS.values():
            self.assertEqual(build_font.sha256(ROOT / path), digest)

    def test_vietnamese_added_and_other_scripts_kept(self):
        for letter in build_font.VIETNAMESE + build_font.VIETNAMESE.upper():
            self.assertIn(ord(letter), self.cmap, letter)
        stock = TTFont(self.stock)
        for code in (0x5B87, 0x416):
            self.assertEqual(self.font.getGlyphID(self.cmap[code]), stock.getGlyphID(stock.getBestCmap()[code]))
        self.assertEqual(self.font['glyf'][self.cmap[0x5B87]].coordinates, stock['glyf']['uni5B87'].coordinates)

    def test_latin_replaced_and_stock_metrics_kept(self):
        self.assertEqual(self.font.getGlyphID(self.cmap[0x41]), 1)
        self.assertNotEqual(self.font['hmtx'][self.cmap[0x41]][0], 220)
        self.assertEqual(self.font['head'].unitsPerEm, 256)
        self.assertEqual((self.font['hhea'].ascent, self.font['hhea'].descent), (230, -69))
        bounds = BoundsPen(self.font.getGlyphSet())
        self.font.getGlyphSet()[self.cmap[ord('H')]].draw(bounds)
        self.assertTrue(170 <= bounds.bounds[3] <= 200, bounds.bounds)

    def test_shared_stock_glyph_not_overwritten(self):
        # Names are synthesized after post format 3, so compare glyph IDs.
        # U+30FB shares the stock glyph with U+00B7 but is absent from Be Vietnam Pro.
        gid = self.font.getGlyphID
        stock = TTFont(self.stock)
        self.assertEqual(gid(self.cmap[0x30FB]), stock.getGlyphID('periodcentered'))
        self.assertNotEqual(gid(self.cmap[0xB7]), gid(self.cmap[0x30FB]))
        self.assertEqual(self.font['glyf'][self.cmap[0x30FB]].coordinates, stock['glyf']['periodcentered'].coordinates)

    def test_marks_sit_over_previous_glyph(self):
        bounds = BoundsPen(self.font.getGlyphSet())
        self.font.getGlyphSet()[self.cmap[0x301]].draw(bounds)
        self.assertEqual(self.font['hmtx'][self.cmap[0x301]][0], 0)
        self.assertLess(bounds.bounds[2], 0)

    def test_unused_tables_dropped(self):
        self.assertEqual(self.font['post'].formatType, 3.0)
        for tag in build_font.UNUSED_TABLES:
            self.assertNotIn(tag, self.font)
        self.assertEqual(self.font['maxp'].numGlyphs, len(self.font.getGlyphOrder()))

    def test_reproducible(self):
        again = Path(self.temp.name) / 'again.ttf'
        build_font.merge(self.stock, LATIN, again)
        self.assertEqual(again.read_bytes(), self.output.read_bytes())


if __name__ == '__main__':
    unittest.main()

import unittest
from pypdf import PageObject
from pypdf.generic import NameObject, NumberObject
from eskd import inspect_page


class SheetTests(unittest.TestCase):
    def test_repeated_round_glyph_requires_whole_word_corroboration(self):
        from PIL import Image, ImageDraw
        from eskd.mass_ocr import numeric_candidate
        image = Image.new("L", (48, 38), 255)
        pen = ImageDraw.Draw(image)
        for x in (1, 27):
            pen.ellipse((x, 1, x+16, 31), outline=0, width=3)
        pen.line((23, 29, 20, 36), fill=0, width=2)
        for alternative, expected in [(("0.0", 79), "0,0"), (("1.0", 79), None),
                                      (("0.0", 20), None)]:
            replies = iter([("9,0", 93)]*3 + [("", 0), alternative])
            self.assertEqual(numeric_candidate(lambda im, numeric: next(replies), image), expected)

    def test_zero_like_contour_needs_confirmation_for_nine(self):
        from PIL import Image, ImageDraw
        from eskd.mass_ocr import numeric_candidate
        glyph = Image.new("L", (20, 32), 255)
        ImageDraw.Draw(glyph).ellipse((1, 1, 18, 30), outline=0, width=4)
        for isolated, expected in [(("0", 97), None), (("9", 35), None), (("9", 92), "9")]:
            replies = iter([("9", 96)]*3 + [isolated])
            self.assertEqual(numeric_candidate(lambda image, numeric: next(replies), glyph), expected)

    def test_reference_to_neighbouring_scale_cell_does_not_supply_mass(self):
        from unittest.mock import Mock
        import pymupdf as fitz
        from eskd.layout import _text_mass_fields
        page = Mock(rotation_matrix=fitz.Matrix(1, 1))
        words = []
        for shift, number in [(0, "7.02"), (110, "1,02")]:
            words.extend([(shift, 20, shift+18, 30, "Лит."),
                          (shift+22, 20, shift+47, 30, "Масса"),
                          (shift+54, 20, shift+96, 30, "Масштаб"),
                          (shift+24, 35, shift+45, 45, number),
                          (shift+62, 35, shift+82, 45, "1:2")])
        page.get_text.return_value = words
        self.assertEqual([f["value"] for f in _text_mass_fields(page)], ["7.02", "1,02"])

    def test_seven_misread_as_one_needs_isolated_confirmation(self):
        from PIL import Image, ImageDraw
        from eskd.mass_ocr import numeric_candidate
        image = Image.new("L", (20, 32), 255)
        pen = ImageDraw.Draw(image)
        pen.line([(1, 1), (18, 1), (7, 30)], fill=0, width=2)
        for isolated, expected in [(("7", 96), "7"), (("7", 22), None), (("1", 93), None)]:
            replies = iter([("1", 96)]*3 + [isolated])
            self.assertEqual(numeric_candidate(lambda image, numeric: next(replies), image), expected)

    def test_tall_decimal_mark_requires_matching_ocr(self):
        from PIL import Image, ImageDraw
        from eskd.mass_ocr import numeric_candidate
        image = Image.new("L", (35, 31), 255)
        pen = ImageDraw.Draw(image)
        pen.rectangle((2, 1, 7, 24), fill=0)
        pen.rectangle((24, 1, 29, 24), fill=0)
        pen.line((16, 20, 13, 30), fill=0, width=2)
        for text, expected in [("1,1", "1,1"), ("11", None), ("1.1", None)]:
            with self.subTest(text=text):
                self.assertEqual(numeric_candidate(lambda image, numeric: (text, 95), image), expected)

    def test_ocr_mass_requires_a_unique_labelled_cell_pair(self):
        import pymupdf as fitz
        from PIL import Image
        from eskd.mass_ocr import scan_mass, scan_mass_fields
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=300)
            def add_pair(x, y):
                page.draw_rect(fitz.Rect(x, y, x+60, y+17))
                page.draw_rect(fitz.Rect(x, y+17, x+60, y+52))
                page.insert_text((x+4, y+13), "Mass")
                page.insert_text((x+8, y+44), "42", fontsize=18)
            add_pair(20, 20)
            def known_text(image, *, numeric):
                self.assertIsInstance(image, Image.Image)
                return ("42" if numeric else "Масса"), 90
            field = scan_mass(page, known_text)
            self.assertEqual((field["status"], field["value"]), ("review", "42"))
            self.assertLess(field["box_pt"][0], 25)
            self.assertIsNone(scan_mass(page, lambda image, numeric: ("Размер", 95)))
            add_pair(200, 130)
            self.assertIsNone(scan_mass(page, known_text))
            fields = scan_mass_fields(page, known_text)
            self.assertEqual([item["value"] for item in fields], ["42", "42"])
            self.assertLess(fields[0]["box_pt"][2], fields[1]["box_pt"][0])

    def test_mass_is_read_only_under_matching_headers(self):
        from unittest.mock import Mock
        import fitz
        from eskd.layout import read_mass_field
        page = Mock(rect=fitz.Rect(0, 0, 300, 300), rotation_matrix=fitz.Matrix(1, 1),
                    derotation_matrix=fitz.Matrix(1, 1))
        drawing = fitz.open()
        self.addCleanup(drawing.close)
        sheet = drawing.new_page(width=300, height=300)
        page.get_pixmap.side_effect = sheet.get_pixmap
        headers = [(175, 200, 190, 210, "Лит."), (200, 200, 225, 210, "Масса"),
                   (230, 200, 275, 210, "Масштаб")]
        value = (201, 213, 224, 222, "1.25")
        scale = (240, 213, 260, 222, "1:2")
        page.get_text.return_value = headers + [value, scale]
        self.assertTrue(read_mass_field(page)["decimal_point"])
        page.get_text.return_value = headers + [(*value[:4], "1,25"), scale]
        self.assertFalse(read_mass_field(page)["decimal_point"])
        for words in (headers + [scale], headers[1:] + [value], headers + [value, value]):
            page.get_text.return_value = words
            self.assertEqual(read_mass_field(page)["status"], "unread")
        sheet.draw_rect(fitz.Rect(195, 210, 230, 235), width=1)
        page.get_text.return_value = headers + [scale]
        self.assertEqual(read_mass_field(page)["status"], "empty")
        sheet.draw_line((210, 218), (214, 225), width=1)
        self.assertEqual(read_mass_field(page)["status"], "unread")

    def test_frame_requires_four_strokes(self):
        import numpy as np
        from eskd.layout import bounds
        mask = np.zeros((150, 200), dtype=bool)
        mask[10, 8:195] = mask[140, 8:195] = True
        mask[10:141, 8] = True
        self.assertIsNone(bounds(mask))
        mask[10:141, 194] = True
        self.assertEqual(bounds(mask), (8, 10, 194, 140))

    def test_units_rotation_and_tolerances(self):
        for width, height, expected in [(421, 595, "A2"), (424, 594, None),
                                        (297, 630, "A4x3"), (210, 148, "A5")]:
            page = PageObject.create_blank_page(width=width*72/50.8, height=height*72/50.8)
            page[NameObject("/UserUnit")] = NumberObject(2)
            page.rotate(90)
            report = inspect_page(page, 1)
            self.assertEqual(report["format"], expected)
            self.assertEqual(report["sheet_mm"], [height, width])

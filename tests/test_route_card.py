"""Regression checks for the optional local ESTD reader."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if all(importlib.util.find_spec(m) for m in ("pdfplumber", "numpy", "PIL")):
    import numpy as np
    from estd import reader
    from scripts import inspect_route_batch as batch
else:
    reader = None


@unittest.skipIf(reader is None, "Install scripts/requirements-route-card.txt for the vision tests")
class RouteCardTests(unittest.TestCase):
    def test_form_code_must_occupy_its_own_footer_cell(self):
        sheet = reader.Image.new("L", (400, 300), 255)
        draw = reader.ImageDraw.Draw(sheet)
        draw.rectangle((8, 273, 390, 292), outline=0)
        draw.line((60, 273, 60, 292), fill=0)
        page = Mock(width=400, height=300, bbox=(0, 0, 400, 300), page_number=2)
        code = dict(text="КЭ", x0=26, top=278, x1=44, bottom=289)
        with tempfile.TemporaryDirectory() as out:
            def match(words, ocr=None):
                return reader.find_form_marker(page, sheet, [page.bbox], words, ocr, Path(out))

            marker, angle = match([code])
            self.assertEqual((marker["code"], marker["method"], angle), ("КЭ", "pdf", 0))
            self.assertTrue((Path(out)/marker["crop"]).is_file())
            self.assertIsNone(match([dict(code, top=100, bottom=110)]))
            self.assertIsNone(match([code, dict(code, text="лист")]))
            for text, kind in (("МК", "route_card"), ("OK", "operation_card"), ("MK", "route_card")):
                self.assertEqual(match([dict(code, text=text)])[0]["type"], kind)
            self.assertIsNone(match([dict(code, text="МК-1")]))
            ocr = Mock()
            ocr.line.return_value = "КЗ"
            ocr.recognize_line.return_value = ("КЗ", 95)
            self.assertIsNone(match([], ocr))
            ocr.recognize_line.return_value = ("КЭ", 34)
            self.assertIsNone(match([], ocr))

    def test_outer_frame_is_not_read_as_the_footer(self):
        sheet = reader.Image.new("L", (570, 400), 255)
        draw = reader.ImageDraw.Draw(sheet)
        draw.rectangle((25, 15, 540, 382), outline=0)
        draw.rectangle((46, 340, 520, 364), outline=0)
        draw.line((110, 340, 110, 364), fill=0)
        self.assertEqual(reader.footer_cell(sheet), (46, 340, 110, 364))

    def test_uncertain_scanned_word_requires_matching_confident_retry(self):
        image = reader.Image.new("L", (96, 42), 255)
        reader.ImageDraw.Draw(image).text((14, 12), "word", fill=0)
        ocr = reader.LocalOCR(None, None)
        ocr.engine = Mock()
        first_passes = [("прокат", 34), ("прокат", 48)]
        cases = [
            ([("другое", 95), ("прокат", 76)], "прокат"),
            ([("другое", 95), ("прокат", 60)], ""),
        ]
        for retries, expected in cases:
            ocr.recognize_line = Mock(side_effect=first_passes + retries)
            with patch.object(reader, "engineering_text", return_value=image):
                self.assertEqual(ocr.line(image, (0, 0, 96, 42), scan=True), expected)

    def test_rotated_scan_finds_unique_heading_in_offset_image(self):
        page = Mock(width=200, height=120, bbox=(10,20,210,140))
        image = reader.Image.new("L", (400,240), 255)
        obj = {"x0": 30, "top": 30, "x1": 80, "bottom": 130}
        ocr = Mock()
        ocr.scan_lines.return_value = [("Лрофиль и размеры", (40,10,120,30))]
        angle, anchors, evidence = reader.scan_headings(page, image, [obj], ocr)
        self.assertEqual(angle, 90)
        self.assertEqual(anchors[1], ((30,25,70,35), "scan_ocr"))
        self.assertEqual(evidence[0][1:], (60,50,140,70))
        ocr.scan_lines.return_value *= 2
        self.assertIsNone(reader.scan_headings(page, image, [obj], ocr))

    def test_batch_continues_after_error_and_keeps_duplicate_names_separate(self):
        field = {"value": {"text": ""}}
        result = {"sha256": "example", "pages": [{
            "page": 1, "fields": {key: field for key, _, _ in reader.FIELDS},
            "preview": "page-1.png", "has_text_layer": True, "seconds": 0.2}]}
        with tempfile.TemporaryDirectory() as out, patch.object(
                batch, "analyze_document", side_effect=[ValueError("Invalid PDF"), result, result]) as inspect:
            args = ["check", "bad.pdf", "a/book.pdf", "b/book.pdf", "--output", out]
            with patch.object(sys, "argv", args):
                self.assertEqual(batch.main(), 1)
            summary = json.loads(next(Path(out).glob("*/summary.json")).read_bytes())
            self.assertEqual((summary["pages"], summary["failed_documents"]), (2, 1))
            _, a, b = summary["documents"]
            self.assertNotEqual(a["report"], b["report"])
            self.assertEqual(a["fields"]["designation"], {"with_text": 0, "located": 1})
            self.assertEqual(inspect.call_count, 3)

    def test_page_without_text_is_not_rasterized(self):
        page = Mock(page_number=1, width=100, height=80, chars=[])
        page.extract_words.return_value = []
        with tempfile.TemporaryDirectory() as out:
            result = reader.read_page(page, Path(out))
        page.to_image.assert_not_called()
        self.assertTrue(all(f["status"] == "review" for f in result["fields"].values()))

    def test_reversed_glyph_positions_need_a_unique_literal_heading(self):
        text = reader.LABEL
        chars = [{"text": c, "x0": 120-i, "x1": 121-i, "top": 20, "bottom": 28,
                  "height": 8} for i,c in enumerate(text)]
        box, method = reader.find_anchor(chars, text, {}, text)
        self.assertEqual(method, "pdf_stream")
        self.assertIsNotNone(box)
        self.assertEqual(reader.find_anchor(chars*2, text*2, {}, text), (None, "ambiguous"))

    def test_adjacent_field_requires_side_walls_and_rejects_merged_headings(self):
        ink = np.zeros((90, 200), dtype=bool)
        ink[[20, 34, 53], 50:141] = True
        ink[20:54, [50, 140]] = True
        anchor = (78, 22, 108, 32)
        header, data, _ = reader.locate(ink, anchor, [], "below")
        self.assertEqual(header, (50, 20, 140, 34))
        self.assertEqual(data, (50, 34, 140, 53))
        ink[36:52, 50] = False
        self.assertIsNone(reader.locate(ink, anchor, [], "below")[1])
        other = [("операции", 53, 22, 73, 32)]
        self.assertIsNone(reader.locate(ink, anchor, other, "below")[0])

    def test_form_heading_controls_rotation_and_source_box_roundtrips(self):
        stream = "Приложение " + reader.LABEL
        chars = [dict(text=c, matrix=(1,0,0,1,0,0)) for c in stream]
        for c in chars[-len(reader.LABEL):]:
            c["matrix"] = (0,1,-1,0,0,0)
        self.assertEqual(reader.form_rotation(chars, stream), 90)
        for angle in (0, 90, 180, 270):
            source = (2, 7, 12, 23)
            box = reader.rotate_box(source, 50, 90, angle)
            size = (90, 50) if angle in (90, 270) else (50, 90)
            self.assertEqual(reader.rotate_box(box, *size, -angle % 360), source)

    def test_symbol_profile_decodes_variable_dimensions_and_requires_symbol_font(self):
        encoded = [0xF0C6, 0xF020, 0xF035, 0xF0B4, 0xF032, 0xF037]
        chars = [dict(text=chr(code), x0=i*6, x1=i*6+6, top=0, bottom=10,
                      doctop=0, upright=True, fontname="Subset+Symbol") for i,code in enumerate(encoded)]
        self.assertEqual(reader.symbol_profile(chars), "⌀ 5×27")
        self.assertEqual(reader.symbol_profile([dict(c, fontname="Other") for c in chars]), "")

    def test_first_numbered_row_in_outlined_table(self):
        ink = np.zeros((120, 200), dtype=bool)
        ink[[20,34,50,70,90], 5:141] = True
        ink[20:91, [5,35,50,140]] = True
        anchor = (78,22,108,32)
        words = [("А",14,22,22,32)]
        marker = Mock(side_effect=[False, True])
        self.assertEqual(reader.locate(ink, anchor, words, "row_a", marker)[1], (50,50,140,70))
        self.assertEqual(marker.call_args.args[0], (5,50,35,70))
        self.assertIsNone(reader.locate(ink, anchor, words, "row_a", lambda box: None)[1])

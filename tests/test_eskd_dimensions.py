import unittest
from unittest.mock import Mock

import pymupdf as fitz
from eskd.dimensions import parse_label, pdf_lines


class AnnotationTests(unittest.TestCase):
    def test_drawing_notation(self):
        self.assertEqual(parse_label("Ø45m5")["fit"], "m5")
        self.assertEqual(parse_label("Ø42−0,16")["deviation"], "-0,16")
        self.assertEqual(parse_label("2хR0,3")["count"], "2")
        self.assertEqual(parse_label("28±0,26")["value"], "28")
        self.assertEqual(parse_label("3×45°")["kind"], "chamfer")
        self.assertEqual(parse_label("27°29'+30\"")["deviation"], "+30″")
        self.assertTrue(parse_label("(115)")["reference"])
        for noise in ["ГОСТ 1050-88", "1:1", "Ra 0,63", "А-А", "Масса 15"]:
            self.assertIsNone(parse_label(noise))

    def test_only_touching_exponent_is_attached(self):
        def line(text, box):
            return {"dir": (1,0), "bbox": box, "spans": [{"text":text, "font":"GOSTTypeA"}]}
        page = Mock(rotation_matrix=fitz.Matrix(1,1))
        page.get_text.return_value = {"blocks":[{"lines":[line("7",(10,10,20,20)),
            line("+0,3",(20,8,29,14)), line("50",(40,10,60,20)), line("+1",(77,6,85,14))]}]}
        self.assertEqual([s for _,s,_ in pdf_lines(page)], ["7+0,3","50","+1"])

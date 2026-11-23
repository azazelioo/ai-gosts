import unittest
from pypdf import PageObject
from pypdf.generic import NameObject, NumberObject
from eskd import inspect_page


class SheetTests(unittest.TestCase):
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

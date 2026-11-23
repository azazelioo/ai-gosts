import pymupdf
import unittest
from eskd.dimension_lines import link_dimensions


class DimensionLinesTests(unittest.TestCase):
    def test_arrowheads_required_at_both_ends(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_line((20,60),(100,60))
            item = {"box_pt":[50,48,69,58], "method":"pdf"}
            page.draw_line((20,60),(27,58))
            page.draw_line((20,60),(27,62))
            link_dimensions(page,[item])
            self.assertNotIn("dimension_line",item)
            page.draw_line((100,60),(93,58))
            page.draw_line((100,60),(93,62))
            link_dimensions(page,[item])
            self.assertEqual(item["dimension_line"]["endpoints_pt"],[[20,60],[100,60]])

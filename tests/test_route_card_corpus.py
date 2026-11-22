"""Optional offline acceptance test on independently published PDF forms.

Set ESTD_TEST_PDFS to a directory containing the files listed below and install
the route-card requirements with local Russian tessdata. No downloads in tests.
Run: python -m unittest discover -s tests -p test_route_card_corpus.py

Inputs from earchive.tpu.ru/bitstream/11683/<handle>/1/<filename>:
49126/TPU571070.pdf, 54794/TPU731275.pdf, 72271/TPU1375312.pdf.
Save the following SGAU course manual as vavilov.pdf:
https://www.vavilovsar.ru/sveden/files/MU_po_vypolneniyu_KP_B1.O.46_23.05.01_S-NTS_FGOS_VO_3_2021.pdf
"""
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path


@unittest.skipUnless(os.environ.get("ESTD_TEST_PDFS"), "Set ESTD_TEST_PDFS for the PDF corpus")
class FormCorpusTests(unittest.TestCase):
    def test_source_forms(self):
        from scripts.inspect_route_card import LocalOCR, analyze_document

        # Physical page, visually checked footer code (None for a negative),
        # and optionally the exact expected profile text.
        cases = {
            "TPU571070.pdf": [(56, "МК", None), (59, "ОК", None), (60, "КЭ", None)],
            "TPU731275.pdf": [(95, "МК", None), (97, "КЭ", None), (100, "ОК", "Прокат Ø95×312")],
            "vavilov.pdf": [(7, None, None), (49, "МК", None), (53, "ОК", None), (55, "КЭ", None),
                            (56, None, None), (72, "МК", None)],
            "TPU1375312.pdf": [(115, "КЭ", None)],
        }
        data = Path(os.environ["ESTD_TEST_PDFS"])
        tessdata = Path(os.environ.get("TESSDATA_PREFIX", Path(sys.prefix)/"share"/"tessdata"))
        with ExitStack() as stack, tempfile.TemporaryDirectory() as tmp:
            ocr = LocalOCR(stack, tessdata)
            for name, samples in cases.items():
                with self.subTest(document=name):
                    out = Path(tmp)/Path(name).stem
                    report = analyze_document(data/name, out, ocr, [n for n, _, _ in samples])
                    for page, (number, code, text) in zip(report["pages"], samples, strict=True):
                        with self.subTest(page=number):
                            self.assertEqual(page["page"], number)
                            self.assertEqual(page.get("form", {}).get("code"), code)
                            profile = page["fields"]["profile_size"]
                            self.assertEqual(profile["status"] == "not_applicable", code == "КЭ")
                            if code:
                                self.assertTrue((out/page["form"]["crop"]).is_file())
                            if text:
                                self.assertEqual(profile["value"]["text"], text)

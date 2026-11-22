"""Public API, source references and isolation of reviewed training documents."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("pdfplumber") and importlib.util.find_spec("numpy")
                     and importlib.util.find_spec("PIL"), "Install the ESTD dependencies")
class ModuleTests(unittest.TestCase):
    def test_public_contract_and_failed_run_isolation(self):
        from PIL import Image
        from estd import analyze_pdf, capabilities, normative_references

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/"input.pdf"
            Image.new("RGB", (150, 100), "white").save(source, "PDF")
            runs = root/"runs"
            first = analyze_pdf(source, output_dir=runs, pages=[1])
            second = analyze_pdf(source, output_dir=runs, pages=[1])
            self.assertNotEqual(first.directory, second.directory)
            with self.assertRaises(ValueError):
                analyze_pdf(source, output_dir=runs, pages=[2])
            self.assertEqual(set(runs.iterdir()), {first.directory, second.directory})
            self.assertEqual(json.loads((first.directory/"result.json").read_text("utf-8")), first.report)
            self.assertEqual(first.report["schema_version"], capabilities()["schema_version"])
            self.assertFalse(first.report["supported_form_found"])
            self.assertEqual(first.report["pages"][0]["fields"]["profile_size"]["status"], "review")
            self.assertEqual(first.report["normative_references"], [])
            refs = normative_references("МК")
            self.assertEqual([(r["field"], r["page"]) for r in refs if r["field"]],
                             [("profile_size", 7), ("designation", 7)])

    def test_same_document_cannot_supply_training_and_test_pages(self):
        from PIL import Image, ImageDraw
        from estd.learning import _reviewed_samples

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = Image.new("L", (64, 40), 255)
            ImageDraw.Draw(image).text((10, 10), "MK", fill=0)
            image.save(root/"cell.png")
            row = dict(source_sha256="a"*64, crop="cell.png", reviewed=True, label="МК",
                       crop_sha256=hashlib.sha256((root/"cell.png").read_bytes()).hexdigest())
            manifest = root/"examples.jsonl"
            manifest.write_text("\n".join(json.dumps(dict(row, page=n, split=s))
                                          for n, s in [(1, "train"), (2, "test")]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "both splits"):
                _reviewed_samples(manifest)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "Install ESTD training dependencies")
    def test_reloaded_weights_and_duplicate_exclusion(self):
        from PIL import Image, ImageDraw
        from estd.learning import FormCodeModel, train_form_model

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = []
            for n, (label, glyph, split) in enumerate([("МК", "M", "train"), ("ОК", "O", "train"),
                                                        ("КЭ", "K", "train"), ("МК", "M", "test"),
                                                        ("ОК", "OK", "test")], 1):
                image = Image.new("L", (64, 40), 255)
                ImageDraw.Draw(image).text((12, 12), glyph, fill=0)
                path = root/f"{n}.png"
                image.save(path)
                records.append(dict(label=label, split=split, page=n, reviewed=True, crop=path.name,
                                    source_sha256=("a" if split == "train" else "b")*64,
                                    crop_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            index = root/"samples.jsonl"
            index.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            metrics = train_form_model(index, root)
            self.assertEqual(metrics["cross_split_duplicates_excluded"], 1)
            self.assertEqual(metrics["unique_test"], 1)
            model = FormCodeModel(root/"form-model.npz")
            self.assertEqual(model.predict(root/"5.png"), metrics["test_results"][0]["predicted"])

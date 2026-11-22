"""Train and evaluate a local classifier on reviewed crops of form-code cells."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

from .reader import FORM_TYPES


def _features(image):
    gray = np.asarray(image.convert("L"))
    if min(gray.shape) < 9:
        raise ValueError("Form-code crop is too small")
    ink = gray[3:-3, 3:-3] < 175
    # The crop includes the rules of the cell; train on the lettering inside.
    ink[ink.mean(axis=1) > .85, :] = False
    ink[:, ink.mean(axis=0) > .85] = False
    y, x = np.nonzero(ink)
    if not len(x):
        raise ValueError("Form-code crop has no lettering")
    text = Image.fromarray(ink[y.min():y.max()+1, x.min():x.max()+1].astype("uint8")*255)
    scale = min(44/text.width, 28/text.height)
    text = text.resize((max(1,round(text.width*scale)), max(1,round(text.height*scale))),
                       Image.Resampling.LANCZOS)
    canvas = Image.new("L", (48, 32))
    canvas.paste(text, ((48-text.width)//2, (32-text.height)//2))
    return np.asarray(canvas).ravel()


def _reviewed_samples(manifest):
    samples = []
    document_splits = {}
    seen_pages = set()
    feature_labels = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        code, split = row["label"], row["split"]
        if row.get("reviewed") is not True or code not in FORM_TYPES or split not in ("train", "test"):
            raise ValueError("Expected a reviewed form-code label and a train/test assignment")
        doc, page = row["source_sha256"], row["page"]
        if not re.fullmatch(r"[a-f0-9]{64}", doc) or type(page) is not int or page < 1:
            raise ValueError("Expected a PDF fingerprint and a physical page number")
        if document_splits.setdefault(doc, split) != split:
            raise ValueError("Pages of one source PDF cannot be in both splits")
        if (doc, page) in seen_pages:
            raise ValueError("Duplicate reviewed page")
        seen_pages.add((doc, page))
        path = manifest.parent/row["crop"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["crop_sha256"]:
            raise ValueError(f"Crop fingerprint mismatch: {path.name}")
        with Image.open(path) as image:
            feat = _features(image)
        key = hashlib.sha256(feat.tobytes()).digest()
        if feature_labels.setdefault(key, code) != code:
            raise ValueError("Identical form-code crops have conflicting labels")
        samples.append((split, code, feat, key, doc, page))
    return samples


def train_form_model(manifest, output_dir):
    """Fit a small neural net on training documents. Return metrics on held-out documents."""
    from sklearn.neural_network import MLPClassifier

    manifest = Path(manifest)
    samples = _reviewed_samples(manifest)
    train, test = {}, {}
    for split, code, feat, key, doc, page in samples:
        (train if split == "train" else test).setdefault(key, (feat, code, doc, page))
    overlap = set(train).intersection(test)
    for key in overlap:
        del test[key]
    if {item[1] for item in train.values()} != set(FORM_TYPES) or not test:
        raise ValueError("Need training examples for every form and distinct held-out examples")
    x, y, _, _ = zip(*train.values())
    model = MLPClassifier(hidden_layer_sizes=(24,), solver="lbfgs", max_iter=300, random_state=42)
    model.fit(np.asarray(x)/255., y)
    features, expected, documents, pages = zip(*test.values())
    predicted = model.predict(np.asarray(features)/255.)
    results = [{"source_sha256": d, "page": p, "expected": e, "predicted": str(a)}
               for d,p,e,a in zip(documents, pages, expected, predicted)]
    metrics = {"task": "classify_cropped_form_code", "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
               "reviewed_pages": len(samples), "unique_train": len(train), "unique_test": len(test),
               "cross_split_duplicates_excluded": len(overlap), "correct": int(np.sum(predicted == expected)),
               "train_documents": sorted({s[4] for s in samples if s[0] == "train"}),
               "test_documents": sorted({s[4] for s in samples if s[0] == "test"}), "test_results": results}
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(dest/"form-model.npz", format_version=1, classes=model.classes_,
                        w1=model.coefs_[0], b1=model.intercepts_[0], w2=model.coefs_[1], b2=model.intercepts_[1])
    (dest/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


class FormCodeModel:
    """Load local weights once. Predict only on an already cropped marker."""

    def __init__(self, path):
        with Path(path).open("rb") as stream:
            self.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            stream.seek(0)
            with np.load(stream, allow_pickle=False) as model:
                if int(model["format_version"]) != 1:
                    raise ValueError("Unsupported form model format")
                self.classes = model["classes"]
                self.w1, self.b1 = model["w1"], model["b1"]
                self.w2, self.b2 = model["w2"], model["b2"]

    def predict(self, crop):
        with Image.open(crop) as image:
            hidden = np.maximum(0, (_features(image)/255.) @ self.w1 + self.b1)
        scores = hidden @ self.w2 + self.b2
        return str(self.classes[int(scores.argmax())])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = train_form_model(args.manifest, args.output)
    print(f'{metrics["correct"]}/{metrics["unique_test"]} held-out distinct crops')

"""Local CLI for single-page or full-document ESTD analysis."""
import argparse
import os
import sys
from contextlib import ExitStack
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from estd.reader import FIELDS, FORM_TYPES, LocalOCR, analyze_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pages", type=int, nargs="+")
    selection.add_argument("--all-pages", action="store_true", help="Inspect every physical PDF page")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tessdata", type=Path, help="Local directory containing rus.traineddata",
                        default=os.environ.get("TESSDATA_PREFIX", Path(sys.prefix)/"share"/"tessdata"))
    args = parser.parse_args()
    with ExitStack() as stack:
        ocr = LocalOCR(stack, args.tessdata)
        try:
            report = analyze_document(args.pdf, args.output, ocr, None if args.all_pages else args.pages or [11])
        except ValueError as exc:
            parser.error(str(exc))
    for p in report["pages"]:
        print(f"page {p['page']}: {p['status']}")
    print(args.output.resolve() / "index.html")


if __name__ == "__main__":
    main()

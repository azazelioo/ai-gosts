import argparse

from . import analyze_pdf

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--output", required=True)
    parser.add_argument("--pages", type=int, nargs="+")
    parser.add_argument("--tessdata", help="Local directory containing rus.traineddata")
    parser.add_argument("--formats-only", action="store_true")
    parser.add_argument("--dimensions", choices=["auto", "on", "off"], default="auto")
    args = parser.parse_args()
    print(analyze_pdf(args.pdf, output_dir=args.output, pages=args.pages, tessdata=args.tessdata, layout=not args.formats_only, dimensions=args.dimensions).directory / "result.json")

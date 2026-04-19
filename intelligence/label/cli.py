from __future__ import annotations

import argparse
from pathlib import Path

from .scraper import scrape_labels
from .exporters import export_csv, export_json, export_sqlite, export_txt


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="label-intel")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape", help="Scrape label metadata from Beatport/Traxsource")
    s.add_argument("--seeds", required=True, type=Path)
    s.add_argument("--out-dir", required=True, type=Path)
    s.add_argument("--cache-dir", type=Path, default=Path(".cache/label_intel"))
    s.add_argument("--sources", nargs="+", default=["beatport", "traxsource"], choices=["beatport", "traxsource"])
    s.add_argument("--delay", type=float, default=2.0)
    s.add_argument("--timeout", type=int, default=30)
    s.add_argument("--skip-enrich", action="store_true")

    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "scrape":
        store = scrape_labels(
            seed_path=args.seeds,
            cache_dir=args.cache_dir,
            source_names=args.sources,
            delay=args.delay,
            timeout=args.timeout,
            skip_enrich=args.skip_enrich,
        )
        args.out_dir.mkdir(parents=True, exist_ok=True)
        records = store.values()
        export_json(records, args.out_dir / "labels.json")
        export_csv(records, args.out_dir / "labels.csv")
        export_txt(records, args.out_dir / "labels.txt")
        export_sqlite(records, args.out_dir / "labels.sqlite")
        print(f"Wrote {len(records)} label records to {args.out_dir}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

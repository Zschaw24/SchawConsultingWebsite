#!/usr/bin/env python3
"""Render consignment tags to a PNG you can check on screen, or to ZPL.

    # eyeball the layout before touching a roll
    python scripts/preview_labels.py --out /tmp/tags.png

    # try a different label stock
    python scripts/preview_labels.py --width 32 --height 50 --out /tmp/tags.png

    # emit printer-ready ZPL for one tag
    python scripts/preview_labels.py --zpl --number 42 --condition good --box 18
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.labels import Stock, Tag, render_preview, render_zpl  # noqa: E402

SAMPLES = [
    (42, "good"),
    (1, "very good"),
    (117, "collectible"),
    (8, "good"),
    (256, "collectible"),
    (73, "very good"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=float, default=Stock.width_mm,
                    help="label width in mm, as the tag reads (default: %(default)s)")
    ap.add_argument("--height", type=float, default=Stock.height_mm,
                    help="label height in mm, as the tag reads (default: %(default)s)")
    ap.add_argument("--dpi", type=int, default=Stock.dpi, help="printer dpi (default: %(default)s)")
    ap.add_argument("--darkness", type=int, default=Stock.darkness, help="ZPL ^MD, 0-30")
    ap.add_argument("--rotation", type=int, default=Stock.feed_rotation, choices=[0, 90, 180, 270],
                    help="rotation applied on the way to the printer")
    ap.add_argument("--scale", type=int, default=6, help="preview upscale factor")
    ap.add_argument("--box", type=int, default=18)
    ap.add_argument("--listed", default="09/18/26")
    ap.add_argument("--out", default="tags-preview.png")
    ap.add_argument("--zpl", action="store_true", help="print ZPL for a single tag instead")
    ap.add_argument("--number", type=int, default=42)
    ap.add_argument("--condition", default="good")
    args = ap.parse_args()

    stock = Stock(width_mm=args.width, height_mm=args.height, dpi=args.dpi,
                  feed_rotation=args.rotation, darkness=args.darkness)

    if args.zpl:
        tag = Tag.build(args.number, args.condition, args.box, args.listed)
        print(render_zpl(tag, stock))
        return 0

    tags = [Tag.build(n, c, args.box, args.listed) for n, c in SAMPLES]
    sheet = render_preview(tags, stock, scale=args.scale)
    sheet.save(args.out)
    print(f"{args.out}  {sheet.size[0]}x{sheet.size[1]}px")
    print(f"stock {stock.width_mm}x{stock.height_mm}mm @ {stock.dpi}dpi "
          f"= {stock.width_px}x{stock.height_px}px per tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

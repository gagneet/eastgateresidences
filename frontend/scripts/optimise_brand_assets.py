#!/usr/bin/env python3
"""Regenerate the served brand assets from their full-resolution masters.

WHY
---
`public/eastgate-logo.png` shipped as a 1024x1024, 1.4 MB PNG and `public/east-gate.ico`
as a 432 KB icon. Both load on EVERY page, including `/login` and `/register`, which
render no dashboard data at all. Together they were ~1.7 MB of a ~2.2 MB page — about
three quarters of the weight of the entire application — while the logo is never
displayed larger than 40 CSS pixels (`h-10` in Header.tsx; 24/32 px in
DashboardLayout.tsx; `h-6` in Footer.tsx).

A 2026-08-24 Lighthouse run measured the cost: every page scored 0.35-0.54 with
LCP 3.4-5.6 s, and `uses-responsive-images` alone reported ~1.4 MB of savings.

WHAT IT DOES
------------
Rescales the master to a size that stays sharp at high DPI for the largest place the
logo actually appears, and rewrites the favicon with PNG-compressed frames at the
standard sizes. It is idempotent: running it twice produces the same bytes.

The masters live OUTSIDE `public/` (in `frontend/assets/brand/`) so the full-resolution
originals stay in the repo for design work without being served to every visitor.

USAGE
    python3 frontend/scripts/optimise_brand_assets.py --check   # CI guard, no writes
    python3 frontend/scripts/optimise_brand_assets.py --apply

Requires Pillow. The repo's backend venv already has it:
    backend/venv/bin/python3 frontend/scripts/optimise_brand_assets.py --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - dependency guidance
    sys.exit("Pillow is required. Try: backend/venv/bin/python3 " + __file__ + " --check")

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "frontend" / "public"
MASTERS = REPO / "frontend" / "assets" / "brand"

# The logo is rendered at most at 40 CSS px (Header.tsx `h-10`). 256 px keeps it sharp
# to a 6.4x device pixel ratio — far beyond any real display — for ~22 KB. Going larger
# buys nothing a user can see; going to 128 px would save ~13 KB but only covers 3.2x.
LOGO_PX = 256

# Standard favicon frames. 256 is retained for Windows tiles; PNG-compressed frames are
# what take the file from 432 KB to ~39 KB, so no size needs dropping to hit the budget.
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# Budgets guard against a future re-export quietly reintroducing a multi-megabyte asset.
BUDGETS_KB = {"eastgate-logo.png": 60, "east-gate.ico": 80}


def _master(name: str, served: Path) -> Image.Image:
    """Return the master image, promoting the currently-served file on first run."""
    master = MASTERS / name
    if master.exists():
        return Image.open(master).convert("RGBA")
    MASTERS.mkdir(parents=True, exist_ok=True)
    # First run: today's served file IS the full-resolution original, so preserve it
    # as the master before we overwrite the served copy with a smaller one.
    img = Image.open(served).convert("RGBA")
    img.save(master, "PNG", optimize=True)
    print(f"  preserved master -> {master.relative_to(REPO)}")
    return img


def apply() -> None:
    logo_out = PUBLIC / "eastgate-logo.png"
    ico_out = PUBLIC / "east-gate.ico"

    src = _master("eastgate-logo-master.png", logo_out)
    src.resize((LOGO_PX, LOGO_PX), Image.LANCZOS).save(logo_out, "PNG", optimize=True)
    print(f"  eastgate-logo.png -> {LOGO_PX}px, {logo_out.stat().st_size / 1024:.1f} KB")

    # Rebuild the favicon from the same master so the two never drift apart.
    src.save(ico_out, format="ICO", sizes=ICO_SIZES)
    print(f"  east-gate.ico     -> {len(ICO_SIZES)} frames, {ico_out.stat().st_size / 1024:.1f} KB")


def check() -> int:
    failed = False
    for name, budget in BUDGETS_KB.items():
        path = PUBLIC / name
        if not path.exists():
            print(f"MISSING {name}")
            failed = True
            continue
        kb = path.stat().st_size / 1024
        status = "ok " if kb <= budget else "OVER"
        if kb > budget:
            failed = True
        print(f"  [{status}] {name:22s} {kb:8.1f} KB  (budget {budget} KB)")
    if failed:
        print("\nRun with --apply to regenerate from frontend/assets/brand/.")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="regenerate the served assets")
    ap.add_argument("--check", action="store_true", help="verify sizes against budget")
    args = ap.parse_args()
    if args.apply:
        apply()
        return check()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())

"""Outlet marks: only icon-shaped logos are kept, everything else gets a tile."""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import struct
from intel_brief import outlets

def png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + b"IHDR" + struct.pack(">II", width, height)

# 1. dimensions are read straight from the header
assert outlets._dimensions(png(144, 33)) == (144, 33)
assert outlets._dimensions(png(180, 180)) == (180, 180)
assert outlets._dimensions(b"\x00\x00\x01\x00\x01\x00\x20\x20") == (32, 32)      # ICO
print("1. png and ico dimensions parsed")

# 2. a wordmark is rejected; a square icon is kept
assert not outlets._is_icon_shaped(png(144, 33)), "CoinDesk's 144x33 wordmark must be rejected"
assert outlets._is_icon_shaped(png(180, 180))
assert outlets._is_icon_shaped(png(57, 57))
assert outlets._is_icon_shaped(png(32, 24)), "mild rectangles are fine"
assert not outlets._is_icon_shaped(png(400, 40))
print("2. wide wordmarks rejected, square-ish icons accepted")

# 3. unknown/scalable formats are let through rather than discarded
assert outlets._is_icon_shaped(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
print("3. svg passes: it scales to whatever box it's given")

# 4. monograms are square, deterministic, and readable
for name, want in [("CoinDesk", "Co"), ("Sky News", "SN"), ("Hacker News", "HN")]:
    assert outlets._initials(name) == want, (name, outlets._initials(name))
svg = outlets.monogram_svg("CoinDesk")
assert 'viewBox="0 0 32 32"' in svg and "CoinDesk" in svg and svg.startswith("<svg")
assert outlets.monogram_svg("CoinDesk") == svg, "must be deterministic"
print("4. monogram initials:", {n: outlets._initials(n) for n in ["CoinDesk", "Sky News"]})

# 5. an outlet name that is punctuation or empty still produces something
assert outlets.monogram_svg("").startswith("<svg")
print("5. degenerate names don't raise")

print("\nALL CHECKS PASSED")

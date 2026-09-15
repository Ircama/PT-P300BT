import io, os, sys, contextlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import printlabel as pl

args = pl.set_args().parse_args(["-n", "x", "arial.ttf", "Hello"])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    image = pl.build_label(args)
    padded = pl.rasterize_label(image, args)
w, h = padded.size
print(f"PY {w}x{h}")
for y in range(0, h, 4):
    line = ""
    for x in range(0, w, 2):
        line += "#" if padded.getpixel((x, y)) else "."
    print(line)

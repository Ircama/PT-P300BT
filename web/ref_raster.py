"""Emit reference raster bytes from printlabel.py for comparison with the web port.

Usage: python web/ref_raster.py <case>
Prints JSON: {"width": W, "height": H, "bytes": "<hex>"} for the rasterized
label (the exact bytes sent to the printer, 1 bit per pixel).
"""
import io
import json
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import printlabel as pl


CASES = {
    # name: (text, extra kwargs)
    "hello": ("Hello", {}),
    "multiline": ("Line1\\nLine2", {}),
    "three_lines": ("A\\nB\\nC", {}),
    "symbols": ("→←↑↓★♥©®™", {}),
    "emoji": ("A\U0001F60CB", {}),
    "uniform": ("acca", {"uniform_font": True}),
    "fixed_size": ("Hello", {"fixed_font_size": 40}),
    "center": ("Hi", {"center_text": True}),
    "rulers": ("Hi", {"lines": True}),
    "tab": ("a\tb", {}),
    "unicode_esc": ("\\u0041\\u0042", {"unicode": True}),
    "tape6": ("Hello", {"tape_width": 6.0}),
    "tape9": ("Hello", {"tape_width": 9.0}),
    "hpad": ("Hi", {"h_padding": 20}),
    "vshift": ("Hi", {"v_shift": 5}),
    "endmargin": ("Hi", {"end_margin": 30}),
    "linespacing": ("A\\nB", {"line_spacing": 1.5}),
    "fontscale": ("Hello", {"font_scale": 60}),
    "textsize": ("Hello", {"text_size": 40}),
    "fixedwidth": ("Hi", {"fixed_width": 30.0}),
    "stroke": ("Hi", {"stroke_width": 2, "stroke_fill": "black"}),
}


def build(case):
    text, extra = CASES[case]
    args = pl.set_args().parse_args(["-n", "x", "arial.ttf", text])
    for k, v in extra.items():
        setattr(args, k, v)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        image = pl.build_label(args)
        padded = pl.rasterize_label(image, args)
    # 1-bit image -> bytes, 1 = black dot.
    w, h = padded.size
    data = padded.tobytes()
    # PIL '1' mode packs 8 px/byte, MSB first. Expand to 1 byte per pixel.
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            px = padded.getpixel((x, y))
            out[y * w + x] = 1 if px else 0
    return {"width": w, "height": h, "bytes": bytes(out).hex()}


if __name__ == "__main__":
    case = sys.argv[1] if len(sys.argv) > 1 else "hello"
    if case == "--list":
        print(json.dumps(sorted(CASES)))
    else:
        print(json.dumps(build(case)))

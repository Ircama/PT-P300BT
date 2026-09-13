import sys
import os
import re
import argparse
import serial
from serial.tools import list_ports
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pdf2image import convert_from_path

from labelmaker import do_print_job, reset_printer


# --------------------------------------------------------------------------
# Emoji fallback: a wrapper font that renders every character with the
# primary TrueType font, switching to a system emoji font for characters
# the primary font cannot represent (e.g. U+1F600 😀). printlabel normally
# drew those as empty .notdef boxes.
# --------------------------------------------------------------------------
_EMOJI_CANDIDATES = [
    r"C:\Windows\Fonts\seguiemj.ttf",            # Segoe UI Emoji (Win 8+)
    r"C:\Windows\Fonts\SegoeUIEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",  # macOS
    "/System/Library/Fonts/Apple Color Emoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",  # Linux
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/NotoColorEmoji-Regular.ttf",
    "/usr/local/share/fonts/NotoColorEmoji-Regular.ttf",
]
_emoji_path = None
_emoji_cmap = None
_CMAP_CACHE = {}      # path -> set(codepoints) cache for primary fonts


def _find_emoji_font():
    """Return (path, cmap) of the first usable emoji font, cached."""
    global _emoji_path, _emoji_cmap
    if _emoji_path is not None:
        return _emoji_path, _emoji_cmap
    from fontTools.ttLib import TTFont
    for cand in _EMOJI_CANDIDATES:
        if os.path.exists(cand):
            try:
                f = TTFont(cand, lazy=True)
                cmap = f.getBestCmap()
                if cmap and 0x1F600 in cmap:
                    _emoji_path, _emoji_cmap = cand, cmap
                    return cand, cmap
            except Exception:
                continue
    _emoji_path = ""  # remember failure
    _emoji_cmap = {}
    return "", {}


def is_emoji_char(ch):
    """True if `ch` is a proper emoji the system emoji font can render.

    Public helper used by the GUI to render emoji in color in the text
    input box (Tk/GDI draws them in monochrome otherwise). Only true
    emoji code points are matched (the emoji font also covers Latin
    letters, so matching its cmap alone would flag every letter).

    Joiner/variant characters (U+FE0F variation selector, U+200D ZWJ, …)
    are NOT emoji by themselves: they only modify the surrounding emoji,
    and rendering them alone produces no glyph. Flagging them as emoji
    while the renderer returns None breaks the placeholder bookkeeping in
    the GUI (infinite rebuild -> freeze), so they are excluded here.
    """
    if not ch:
        return False
    cp = ord(ch)
    # Emoji ranges: Miscellaneous Symbols and Pictographs (1F300+),
    # Emoticons (1F600+), Transport (1F680+), Supplemental Symbols (1F900+),
    # Symbols and Pictographs Extended-A (1FA70+), Dingbats/Misc symbols,
    # plus the variation selector for emoji presentation.
    if 0x1F000 <= cp <= 0x1FAFF:
        pass
    elif 0x2600 <= cp <= 0x27BF:
        pass
    elif 0x2B00 <= cp <= 0x2BFF:
        pass
    elif 0xFE0F == cp:
        return False  # variation selector alone: no glyph (joiner)
    elif 0x200D == cp:
        return False  # ZWJ alone: no glyph (joiner)
    elif 0x2300 <= cp <= 0x23FF and cp not in (0x23E9,):
        pass
    else:
        return False
    _path, _cmap = _find_emoji_font()
    if not _path or not _cmap or 0x1F600 not in _cmap:
        return False
    return cp in _cmap


def emoji_thumbnail(ch, height, mono=True):
    """Render `ch` (an emoji) as a raster `height` px high.

    Returns a PIL RGBA Image, or None if the character cannot be rendered.
    Used by the GUI to show emoji in the text input box. `mono=True`
    (default) renders the font's base monochrome outline — the same
    black-and-white glyph a standard text box draws; `mono=False`
    renders the embedded color bitmap.
    """
    path, _cmap = _find_emoji_font()
    if not path:
        return None
    raster, _w = _emoji_raster_to_height(
        path, ch, max(8, int(height)), mono=mono)
    return raster


def _luminance_mono(raster, lo=140.0, hi=175.0):
    """In-place: convert an RGBA COLR emoji raster to black-ink shapes.

    Applies a luminance cut to the embedded-color render: dark pixels
    (outline, eyes, mouth) become solid black, light pixels (the yellow
    face fill) become transparent so the label's white shows through — a
    face with an outline reads as a ring instead of a filled black blob.
    Used when --mono-emoji is OFF so the label preview still shows the
    COLR-derived shape in black and white (matching what actually prints)
    instead of the full-color bitmap. `lo`/`hi` are the configurable
    luminance thresholds (defaults 140/175).
    """
    if raster is None or raster.mode != "RGBA":
        return
    if lo > hi:
        lo, hi = hi, lo
    pix = raster.load()
    w, h = raster.size
    # Sharp luminance cut: dark ink (outline/features) stays black;
    # light fills (yellow face, whites) go transparent.
    LO, HI = float(lo), float(hi)
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            if not a:
                continue
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum <= LO:
                pix[x, y] = (0, 0, 0, a)          # solid ink
            elif lum >= HI:
                pix[x, y] = (0, 0, 0, 0)          # clear (white label)
            else:
                keep = int(round(a * (HI - lum) / (HI - LO)))
                pix[x, y] = (0, 0, 0, keep)     # antialias ramp


def _emoji_raster_to_height(emoji_path, char, target_h, mono=False):
    """Render an emoji as an RGBA raster scaled to `target_h` px high.

    Renders the glyph at a larger size (for antialiasing), crops the ink
    with the alpha channel and scales it down, preserving the aspect
    ratio. Returns (PIL.Image RGBA, width_px) or (None, 0) on failure.

    `mono=True` renders the glyph's BASE outline (the monochrome shape
    drawn by a standard Windows text box) instead of the embedded color
    bitmap: dark strokes on transparency, exactly the black-and-white
    glyph the user sees when typing the emoji in any text field.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        # Render at 2x the target height for a smoother downscale.
        big = max(32, int(target_h * 2))
        f = ImageFont.truetype(emoji_path, big)
        pad = int(big * 0.6)
        img = Image.new("RGBA", (big * 2 + pad * 2, big * 2 + pad * 2),
                        (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Baseline at the vertical middle of the big canvas.
        asc, desc = f.getmetrics()
        if mono:
            # Base monochrome glyph (no embedded_color): the outline /
            # silhouette the OS text box draws. Rendered as black ink.
            d.text((pad, pad + asc), char, font=f, fill=(0, 0, 0, 255),
                   anchor="ls")
        else:
            d.text((pad, pad + asc), char, font=f, fill=(0, 0, 0, 255),
                   anchor="ls", embedded_color=True)
        b = img.getbbox()
        if b is None:
            return None, 0
        crop = img.crop(b)
        w, h = crop.size
        if h <= 0:
            return None, 0
        new_w = max(1, int(round(w * target_h / h)))
        crop = crop.resize((new_w, target_h), Image.Resampling.LANCZOS)
        return crop, crop.width
    except Exception:
        return None, 0


class _MixedFont:
    """Transparent TrueType wrapper with raster emoji overlay.

    Text is measured and drawn EXACTLY like the original algorithm: the
    primary TrueType font is used for every glyph, and emoji characters
    (missing from the primary font's cmap) are treated as spaces for
    measurement purposes, so the auto-fit loop and the multiline layout
    behave identically to the original for any text.

    Emoji are NOT drawn as glyphs: each emoji is rendered separately as an
    RGBA raster, scaled to a target height, and pasted over the label:

      - line mode (default): target height = the text line height, pasted
        starting at the top of its line (in-line).
      - print mode (--emoji-print-area): target height = the printable
        band (64 px), pasted at the same position as merged images
        (in-band).

    The raster's width is also the pen advance, so the space occupied by
    an emoji is exactly the space the drawn emoji takes.
    """

    def __init__(self, primary, size, emoji_path, emoji_mode="line",
                 uniform=False, mono=True, ligatures=False):
        self.font = ImageFont.truetype(primary, size, encoding="utf-8")
        self.size = size
        self._ligatures = ligatures
        self._emoji_mode = emoji_mode or "line"
        # uniform=True: the auto-fit measures a fixed sample ("Ag") instead
        # of the specific text, so all texts of the same line count get the
        # same font size (useful when printing many labels with different
        # word lengths but same number of rows).
        self._uniform = uniform
        # mono=True: emoji rasters are flattened to black ink (what the
        # 1-bit thermal print actually looks like); False keeps the
        # embedded colors for screen-only rendering.
        self._mono = mono
        # Configurable luminance thresholds for the B/W derivation
        # (--luma-lo / --luma-hi).
        self._luma_lo, self._luma_hi = _LUMA_GLOBAL
        # Primary font cmap (cached per path): which chars need the emoji.
        self._primary_cmap = None
        primary_path = getattr(self.font, "path", None) or primary
        self._path = primary_path
        try:
            key = os.path.normcase(os.path.abspath(primary_path))
        except Exception:
            key = os.path.normcase(primary_path)
        if key in _CMAP_CACHE:
            self._primary_cmap = _CMAP_CACHE[key]
        else:
            try:
                from fontTools.ttLib import TTFont
                self._primary_cmap = set(TTFont(primary_path).getBestCmap())
                _CMAP_CACHE[key] = self._primary_cmap
            except Exception:
                self._primary_cmap = None
        # Emoji raster settings (filled in by build_label after the fit):
        self._emoji_target_h = None     # raster height in px
        self._emoji_band = False        # True = in-band (print position)
        self._emoji_band_y = 0          # y to paste band emoji (print_border)
        self._emoji_raster_cache = {}   # char -> (raster, width)
        self._emoji_path = emoji_path if emoji_path else None

    # -- helpers -----------------------------------------------------
    def is_emoji(self, ch):
        """True if `ch` must be rendered with the emoji font."""
        if self._emoji_path is None or self._primary_cmap is None:
            return False
        cp = ord(ch)
        return (0x1F600 in _emoji_cmap and cp in _emoji_cmap and
                cp not in self._primary_cmap)

    def _split(self, text):
        """Yield (font, chunk) runs: primary-font text vs emoji chars."""
        chunks = []
        buf = []
        for ch in text:
            if self.is_emoji(ch):
                if buf:
                    chunks.append((self.font, "".join(buf)))
                    buf = []
                chunks.append((None, ch))  # None = emoji slot
            else:
                buf.append(ch)
        if buf:
            chunks.append((self.font, "".join(buf)))
        return chunks

    def runs(self, text):
        return self._split(text)

    def _emoji_width(self, ch):
        """Advance (px) for an emoji char at the current target height.

        Without a target height yet (during the fit) it falls back to the
        primary font's em-box width so measurement still converges. In
        band mode the emoji is not inline: it sits in the left strip, so
        the inline text advance is a plain space.
        """
        if self._emoji_band:
            return self.font.getlength(" ")
        if self._emoji_target_h:
            raster, w = self._raster(ch)
            return w
        return max(1, int(round(self.font.getlength("M"))))

    def _raster(self, ch):
        """Cached RGBA raster + width for an emoji at the target height.

        The cache key includes the mono flag: mono=True renders the font's
        base B/W outline glyph; mono=False renders the embedded color
        bitmap and then applies a luminance cut, so the label preview /
        printed raster shows the COLR-derived shape in black and white
        (matching the 1-bit thermal output) instead of full color."""
        key = (ch, self._emoji_target_h, self._mono)
        if key in self._emoji_raster_cache:
            return self._emoji_raster_cache[key]
        raster, w = _emoji_raster_to_height(self._emoji_path, ch,
                                            self._emoji_target_h or 32,
                                            mono=self._mono)
        if raster is not None and not self._mono:
            # mono-emoji unchecked: derive black & white from the COLR
            # render via a luminance cut (outline stays, fill clears).
            _luminance_mono(raster, lo=self._luma_lo, hi=self._luma_hi)
        self._emoji_raster_cache[key] = (raster, w)
        return raster, w

    def set_emoji_line_height(self, line_height):
        """Line mode: emoji raster height = the text line height (px)."""
        self._emoji_target_h = max(4, int(round(line_height)))
        self._emoji_band = False
        self._emoji_raster_cache.clear()

    def set_emoji_band(self, band_y):
        """Print mode: emoji fills the printable band, pasted at band_y."""
        self._emoji_target_h = 64  # height_of_the_printable_area
        self._emoji_band = True
        self._emoji_band_y = int(band_y)
        self._emoji_raster_cache.clear()

    def band_width(self, text):
        """Total width (px) of all emoji rasters in `text`, in order.

        Used only in band mode: the emojis are laid out as a strip of
        images on the left, so the text starts to their right.
        """
        if not self._emoji_band:
            return 0
        total = 0
        for ch in text:
            if self.is_emoji(ch):
                _raster, w = self._raster(ch)
                total += w
        return total

    def _clean_text(self, text):
        """Text with every emoji replaced by a space (for measurement)."""
        return "".join(" " if self.is_emoji(ch) else ch for ch in text)

    # -- Pillow font surface -----------------------------------------
    def getmask(self, *a, **k):
        raise NotImplementedError(
            "use _draw_text_mixed() with _MixedFont instead of draw.text()")

    getmask2 = getmask

    def getbbox(self, text, mode="", direction=None, features=None,
                language=None, stroke_width=0, anchor=None, *args, **kwargs):
        """Ink bounding box of the text with emoji as advance slots.

        Measurement ALWAYS uses the primary TrueType font: emoji chars are
        replaced by spaces (their em-box advance), so the result matches
        exactly what the original algorithm would measure, keeping the
        auto-fit loop and the multiline layout identical to the original.

        When the emoji target height is set (after the fit), the width
        includes the emoji rasters (their real advance) so the image is
        wide enough to show them; the height stays the text height.
        """
        if not text:
            return (0, 0, 0, 0)
        has_emoji_target = self._emoji_target_h is not None
        clean = self._clean_text(text)
        if not clean.strip():
            # Only emoji: measure the em-box slot ("Ag") so the fit still
            # converges on the printable-area height.
            clean = "Ag"
        if self._uniform and clean.strip():
            # Uniform mode: the HEIGHT comes from the standard sample
            # ("Ag") so all texts with the same line count fit the same;
            # the WIDTH stays the real text width so long phrases are not
            # cut off.
            h_bbox = self.font.getbbox(
                _UNIFORM_SAMPLE, mode=mode, direction=direction,
                features=features, language=language,
                stroke_width=stroke_width, anchor=anchor,
                *args, **kwargs)
            w_bbox = self.font.getbbox(
                clean, mode=mode, direction=direction, features=features,
                language=language, stroke_width=stroke_width, anchor=anchor,
                *args, **kwargs)
            if has_emoji_target and any(self.is_emoji(ch) for ch in text) \
                    and not self._emoji_band:
                # Inline (line mode) with emoji: the width must also
                # include the emoji rasters, exactly like in the regular
                # mode below, or the label would be too short and cut the
                # text after the last emoji.
                w = int(round(self.getlength(text)))
                if anchor == "lt":
                    return (0, 0, w, h_bbox[3] - h_bbox[1])
                return (w_bbox[0], h_bbox[1], w_bbox[0] + w, h_bbox[3])
            if anchor == "lt":
                return (w_bbox[0], h_bbox[1], w_bbox[2],
                        h_bbox[3] - h_bbox[1])
            return (w_bbox[0], h_bbox[1], w_bbox[2], h_bbox[3])
        b = self.font.getbbox(
            clean, mode=mode, direction=direction, features=features,
            language=language, stroke_width=stroke_width, anchor=anchor,
            *args, **kwargs)
        if has_emoji_target and any(self.is_emoji(ch) for ch in text) \
                and not self._emoji_band:
            # Inline (line mode) with emoji: width = pen advance of the
            # whole string (raster slots), height stays the text height.
            w = int(round(self.getlength(text)))
            if anchor == "lt":
                return (0, 0, w, b[3] - b[1])
            return (b[0], b[1], b[0] + w, b[3])
        return b

    def getlength(self, text, *args, **kwargs):
        total = 0
        for font, chunk in self._split(text):
            if font is None:  # emoji slot
                for ch in chunk:
                    total += self._emoji_width(ch)
            else:
                total += font.getlength(chunk)
        return total

    def getmetrics(self):
        return self.font.getmetrics()


def _load_text_font(primary, size, emoji_mode="line", uniform=False,
                    mono=True, ligatures=None):
    """Create a font for drawing text, with emoji raster overlay."""
    if ligatures is None:
        ligatures = _LIGATURES_GLOBAL
    epath, _cmap = _find_emoji_font()
    return _MixedFont(primary, size, epath, emoji_mode=emoji_mode,
                      uniform=uniform, mono=mono, ligatures=ligatures)


# Global GSUB feature tag (e.g. "calt") set by build_label(); avoids
# threading an argument through every _load_text_font() call while keeping
# the option module-wide. An empty string disables the feature.
_LIGATURES_GLOBAL = ""

# Standard sample used by --uniform-font for the (fixed) measured height
# and baseline. "Agiy" covers tall ascenders (the 'i' rises above 'A' in
# many fonts, e.g. DejaVu Sans Mono Bold where 'i' asc=33 vs 'A'=29) and
# deep descenders (y/g), so no glyph of the actual text overflows the
# print band once the font is sized from this sample.
_UNIFORM_SAMPLE = "Agiy"

# Global luminance thresholds (--luma-lo / --luma-hi) for the B/W emoji
# derivation, set by build_label() like the ligature feature tag.
_LUMA_GLOBAL = (140.0, 175.0)


# ---------------------------------------------------------------------------
# Ligature support (--ligatures FEATURE)
# ---------------------------------------------------------------------------
# The classic PIL/FreeType path does not apply OpenType GSUB substitutions
# (no libraqm), so a text like "-->" in Fira Code prints as three separate
# characters. When uharfbuzz is available, --ligatures FEATURE lets the
# user enable a GSUB feature of the selected font (e.g. --ligatures calt,
# --ligatures liga, --ligatures dlig): the text is shaped with HarfBuzz
# and every place where the FEATURE really substitutes glyphs is replaced
# by a Private-Use-Area code point that maps to the substituted glyph in
# an in-memory copy of the font, so FreeType rasterizes the ligature with
# the exact same rendering as normal characters. All other characters are
# drawn by the normal Pillow path - unchanged.
_LIG_CACHE = {}  # (font_path, size) -> PUA-extended ImageFont
_LIG_TT_CACHE = {}  # font_path -> TTFont (cached)


def _lig_features(font_path):
    """List the GSUB feature tags of the font (sorted), or [] on error."""
    try:
        from fontTools.ttLib import TTFont
        tt = _LIG_TT_CACHE.get(font_path)
        if tt is None:
            tt = TTFont(font_path, lazy=True)
            _LIG_TT_CACHE[font_path] = tt
        gsub = tt.get("GSUB")
        if gsub is None or not getattr(gsub, "table", None):
            return []
        feats = set()
        for rec in gsub.table.FeatureList.FeatureRecord:
            feats.add(rec.FeatureTag)
        return sorted(feats)
    except Exception:
        return []


def _lig_ext_cmap(font_path):
    """Extended cmap {cp -> glyph_name} for the PUA font (cached)."""
    try:
        from fontTools.ttLib import TTFont
        tt = _LIG_TT_CACHE.get(font_path)
        if tt is None:
            tt = TTFont(font_path, lazy=False)
            _LIG_TT_CACHE[font_path] = tt
        ext = dict(tt.getBestCmap() or {})
        order = tt.getGlyphOrder()
        existing = set(ext.values())
        pua = 0xE000
        for gname in order:
            if gname in existing:
                continue
            while pua in ext:
                pua += 1
            ext[pua] = gname
            pua += 1
        return ext
    except Exception:
        return {}


def _lig_pua_font(font_path, size):
    """PUA-extended ImageFont (cached). Returns None on error."""
    key = (font_path, size)
    if key in _LIG_CACHE:
        return _LIG_CACHE[key]
    entry = None
    try:
        from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
        ext = _lig_ext_cmap(font_path)
        if not ext:
            entry = None
        else:
            import io
            from fontTools.ttLib import TTFont
            tt = _LIG_TT_CACHE.get(font_path)
            if tt is None:
                tt = TTFont(font_path, lazy=False)
                _LIG_TT_CACHE[font_path] = tt
            st = CmapSubtable.newSubtable(12)
            st.platformID, st.platEncID, st.language = 3, 10, 0
            st.cmap = ext
            tt["cmap"].tables = [st]
            bio = io.BytesIO()
            tt.save(bio)  # one-off per (path, size): slow but fine
            bio.seek(0)
            entry = ImageFont.truetype(bio, size, encoding="utf-8")
    except Exception:
        entry = None
    _LIG_CACHE[key] = entry
    return entry


def _lig_shape(font_path, text, feature):
    """Shape `text` with the feature ON -> [(gid, cluster)] or None."""
    try:
        import uharfbuzz as hb
        blob = hb.Blob.from_file_path(font_path)
        face = hb.Face(blob)
        font = hb.Font(face)
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf, {feature: True})
        return [(i.codepoint, i.cluster) for i in buf.glyph_infos]
    except Exception:
        return None


def _lig_substitute(text, font_path, feature):
    """Return `text` with ligature glyphs mapped to PUA code points.

    Characters whose glyph is unchanged by the feature stay exactly as
    they are; only the substituted (ligature) runs get PUA chars, which
    the PUA-extended font renders with the substituted glyph. The
    comparison is against the glyph FreeType would render naively (the
    nominal glyph per code point), so every substitution the font makes
    with the feature enabled (e.g. "calt" arrows in Fira Code) is caught,
    even if HarfBuzz applies it by default. `feature` may be a single tag
    or a comma-separated list ("calt,liga"); each tag is applied in turn.
    Returns the text unchanged if shaping is not available or the feature
    is not in the font.
    """
    if not text or not feature:
        return text
    result = text
    for tag in [t.strip() for t in feature.split(",") if t.strip()]:
        if tag not in _lig_features(font_path):
            continue  # unknown feature for this font: never alter text
        shaped = _lig_shape(font_path, result, tag)
        if not shaped:
            continue
        ext = _lig_ext_cmap(font_path)
        if not ext:
            continue
        # reverse mapping glyph name -> pua (unique in ext)
        name_to_pua = {}
        for cp, nm in ext.items():
            name_to_pua.setdefault(nm, chr(cp))
        try:
            import uharfbuzz as hb
            blob = hb.Blob.from_file_path(font_path)
            face = hb.Face(blob)
            hbfont = hb.Font(face)
        except Exception:
            continue
        out = list(result)
        for idx, (gid, cl) in enumerate(shaped):
            if cl >= len(result):
                continue
            # what FreeType would draw for this character
            nominal = None
            try:
                nominal = hbfont.get_nominal_glyph(ord(result[cl]))
            except Exception:
                nominal = None
            if nominal == gid:
                continue  # normal character: unchanged
            # substituted glyph: find how many chars it covers (up to the
            # next cluster), map its name to the PUA and replace the range.
            nxt = len(result)
            for _g2, c2 in shaped[idx + 1:]:
                if c2 != cl:
                    nxt = c2
                    break
            gname = None
            try:
                tt = _LIG_TT_CACHE.get(font_path)
                if tt is None:
                    from fontTools.ttLib import TTFont
                    tt = TTFont(font_path, lazy=False)
                    _LIG_TT_CACHE[font_path] = tt
                order = tt.getGlyphOrder()
                gname = order[gid] if gid < len(order) else None
            except Exception:
                gname = None
            pua = name_to_pua.get(gname)
            if pua:
                lo = max(0, cl)
                hi = min(nxt, len(result))
                out[lo:hi] = [pua] + [""] * (hi - lo - 1)
        result = "".join(out)
    return result


def _draw_text_mixed(draw, xy, text, font, fill=None, anchor=None,
                     stroke_width=0, stroke_fill=None):
    """Draw `text`: normal glyphs with the primary font, emoji as rasters.

    Glyph chunks are drawn on a single shared baseline (anchor "ls"), the
    way the original draws one continuous string: every run keeps its
    natural advance and left-bearing, so the first letter of a run is
    never shifted (fixes "a" looking raised in "a<emoji>abC"). Emoji chars
    are pasted as rasters at their slot:

      - in-line (default): raster height = the line height, pasted at the
        top of the CURRENT row (`y0`).
      - in-band (--emoji-print-area): raster fills the printable band and
        is pasted at the top of its row (the band only drives the height,
        the position follows the row, like an image inside that row).

    `xy` is the top-left of the text block when anchor is "lt" (the
    original convention): y0 is the top of the row being drawn, and the
    baseline is y0 + the ink ascent of the clean text.
    """
    if not text:
        return
    if not isinstance(font, _MixedFont):
        draw.text(xy, text, font=font, fill=fill, anchor=anchor,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        return
    x0, y0 = xy
    clean = font._clean_text(text)
    if font._emoji_band:
        # Band mode: the emoji are pasted by build_label as a strip of
        # images on the left (like merged images); here only the clean
        # text is drawn, starting at x0 (which build_label already shifted
        # past the strip). Real newlines (API misuse) are drawn without an
        # anchor: Pillow's multiline_text rejects anchors.
        if clean.strip():
            draw.text((x0, y0), clean, font=font.font, fill=fill,
                      anchor=(anchor if "\n" not in clean else None),
                      stroke_width=stroke_width,
                      stroke_fill=stroke_fill)
        return
    # Baseline selection:
    # - default: from the CLEAN TEXT bounding box (anchor "ls"), i.e.
    #   y0 is the top of the text ink exactly like PIL's draw.text with
    #   anchor "lt" — the original behaviour. Adding a tall letter raises
    #   only the boxes that need it; the text keeps its natural vertical
    #   place (top at y0), never drifting down.
    # - --uniform-font: from the standard sample "Ag" (the same measure
    #   the auto-fit used), so every text variant shares a FIXED baseline
    #   and the existing glyphs never shift when a tall letter is added.
    if font._uniform:
        asc = -font.font.getbbox(_UNIFORM_SAMPLE, anchor="ls")[1]
    else:
        asc = -font.font.getbbox(clean, anchor="ls")[1] \
            if clean.strip() else font.font.getmetrics()[0]
    baseline = y0 + asc
    for font_used, chunk in font._split(text):
        if font_used is None:
            # Emoji slot(s): paste the raster at the slot position, top
            # aligned with the current row.
            for ch in chunk:
                raster, w = font._raster(ch)
                if raster is not None:
                    draw._image.paste(raster, (int(x0), int(y0)), raster)
                x0 += w
        else:
            if "\n" not in chunk:
                if getattr(font, "_ligatures", False):
                    # OpenType feature shaping (--ligatures FEATURE): only
                    # the substituted (ligature) glyphs change, all normal
                    # chars stay drawn by the normal Pillow path.
                    ssub = _lig_substitute(
                        chunk, font._path, font._ligatures)
                    if ssub and ssub != chunk:
                        pua_font = _lig_pua_font(font._path, font.size)
                        if pua_font is not None:
                            draw.text((x0, baseline), ssub,
                                      font=pua_font, fill=fill,
                                      anchor="ls",
                                      stroke_width=stroke_width,
                                      stroke_fill=stroke_fill)
                            x0 += pua_font.getlength(ssub)
                            continue
                    draw.text((x0, baseline), chunk, font=font_used,
                              fill=fill, anchor="ls",
                              stroke_width=stroke_width,
                              stroke_fill=stroke_fill)
                    x0 += font_used.getlength(chunk)
                else:
                    draw.text((x0, baseline), chunk, font=font_used,
                              fill=fill, anchor="ls",
                              stroke_width=stroke_width,
                              stroke_fill=stroke_fill)
                    x0 += font_used.getlength(chunk)
            else:
                # Safety: real newlines in a chunk (API misuse): draw each
                # line on its own baseline without an anchor.
                for ln in chunk.split("\n"):
                    if ln:
                        draw.text((x0, baseline), ln, font=font_used,
                                  fill=fill,
                                  stroke_width=stroke_width,
                                  stroke_fill=stroke_fill)
                        x0 += font_used.getlength(ln)
                    baseline += font_used.getlength("A")


def set_args():
    """
    Similar to parse_args() in labelmaker, with the addition of
    two other parameters and some change in the help.
    """
    p = argparse.ArgumentParser()
    p.add_argument(
        'comport',
        metavar='COM_PORT',
        nargs='?',
        help='Printer COM port, or "bt:NAME" for native Bluetooth.'
    )
    p.add_argument(
        '--list-bt',
        help='List paired Bluetooth devices supporting RFCOMM/SPP and exit.',
        action='store_true'
    )
    p.add_argument(
        '--gui',
        help='Launch the Tkinter GUI instead of printing from the command '
             'line. COM_PORT/FONT_NAME/TEXT_TO_PRINT are ignored. The GUI '
             'has its own status panel, so console progress messages are '
             'suppressed.',
        action='store_true',
        default=False,
    )
    p.add_argument(  
        '--fixed-width',  
        type=int,  
        default=None,  
        metavar='MILLIMETERS',  
        help='Pad label to exact width in mm (adds whitespace if text is shorter).'  
    )
    p.add_argument(  
        '--fixed-font-size',  
        type=int,  
        metavar='SIZE',  
        help='Use fixed font size (disables auto-sizing to fit printable area)'  
    )
    p.add_argument(
        'fontname',
        metavar='FONT_NAME',
        nargs='?', default='arial.ttf',
        help='Pathname of the used TrueType or OpenType font.'
    )
    p.add_argument(
        'text_to_print',
        metavar='TEXT_TO_PRINT',
        nargs='*',
        help='Text to be printed. UTF8 characters are accepted. Use \\n for line breaks.'
    )
    p.add_argument(
        '-u', '--unicode',
        help='Use Unicode escape sequences in TEXT_TO_PRINT.',
        action='store_true'
    )
    p.add_argument(
        '-l', '--lines',
        help='Add horizontal lines for drawing area (dotted red) and tape (cyan).',
        action='store_true'
    )
    p.add_argument(
        '-s', '--show',
        help='Show the created image. (If also using -n, terminate.)',
        action='store_true'
    )
    p.add_argument(
        '-c', '--show-conv',
        help='Show the converted image. (If also using -n, terminate.)',
        action='store_true'
    )
    p.add_argument(
        '-i', '--image',
        metavar='FILE_NAME',
        help='Image file to print. If this option is used (legacy mode), TEXT_TO_PRINT and FONT_NAME are ignored.'
    )
    p.add_argument(
        '-M', '--merge',
        metavar='FILE_NAME',
        action='append',
        help='Merge the image file before the text. Can be used multiple times.'
    )
    p.add_argument(
        '-R', '--resize',
        type=float,
        metavar='FLOAT',
        help='With image merge, additionaly resize it (floating point number).',
        default = 1.0
    )
    p.add_argument(
        '-X', '--x-merge',
        type=int,
        metavar='DOTS',
        help='With image merge, shift right the image of X dots.',
        default = 0
    )
    p.add_argument(
        '-Y', '--y-merge',
        metavar='DOTS',
        type=int,
        help='With image merge, shift down the image of Y dots.',
        default = 12
    )
    p.add_argument(
        '--merge-gap',
        metavar='DOTS',
        type=int,
        help='Horizontal gap (in dots) between a merged image and the text.',
        default = 0
    )
    p.add_argument(
        '-S', '--save',
        metavar='FILE_NAME',
        help='Save the produced image to a PNG file.'
    )
    p.add_argument(
        '--save-conv',
        metavar='FILE_NAME',
        help='Save the converted (rasterized) image sent to the printer'
             ' to a PNG file.',
    )
    p.add_argument(
        '-n', '--no-print',
        help='Only configure the printer and send the image but do not send print command.',
        action='store_true'
    )
    p.add_argument(
        '-F', '--no-feed',
        help='Disable feeding at the end of the print (chaining).',
        action='store_true'
    )
    p.add_argument(
        '-a', '--auto-cut',
        help='Enable auto-cutting (or print label boundary on e.g. PT-P300BT).',
        action='store_true'
    )
    p.add_argument(
        '-m', '--end-margin',
        metavar='DOTS',
        help='End margin (in dots).',
        default=0,
        type=int
    )
    p.add_argument(
        '-r', '--raw',
        help='Send the image to printer as-is without any pre-processing.',
        action='store_true'
    )
    p.add_argument(
        '-C', '--nocomp',
        help='Disable compression.',
        action='store_true'
    )
    p.add_argument(
        '--fill-color',
        dest="fill",
        help='Fill color for the text (e.g., "white"; default = "black").',
        default="black",
    )
    p.add_argument(
        '--stroke-fill',
        help='Stroke Fill color for the text (e.g., "black"; default = None).',
        default=None,
    )
    p.add_argument(
        '--stroke-width',
        help='Width of the text stroke (e.g., 1 or 2).',
        type=int,
        default=0,
    )
    p.add_argument(
        '--text-size',
        help='Horizontally stretch the text to fit the specified size.',
        metavar='MILLIMETERS',
        type=int,
        default=None,
    )
    p.add_argument(
        '--font-scale',
        type=float,
        default=None,
        metavar='NUMBER',
        help='Scale font size by specified percentage (default: 100%%)'
    )
    p.add_argument(
        '--h-padding',
        type=int,
        default=5,
        metavar='DOTS',
        help='Define custom left and right horizontal padding in pixels'
        ' (default: 5 pixels left and 5 pixels right)'
    )
    p.add_argument(
        '--v-shift',
        type=int,
        default=0,
        metavar='DOTS',
        help='Define relative vertical traslation in pixels'
        ' (default is to vertically center the font)'
    )
    p.add_argument(
        '-p',
        '--line-spacing',
        type=float,
        default=1.2,
        metavar='MULTIPLIER',
        help='Line spacing multiplier for multi-line text (default: 1.2)'
    )
    p.add_argument(
        '-H',
        '--center-text',
        help='Horizontally center text inside the label image.',
        action='store_true'
    )
    p.add_argument(
        '--tape-width',
        type=float,
        metavar='MILLIMETERS',
        default=12.0,
        help='Printable tape width in mm (default: 12). The PT-P300BT prints '
             'on 12 mm tape with a fixed raster; values below 12 (e.g. 6, 9) '
             'shrink the printable band the text is auto-sized to, keeping the '
             '128 px raster compatible with the device.'
    )
    p.add_argument(
        '--emoji-print-area',
        help='Size emoji to fill the printable area (64 px, like merged'
             ' images) instead of their line height.',
        action='store_true',
        default=False,
    )
    p.add_argument(
        '--uniform-font',
        help='Use the same font size regardless of the specific text: the'
             ' auto-fit measures a standard sample ("Ag", covering ascents'
             ' and descents) instead of the actual letters. All texts with'
             ' the same number of lines then get the same size, e.g. "cao"'
             ' and "ciao".',
        action='store_true',
        default=False,
    )
    p.add_argument(
        '--ligatures',
        help='Enable a GSUB feature of the selected font (requires '
             'uharfbuzz). The feature name is the OpenType tag, e.g. '
             '"calt" (contextual alternates, arrows in Fira Code), "liga" '
             '(standard ligatures) or "dlig" (discretionary ligatures). '
             'Repeat the option to enable several features. Text is '
             'shaped with HarfBuzz but only the substituted (ligature) '
             'glyphs are drawn differently; all normal characters stay '
             'exactly as they are. Use --list-ligatures to see the '
             'features the font actually exposes.',
        metavar='FEATURE',
        action='append',
        default=None,
    )
    p.add_argument(
        '--list-ligatures',
        help='List the OpenType GSUB features of the selected font and '
             'exit. Use a tag from the list with --ligatures.',
        metavar='FONT_NAME',
        nargs='?',
        const='arial.ttf',
        default=None,
    )
    p.add_argument(
        '--luma-lo',
        help='Monochrome emoji cut: luminance below this value becomes '
             'solid black ink (default: 140).',
        type=float,
        metavar='NUMBER',
        default=140.0,
    )
    p.add_argument(
        '--luma-hi',
        help='Monochrome emoji cut: luminance above this value becomes '
             'transparent (default: 175). Values between LO and HI fade '
             'with antialiasing.',
        type=float,
        metavar='NUMBER',
        default=175.0,
    )
    p.add_argument(
        '--mono-emoji',
        help='Render emoji as black ink (--mono-emoji, default) instead of '
             'in their embedded colors (--no-mono-emoji). The thermal '
             'print is 1-bit, so mono matches what actually prints.',
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    p.add_argument(
        '--tab-width',
        help='Number of spaces a TAB character expands to (default: 8).',
        type=int,
        metavar='SPACES',
        default=8,
    )
    p.add_argument(
        '--white-level',
        help='Minimum pixel value to consider it "white" when'
        ' cropping the image. Set it to a value close to 255. (Default: 240)',
        metavar='NUMBER',
        type=int,
        default=240,
    )
    p.add_argument(
        '--threshold',
        help='Custom thresholding when converting the image to binary, to'
        ' manually decide which pixel values become black or white'
        ' (Default: 75)',
        metavar='NUMBER',
        type=int,
        default=75,
    )
    return p


def process_image(image_path, resize, white_level, target_height):
    # Determines if the image is a PDF and converts it to PNG if necessary
    if image_path.lower().endswith('.pdf'):
        image_path = convert_pdf(image_path)  # Sends image_path to convert_pdf() and returns the output_filename

    # Open the image
    img = Image.open(image_path)
    
    # Convert the image to RGBA to ensure it has an alpha channel
    img = img.convert("RGBA")
    pixels = img.load()

    # Create a new white background image with the same size as the original
    white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    
    # Paste the original image onto the white background
    white_background.paste(img, (0, 0), img)
    
    # Now 'white_background' has no transparency (transparency is replaced by white)
    img = white_background
    
    # Convert the image to grayscale
    img = img.convert("L")  # "L" mode is for grayscale images
    
    # Get image dimensions
    width, height = img.size

    # Initialize the bounding box coordinates
    left, top, right, bottom = width, height, 0, 0
    
    # Iterate over each pixel to find the bounding box of non-white pixels
    for y in range(height):
        for x in range(width):
            pixel = img.getpixel((x, y))
            
            # White pixels in grayscale have a value close to 255
            if pixel < white_level:  # Consider pixels that are not white
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    
    # Crop the image to the bounding box
    if right > left and bottom > top:
        cropped_img = img.crop((left, top, right + 1, bottom + 1))
        
        # Get the size of the cropped image
        cropped_width, cropped_height = cropped_img.size
        
        # Calculate the new width to maintain the aspect ratio with target height
        aspect_ratio = cropped_width / cropped_height
        new_width = int(target_height * aspect_ratio)
        
        # Resize the image to target height while maintaining aspect ratio
        return cropped_img.resize(
            (int(new_width * resize), int(target_height * resize)),
            Image.Resampling.LANCZOS
        )
    else:
        print("No content detected to crop.")
    return None


def convert_pdf(filename):
    # Converts the first page of a PDF to a PNG, returns PNG
    output_filename = filename.replace('.pdf', '.png')
    images = convert_from_path(filename, dpi=300, first_page=1, last_page=1) # used defaults, 300dpi may even be overkill for labels
    images[0].save(output_filename, "PNG")
    return output_filename


def calculate_multiline_dimensions(lines, font, line_spacing):
    """Calculate the total width and height needed for multiline text"""
    max_width = 0
    line_heights = []
    # Use the same sample as draw_multiline_text for line height
    sample_bbox = font.getbbox("".join(lines), anchor="lt")
    base_line_height = sample_bbox[3] - sample_bbox[1]
    line_spacing_pixels = base_line_height * line_spacing
    n_lines = len(lines)
    for line in lines:
        bbox = font.getbbox(line, anchor="lt")
        line_width = bbox[2] - bbox[0]
        max_width = max(max_width, line_width)
        line_heights.append(base_line_height)
    total_height = 0
    for i in range(n_lines):
        total_height += base_line_height
        if n_lines > 1 and i < n_lines - 1:
            total_height += (line_spacing_pixels - base_line_height)
    return max_width, int(round(total_height)), line_heights


def draw_multiline_text(
    draw, text_lines, x, y, font, fill,
    stroke_width, stroke_fill, line_spacing, center_text, image_width
):
    """Draw multiple lines of text with proper spacing"""
    if not text_lines:
        return

    sample_bbox = font.getbbox("".join(text_lines), anchor="lt")
    base_line_height = sample_bbox[3]
    line_spacing_pixels = base_line_height * line_spacing

    current_y = y
    for line in text_lines:
        if line.strip():
            if center_text:
                # Same centering measurement as the original algorithm.
                # (PIL's textbbox delegates to font.getbbox, so this works
                # transparently with the emoji-aware _MixedFont wrapper.)
                _, _, line_width, _ = draw.textbbox(
                    (0, 0), line, font=font, stroke_width=stroke_width
                )
                x_pos = (image_width - line_width) // 2
            else:
                x_pos = x
            # Same as the original draw.text (anchor "lt", same args).
            # _draw_text_mixed is transparent for plain text (identical
            # pixels to draw.text); for emoji it draws the text glyphs on
            # a shared baseline and pastes each emoji raster at its slot,
            # top-aligned with the current row (in-line height or
            # printable-band height).
            _draw_text_mixed(
                draw, (x_pos, current_y), line, font,
                fill=fill, anchor="lt",
                stroke_width=stroke_width, stroke_fill=stroke_fill,
            )
        current_y += line_spacing_pixels



def main():
    p = set_args()
    args = p.parse_args()
    if args.list_bt:
        _list_bt_devices()
        sys.exit(0)
    if args.list_ligatures is not None:
        # --list-ligatures [FONT_NAME]: print the GSUB features the font
        # exposes, without needing a printer or a COM port.
        font_path = args.list_ligatures
        try:
            from fontbrowser import find_font_path
            font_path = find_font_path(font_path)
        except Exception:
            pass
        feats = _lig_features(font_path)
        if not feats:
            print(f'No GSUB features found in "{font_path}" '
                  f'(or the font has no GSUB table).')
        else:
            print(f'GSUB features of "{font_path}":')
            print("  " + " ".join(feats))
            print("Use a tag with --ligatures, e.g. "
                  f'--ligatures {feats[0] if feats else "calt"}')
        sys.exit(0)
    if not args.comport:
        p.error("COM_PORT is required (or use --list-bt to list Bluetooth devices).")
    if not args.comport.startswith("bt:") and \
            args.comport not in [p.device for p in list_ports.comports()]:
        print("Port '" + args.comport + "' does not seem a valid serial communication port.")

    # Build the label image (returns None for legacy image mode handling)
    image = build_label(args)
    if image is None:
        return

    padded = rasterize_label(image, args)

    # Image save and show
    if args.save_conv:
        print(f'Saving converted image "{args.save_conv}".')
        padded.save(args.save_conv)
    if args.save:
        print(f'Saving image "{args.save}".')
        image.save(args.save)
        if args.no_print:
            return
    if args.show:
        try:
            image.show()
        except Exception as e:
            p.error("Cannot show image:" + repr(e))
        if not args.show_conv and args.no_print:
            return
    if args.show_conv:
        padded.show()
        if args.no_print:
            return

    data = padded.tobytes()

    # Send to the printer
    try:
        ser = open_printer(args.comport)
    except serial.SerialException:
        p.error(
            'Printer on Bluetooth serial port "'
            + args.comport
            + '" is unavailable or unreachable.'
        )
    except Exception as e:
        p.error(e)

    try:
        assert data is not None
        do_print_job(ser, args, data)
    finally:
        # Initialize (released state) and close the connection so the port
        # / RFCOMM channel is freed immediately.
        try:
            reset_printer(ser)
        finally:
            try:
                ser.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Reusable API (used by gui.py and other front-ends)
# --------------------------------------------------------------------------

def _list_bt_devices():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "native"))
    from btcommon import list_devices, BTError, platform_backend
    try:
        devices = list_devices()
    except BTError as e:
        print(str(e))
        sys.exit(1)
    if not devices:
        print("No paired Bluetooth devices found.")
        sys.exit(1)

    # Column widths (aligned, respecting wide/UTF-8 names)
    import unicodedata

    def _width(s):
        # Count East-Asian wide characters as 2 columns.
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                   for c in s)

    def _pad(s, w):
        return s + " " * max(0, w - _width(s))

    headers = ("DEVICE NAME", "ADDRESS / PORT", "CHANNEL")
    if platform_backend() == "windows":
        headers = ("DEVICE NAME", "ADDRESS", "COM PORT")
    rows = [(str(n), str(a), str(c)) for n, a, c in devices]
    widths = [max(_width(h), max((_width(r[i]) for r in rows), default=0))
              for i, h in enumerate(headers)]

    def _sep():
        print("+" + "+".join("-" * (w + 2) for w in widths) + "+")

    def _row(cells):
        print("| " + " | ".join(_pad(c, w)
                                for c, w in zip(cells, widths)) + " |")

    _sep()
    _row(headers)
    _sep()
    for r in rows:
        _row(r)
    _sep()
    print(f"{len(devices)} paired Bluetooth device(s). ")


def open_printer(comport):
    """Open a serial or native-Bluetooth ("bt:NAME"/"bt:MAC") connection."""
    if comport.startswith("bt:"):
        # Cross-platform native Bluetooth RFCOMM transport ("bt:NAME"
        # matches the paired device whose Bluetooth name contains NAME,
        # default PT-P300). Backends: IOBluetooth/PyObjC on macOS (the
        # /dev/cu.* bridge is unreliable there), the OS SPP/COM mapping on
        # Windows, and rfcomm/BlueZ on Linux. Falls back to the regular
        # serial.Serial path on each platform.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "native"))
        from btcommon import BTSerial
        name = comport[3:] or "PT-P300"
        return BTSerial(name=name, timeout=3)
    return serial.Serial(comport, timeout=10, write_timeout=10)


class _LabelError(Exception):
    """Raised in place of p.error()/sys.exit() when using the API."""


def build_label(args):
    """API: build the label image from parsed args. Returns a PIL Image.

    Raises _LabelError on invalid parameters (instead of exiting).
    """
    global _LIGATURES_GLOBAL, _LUMA_GLOBAL
    # GSUB feature tag(s), e.g. "calt" or "calt,liga": comma-joined list.
    lig = getattr(args, "ligatures", None) or []
    if isinstance(lig, str):
        lig = [lig]
    _LIGATURES_GLOBAL = ",".join(f for f in lig if f).strip(",")
    _LUMA_GLOBAL = (
        float(getattr(args, "luma_lo", 140.0) or 140.0),
        float(getattr(args, "luma_hi", 175.0) or 175.0),
    )
    # Legacy -i/--image: print the given image as the whole label, ignoring text
    # and font. The image-building code below only ever ran in the non-legacy
    # branch, so -i used to leave `data` unset and crash; route it through the
    # (working) merge path with empty text instead.
    if args.image is not None:
        args.merge = [args.image] + (args.merge or [])
        args.text_to_print = []
        args.image = None
    data = None
    if args.image is None: # not using the legacy mode
        # The PT-P300BT prints on 12 mm TZe tape with a fixed 128 px raster
        # (the hardware does not physically support narrower tape). The
        # --tape-width option lets the author plan for a smaller printable
        # band (e.g. 6 / 9 mm): the text is auto-sized for the requested
        # band while the physical 128 px raster is kept, so the output is
        # still compatible with the device. Default 12 mm keeps the exact
        # original behaviour (printable area = 64 px for a 9 mm band).
        tape_mm = max(3.5, min(12.0, float(getattr(args, "tape_width", 12.0) or 12.0)))
        height_of_the_tape = 86  # 12 mm tape height in px
        # Printable area (px) scales with the tape width: the 12 mm tape
        # offers a 9 mm printable band (64 px), so smaller tapes get a
        # proportionally smaller band (6 mm -> 32 px, 9 mm -> 48 px).
        height_of_the_printable_area = int(
            round(64.0 * tape_mm / 12.0)
        )
        height_of_the_image = 88  # px (can be any value >= height_of_the_tape, but height_of_the_tape + 2 border lines is good)

        # Compute max TT font size to remain within height_of_the_printable_area
        font_size = 0
        font_height = 0
        font = None
        print_border = (height_of_the_image - height_of_the_printable_area) / 2
        emoji_mode = "print" if getattr(args, "emoji_print_area", False) \
            else "line"
        uniform = bool(getattr(args, "uniform_font", False))
        mono = bool(getattr(args, "mono_emoji", True))
        text = " ".join(args.text_to_print)
        if text:
            if args.unicode:
                text = text.encode().decode('unicode_escape')
            # Expand TAB characters to spaces (default 8, configurable),
            # so a tab never renders as a .notdef box.
            tab_w = max(1, int(getattr(args, "tab_width", 8) or 8))
            if "\t" in text:
                text = text.expandtabs(tab_w)
            
            # Check if text contains newlines to determine processing mode
            has_newlines = '\\n' in text

            if has_newlines:
                # Split text into lines for multiline processing
                text_lines = text.replace("\\n", "\n").split('\n')

                if args.fixed_font_size:
                    font_size = args.fixed_font_size  
                    font = _load_text_font(args.fontname, font_size, emoji_mode, uniform, mono)  
                    font_width, font_height, line_heights = calculate_multiline_dimensions(  
                        text_lines, font, args.line_spacing  
                    )  
                    if font_height > height_of_the_printable_area:  
                        print(f"Warning: fixed font size {font_size} exceeds printable area ({font_height} > {height_of_the_printable_area})")  
                else:
                    stop = False
                    while font_height != height_of_the_printable_area:
                        if font_height > height_of_the_printable_area:
                            # Try to slightly decrease line_spacing
                            if len(text_lines) > 1:
                                min_spacing = args.line_spacing * 0.9  # Don't go below this multiplier
                                spacing_step = 0.01
                                new_spacing = args.line_spacing
                                found_fit = False
                                while new_spacing > min_spacing:
                                    new_spacing -= spacing_step
                                    font_width2, font_height2, _ = calculate_multiline_dimensions(
                                        text_lines, font, new_spacing
                                    )
                                    if font_height2 == height_of_the_printable_area:
                                        # Found a fit, update spacing and dimensions
                                        args.line_spacing = new_spacing
                                        print(
                                            f"Line spacing has been slightly decreased "
                                            f"to fit the printable area. Used value: {new_spacing:.2f}."
                                        )
                                        font_width, font_height = font_width2, font_height2
                                        found_fit = True
                                        break
                                if found_fit:
                                    break
                            # If not multiline or can't fit by spacing, decrease font size
                            font_size -= 1
                            stop = True
                        else:
                            font_size += 1
                        try:
                            font = _load_text_font(args.fontname, font_size, emoji_mode, uniform, mono)
                        except Exception as e:
                            raise _LabelError(
                                f'Cannot load font "{args.fontname}" - {e}')
                        # Calculate dimensions for multiline text
                        font_width, font_height, line_heights = calculate_multiline_dimensions(
                            text_lines, font, args.line_spacing
                        )
                        
                        if stop:
                            print(
                                "The max height of this text with font "
                                f'"{args.fontname}" is {font_height} dots'
                                f' instead of {height_of_the_printable_area}.')
                            break

                # The fit measured the text only: rebuild the font with the
                # emoji sized for its row (line) or the printable band
                # (print), so the image width accounts for the emoji and
                # the font used for drawing is the emoji-aware one. In
                # line mode the emoji is sized to ONE text row (the line
                # height), not to the whole block height.
                if not args.font_scale:
                    font = _load_text_font(args.fontname, font_size, emoji_mode, uniform, mono)
                    if emoji_mode == "line":
                        font.set_emoji_line_height(line_heights[0])
                    else:
                        font.set_emoji_band(print_border)
                    font_width, font_height, line_heights = calculate_multiline_dimensions(
                        text_lines, font, args.line_spacing
                    )

                y_position = print_border
                if args.font_scale:
                    scaled_font_size = int(
                        round(font_size * (args.font_scale / 100.0))
                    )
                    try:
                        font = _load_text_font(args.fontname, scaled_font_size, emoji_mode, uniform, mono)
                    except Exception as e:
                        raise _LabelError(
                            f'Cannot load font "{args.fontname}" - {e}')
                    # Recalculate dimensions with the scaled font to get the
                    # scaled line height, then size the emoji for it.
                    font_width, font_height, line_heights = calculate_multiline_dimensions(
                        text_lines, font, args.line_spacing
                    )
                    if emoji_mode == "line":
                        font.set_emoji_line_height(line_heights[0])
                    else:
                        font.set_emoji_band(print_border)
                    font_width, font_height, line_heights = calculate_multiline_dimensions(
                        text_lines, font, args.line_spacing
                    )

                    # Vertically center text
                    y_position = print_border + (
                        height_of_the_printable_area - font_height
                    ) // 2

                # Band mode: the emoji strip (all emoji of all lines, laid out
                # left to right) goes on the left like merged images; the
                # text block shifts right past it.
                emoji_strip_w = 0
                if getattr(font, "_emoji_band", False):
                    emoji_strip_w = max(
                        (font.band_width(l) for l in text_lines),
                        default=0)

                # Create a drawing context for the image
                image = Image.new(
                    "RGB",
                    (
                        emoji_strip_w + font_width + args.h_padding * 2
                        + 1 + args.end_margin,
                        height_of_the_image
                    ),
                    "white"
                )
                draw = ImageDraw.Draw(image)
                if emoji_strip_w:
                    # Paste the emoji strip (band position, like images).
                    x_strip = args.h_padding
                    for l in text_lines:
                        for ch in l:
                            if font.is_emoji(ch):
                                raster, w = font._raster(ch)
                                if raster is not None:
                                    draw._image.paste(
                                        raster,
                                        (int(x_strip),
                                         int(print_border + args.v_shift)),
                                        raster)
                                x_strip += w
                text_x = args.h_padding + emoji_strip_w
                try:
                    # Draw multiline text
                    draw_multiline_text(
                        draw, text_lines, text_x, y_position + args.v_shift,
                        font, args.fill, args.stroke_width, args.stroke_fill, args.line_spacing,
                        args.center_text, image.width
                    )
                except Exception as e:
                    raise _LabelError(f"Invalid parameter: {e}")
                
                if args.text_size:
                    text_size = (
                        int(args.text_size / 0.149)
                        - args.h_padding
                        - args.end_margin
                    )  # mm to dot
                    
                    # For multiline text, use the width of the widest line for scaling
                    scale_factor = font_width / text_size
                    image = image.transform(
                        (text_size + args.end_margin + args.h_padding, height_of_the_image),
                        Image.Transform.AFFINE,
                        (scale_factor, 0, 0, 0, 1, 0),
                    )
                    while image.getpixel((image.width - 1, 0)) == (0, 0, 0):
                        crop_box = (0, 0, image.width - 1, height_of_the_image)
                        image = image.crop(crop_box)
                    draw = ImageDraw.Draw(image)
            else:
                # Single-line processing
                if args.fixed_font_size:  
                    font_size = args.fixed_font_size  
                    font = _load_text_font(args.fontname, font_size, emoji_mode, uniform, mono)  
                    font_width, font_height = font.getbbox(text, anchor="lt")[2:]  
                    if font_height > height_of_the_printable_area:  
                        print(f"Warning: fixed font size {font_size} exceeds printable area ({font_height} > {height_of_the_printable_area})")  
                else:
                    stop = False
                    while font_height != height_of_the_printable_area:
                        if font_height > height_of_the_printable_area:
                            font_size -= 1
                            stop = True
                        else:
                            font_size += 1
                        try:
                            font = _load_text_font(args.fontname, font_size, emoji_mode, uniform, mono)
                        except Exception as e:
                            raise _LabelError(
                                f'Cannot load font "{args.fontname}" - {e}')
                        font_width, font_height = font.getbbox(text, anchor="lt")[2:]
                        if stop:
                            print(
                                "The max height of this text with font "
                                f'"{args.fontname}" is {font_height} dots'
                                f' instead of {height_of_the_printable_area}.')
                            break

                # The fit measured the text only: rebuild the font with the
                # emoji sized for its row (line) or the printable band
                # (print), so the image width accounts for the emoji and
                # the font used for drawing is the emoji-aware one.
                if not args.font_scale:
                    font = _load_text_font(args.fontname, font_size, emoji_mode, uniform, mono)
                    if emoji_mode == "line":
                        font.set_emoji_line_height(font_height)
                    else:
                        font.set_emoji_band(print_border)
                    font_width, font_height = font.getbbox(text, anchor="lt")[2:]

                y_position = print_border
                if args.font_scale:
                    scaled_font_size = int(
                        round(font_size * (args.font_scale / 100.0))
                    )
                    try:
                        font = _load_text_font(args.fontname, scaled_font_size, emoji_mode, uniform, mono)
                    except Exception as e:
                        raise _LabelError(
                            f'Cannot load font "{args.fontname}" - {e}')
                    if emoji_mode == "line":
                        font.set_emoji_line_height(font_height)
                    else:
                        font.set_emoji_band(print_border)
                    font_width, font_height = font.getbbox(text, anchor="lt")[2:]

                    # Vertically center text
                    y_position = print_border + (
                        height_of_the_printable_area - font_height
                    ) // 2

                # Band mode: the emoji strip goes on the left like merged images;
                # the text shifts right past it.
                emoji_strip_w = 0
                if getattr(font, "_emoji_band", False):
                    emoji_strip_w = font.band_width(text)

                # Create a drawing context for the image
                image = Image.new(
                    "RGB",
                    (
                        emoji_strip_w + font_width + args.h_padding * 2
                        + 1 + args.end_margin,
                        height_of_the_image
                    ),
                    "white"
                )
                draw = ImageDraw.Draw(image)
                if emoji_strip_w:
                    x_strip = args.h_padding
                    for ch in text:
                        if font.is_emoji(ch):
                            raster, w = font._raster(ch)
                            if raster is not None:
                                draw._image.paste(
                                    raster,
                                    (int(x_strip),
                                     int(print_border + args.v_shift)),
                                    raster)
                            x_strip += w
                text_x = args.h_padding + emoji_strip_w
                try:
                    _draw_text_mixed(
                        draw, (text_x, y_position + args.v_shift),
                        text, font,
                        fill=args.fill,
                        anchor="lt",
                        stroke_width=args.stroke_width,
                        stroke_fill=args.stroke_fill
                    )
                except Exception as e:
                    raise _LabelError(f"Invalid parameter: {e}")
                if args.text_size:
                    text_size = (
                        int(args.text_size / 0.149)
                        - args.h_padding
                        - args.end_margin
                    )  # mm to dot
                    b = font.getbbox(text, anchor="lt")
                    text_width = (b[2] - b[0]) if b else 0
                    text_height = (b[3] - b[1]) if b else 0
                    scale_factor = text_width / text_size
                    image = image.transform(
                        (text_size + args.end_margin + args.h_padding, height_of_the_image),
                        Image.Transform.AFFINE,
                        (scale_factor, 0, 0, 0, 1, 0),
                    )
                    while image.getpixel((image.width - 1, 0)) == (0, 0, 0):
                        crop_box = (0, 0, image.width - 1, height_of_the_image)
                        image = image.crop(crop_box)
                    draw = ImageDraw.Draw(image)
        else:  # null image
            image = Image.new(
                "RGB",
                (0, height_of_the_image),
                "white"
            )
            draw = ImageDraw.Draw(image)

        if not args.fixed_font_size:  
            print("Font size determined:", font_size)
        if args.merge:
            for i in reversed(args.merge):
                loaded_image = process_image(
                    i,
                    args.resize,
                    white_level=args.white_level,
                    target_height=height_of_the_printable_area
                )
                if not loaded_image:
                    raise _LabelError(f'Invalid image "{i}"')
                gap = args.merge_gap if image.width else 0
                dst = Image.new(
                    "RGB",
                    (loaded_image.width + gap + image.width, height_of_the_image),
                    "white"
                )
                dst.paste(loaded_image, (args.x_merge, args.y_merge))
                dst.paste(image, (loaded_image.width + gap, 0))
                image = dst
            # Convert the image to binary
            draw = ImageDraw.Draw(image)

        if args.fixed_width:  
            target_width_dots = int(round(args.fixed_width / 0.149))  
            current_width = image.width  
            if current_width < target_width_dots:  
                # Create a new white image of target width and paste the existing image
                # centered when -H/--center-text was requested, otherwise left-aligned.
                padded_image = Image.new("RGB", (target_width_dots, image.height), "white")  
                if args.center_text:
                    x_offset = (target_width_dots - current_width) // 2
                else:
                    x_offset = 0  
                padded_image.paste(image, (x_offset, 0))  
                image = padded_image

        if args.lines:
            # Draw ruler (in)
            draw.text(
                (0, 1), "in",
                anchor="la",
                fill="magenta"
            )
            x = -1
            i = 0
            while x < image.width:
                if x > 0:
                    draw.line(  # top
                        (
                            int(x), print_border - (4 if i % 4 else 9),
                            int(x), print_border - 2
                        ),
                        fill="magenta", width=2
                    )
                x += 43.18
                i += 1
            # Draw ruler (cm)
            draw.text(
                (0, 76), "cm",
                anchor="la",
                fill="magenta"
            )
            x = -1
            i = 0
            while x < image.width:
                if x > 0:
                    draw.line(
                        (
                            int(x), height_of_the_image - print_border + 1,
                            int(x), height_of_the_image - print_border
                            + (5 if i % 10 else 9)
                        ),
                        fill="magenta", width=2
                    )
                x += 68
                i += 1
            # Draw a dotted horizontal line over the top border and below the bottom border of the printable area
            for x in range(0, image.width, 5):
                draw.line(  # top
                    (x, print_border - 1, x + 1, print_border - 1),
                    fill="red", width=1
                )
                draw.line(
                    (  # bottom
                        x, height_of_the_image - print_border,
                        x + 1, height_of_the_image - print_border
                    ),
                    fill="red", width=1
                )
            # Draw a cyan line showing the tape borders
            tape_border = int((height_of_the_image - height_of_the_tape) / 2)
            if tape_border > 0:
                draw.line(
                    (0, tape_border - 1, image.width, tape_border - 1),
                    fill="cyan", width=1
                )
                draw.line(
                    (
                        0, height_of_the_image - tape_border,
                        image.width, height_of_the_image - tape_border
                    ),
                    fill="cyan", width=1
                )

        if not image.tobytes():
            raise _LabelError("Null image generated.")
        return image


def rasterize_label(image, args):
    """API: convert the label image into the raster sent to the printer.

    Returns the padded '1'-mode PIL image (128 x length).
    """
    if args.image is not None:
        raise _LabelError("Legacy image mode is handled in build_label().")

    # Convert to greyscale and rotate/invert/mirror the image
    rotated_image = ImageOps.invert(
        image.convert('L', dither=Image.Dither.FLOYDSTEINBERG)
        .rotate(-90, expand=True, resample=Image.BICUBIC)
    )
    rotated_image = ImageOps.mirror(rotated_image)

    # Manual binarization with a threshold (smoother control of artifacts)
    bin_image = rotated_image.point(lambda p: p > args.threshold and 255)

    # Convert to '1' mode (binary image)
    binary_img = bin_image.convert('1')

    # Add padding to increase the height from height_of_the_image to 128
    # (similar to the last part of read_png() code in labelmaker_encode.py)
    w, h = binary_img.size
    padded = Image.new('1', (128, h))
    x, y = (128 - w) // 2, 0
    nw, nh = x + w, y + h
    padded.paste(binary_img, (x, y, nw, nh))

    # Compute tape length and print duration
    print_length = padded.size[1] * 0.149  # mm
    print(
        "Length of the printed tape:",
        "%.1f" % (print_length / 10),
        "cm = %.1f" % (print_length / 10 / 2.54),
        "in, printed in",
        "%.1f" % (print_length / 20),
        "sec."
    )
    print_length += (25 + 1)  # 2.5 cm of wasted tape before, 1 mm after
    print(
        "Length of the used tape (adding header and footer):",
        "%.1f" % (print_length / 10),
        "cm = %.1f" % (print_length / 10 / 2.54),
        "in, printed in",
        "%.1f" % (print_length / 20),
        "sec."
    )

    # Check max tape length
    if print_length > 499:
        raise _LabelError("Print length exceeding 49.9 cm = 19.6 in")

    return padded


def gui_entry():
    """Launch the Tkinter GUI.

    The GUI has its own status/log panel, so informational progress
    messages (font size, tape length, print duration, fit warnings) are
    suppressed instead of cluttering the console. Real errors raised as
    _LabelError are still surfaced by the GUI's own error handling.
    """
    import contextlib
    import io as _io
    try:
        with contextlib.redirect_stdout(_io.StringIO()):
            from ptgui import LabelGUI
            LabelGUI().mainloop()
    finally:
        pass

def _run_gui():
    """Entry point used by '--gui' (and by app launchers): parse args
    (so the help/validation is consistent) and start the GUI."""
    _parser = set_args()
    _args = _parser.parse_args()
    if _args.gui:
        gui_entry()
    else:
        main()


if __name__ == "__main__":
    _run_gui()

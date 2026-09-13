"""Cross-platform system font discovery for the PT-P300BT GUI.

Returns usable .ttf/.otf/.ttc file paths that Pillow's ImageFont can load,
independent of the operating system.
"""

import os
import sys

_FONT_EXTS = (".ttf", ".otf", ".ttc")

# Directories to scan per platform, in priority order.
def _font_dirs():
    home = os.path.expanduser("~")
    dirs = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs += [
            os.path.join(windir, "Fonts"),
            os.path.join(home, "AppData", "Local", "Microsoft", "Windows",
                         "Fonts"),
        ]
    elif sys.platform == "darwin":
        dirs += [
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
            "/Library/Fonts",
            os.path.join(home, "Library", "Fonts"),
        ]
    else:  # Linux and other Unix-likes
        dirs += [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.join(home, ".fonts"),
            os.path.join(home, ".local", "share", "fonts"),
        ]
    return [d for d in dirs if os.path.isdir(d)]


def list_system_fonts():
    """Return sorted [(family_label, full_path), ...] of system fonts.

    `family_label` is derived from the filename (e.g. "Arial Bold") and is
    deduplicated: for each label only the first path found is kept.
    """
    import re
    fonts = {}
    for d in _font_dirs():
        for root, _dirs, files in os.walk(d):
            for f in files:
                if not f.lower().endswith(_FONT_EXTS):
                    continue
                path = os.path.join(root, f)
                label = _humanize(f)
                if label and label not in fonts:
                    fonts[label] = path
    return sorted(fonts.items(), key=lambda kv: kv[0].lower())


def _humanize(filename):
    """'arialbd.ttf' -> 'Arial Bold'; '_-
' stripped; 'uidereg.ttf' etc. skipped."""
    import re
    stem = os.path.splitext(filename)[0]
    # Skip obvious non-text/system fonts
    if re.match(r"^(web|svg|icon|marlett|symbol|wingding|desktop)", stem,
                re.IGNORECASE):
        return None
    # Common style suffixes (Windows naming)
    style_map = [
        (r"bolditalic$", " Bold Italic"),
        (r"(?:bd)?bi$", " Bold Italic"),
        (r"bold$", " Bold"),
        (r"(?:bd)$", " Bold"),
        (r"italic$", " Italic"),
        (r"it$", " Italic"),
        (r"light$", " Light"),
        (r"black$", " Black"),
        (r"medium$", " Medium"),
        (r"semibold$", " SemiBold"),
        (r"extracn$", " Extra Condensed"),
        (r"cond$", " Condensed"),
        (r"cn$", " Condensed"),
    ]
    suffix = ""
    for pat, label in style_map:
        m = re.search(pat, stem, re.IGNORECASE)
        if m and stem.isascii():
            # Only accept the suffix if what precedes it is a word boundary
            # (avoids stripping 'bi' from e.g. 'calibri').
            if m.start() > 0 and stem[m.start() - 1].isalpha() and \
                    len(stem) - m.start() == len(m.group(0)) and \
                    not stem[m.start() - 1].isupper():
                continue
            suffix = label
            stem = stem[:m.start()]
            break
    # Insert spaces at camel-case boundaries and around digits
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)
    name = name.replace("_", " ").replace("-", " ").strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        return None
    # Title-case each word, preserving short uppercase words
    words = []
    for w in name.split():
        words.append(w if len(w) <= 2 and w.isupper() else w.capitalize())
    return " ".join(words) + suffix


def find_font_path(name):
    """Resolve a bare name like 'arial.ttf' to a full system path if found."""
    if os.path.isabs(name) and os.path.exists(name):
        return name
    for d in _font_dirs():
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
        for root, _dirs, files in os.walk(d):
            if name.lower() in (f.lower() for f in files):
                return os.path.join(root, name)
    return name  # return as-is; Pillow may resolve via its own search

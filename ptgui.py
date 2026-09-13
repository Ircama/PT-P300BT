"""Tkinter GUI for printlabel.py"""

import argparse
import os
import sys
import threading
import tkinter as tk
import unicodedata
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import printlabel  # noqa: E402  (imports Pillow etc. at module scope)

APP_TITLE = "PT-P300BT Label Designer"
MAX_ZOOM = 2.0  # maximum zoom factor (200%)

FONT_CHOICES = [
    "arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf",
    "cour.ttf", "times.ttf", "comic.ttf", "comicbd.ttf",
    "georgia.ttf", "impact.ttf", "segoeui.ttf", "tahoma.ttf",
    "verdana.ttf", "calibri.ttf", "consola.ttf",
]
MAC_FONT_CHOICES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
]


def _text_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


class Tooltip:
    """Lightweight hover tooltip with a delay and optional help key.

    Shows extended help text (possibly multi-line) in a floating Toplevel
    near the widget. One instance per widget keeps it simple and cheap.
    """

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip is not None:
            return
        w = self.widget
        x = w.winfo_rootx() + 14
        y = w.winfo_rooty() + w.winfo_height() + 4
        tip = tk.Toplevel(w)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        # A single label: the whole help text stays in one contiguous box
        # (multi-line via \n inside the label), never a stack of separate
        # labels that look detached and interfere with scrolling.
        ttk.Label(tip, text=self.text, background="#ffffe0",
                  foreground="#000000", relief="solid",
                  borderwidth=1, padding=(6, 3),
                  justify="left").pack()
        self._tip = tip

    def _hide(self, _event=None):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# Extended contextual help (English) shown as a tooltip on hover over any
# configuration control. Keys match the `key` used for self.vars plus a few
# group-level helpers.
HELPS = {
    "fontname": (
        "Font file used for the label text.\n"
        "Full path to a TrueType/OpenType font (*.ttf, *.otf, *.ttc).\n"
        "A bare file name is resolved against the system font folders.\n"
        "Use the picker (\"...\") or the font browser (\"Browse...\") to "
        "choose visually."),
    "text": (
        "Label text, one line per row.\n"
        "Each newline becomes a separate label line (same as \"\\n\" in the "
        "CLI).\n"
        "Keep the total print length under 49.9 cm."),
    "center_text": (
        "Center each label line horizontally (-H).\n"
        "Off: lines are left-aligned. On: lines are centered on the tape."),
    "emoji_print_area": (
        "Size emoji to fill the printable area (--emoji-print-area).\n"
        "On: emoji are scaled to fill the full 9 mm print height, the same "
        "way merged images are sized.\n"
        "Off: emoji keep their line height, sized to the row they are on."),
    "mono_emoji": (
        "Show emoji in black and white.\n"
        "On (default): emoji render as black ink in the text box, in the "
        "preview and on the print — exactly what the 1-bit thermal printer "
        "produces.\n"
        "Off: emoji keep their embedded colors on screen (the printed "
        "result is still 1-bit black)."),
    "uniform_font": (
        "Uniform font sizing (--uniform-font).\n"
        "On: the auto-fit measures a standard sample (\"Ag\", covering "
        "ascents and descents) instead of the actual text, so all labels "
        "with the same number of lines use the same font size.\n"
        "Useful when printing a batch where \"cao\" should not come out "
        "larger than \"ciao\"."),
    "ligatures": (
        "OpenType GSUB feature (--ligatures FEATURE).\n"
        "Pick a feature tag that the selected font actually exposes \""
        "(use --list-ligatures on the command line to see them, e.g. \""
        "calt for the arrows of Fira Code, liga for standard ligatures, \""
        "dlig for discretionary ones). Text is shaped with HarfBuzz, but \""
        "only the substituted (ligature) glyphs are drawn differently: all \""
        "normal characters stay exactly as they are. Empty = off. \""
        "Requires uharfbuzz."),
    "luma_lo": (
        "Monochrome emoji cut, low (--luma-lo).\n"
        "Luminance below this value becomes solid black ink. "
        "Default 140."),
    "luma_hi": (
        "Monochrome emoji cut, high (--luma-hi).\n"
        "Luminance above this value becomes transparent (the white label "
        "shows through). Values between LO and HI fade smoothly. "
        "Default 175."),
    "tab_width": (
        "TAB width in spaces (--tab-width).\n"
        "TAB characters in the text are expanded to this many spaces "
        "(default 8) so they never print as a square box."),
    "font_scale": (
        "Font scale factor in percent.\n"
        "100 = the natural font size. Values < 100 shrink the text,\n"
        "values > 100 enlarge it. Scales proportionally per line."),
    "tape_width": (
        "Printable tape width in mm (default 12).\n"
        "The PT-P300BT prints on 12 mm tape with a fixed raster; values "
        "below 12 (e.g. 6, 9) shrink the printable band the text is "
        "auto-sized to, keeping the raster compatible with the device."),
    "line_spacing": (
        "Line spacing multiplier.\n"
        "1.0 = single spacing; 1.2 = 20% extra space between lines.\n"
        "Useful when multiple label lines must fit a fixed tape length."),
    "h_padding": (
        "Horizontal padding in dots (1 dot = 1/203 inch).\n"
        "Extra blank tape before the first pixel and after the last pixel."
        "Minimum is 0; values too large may exceed the 49.9 cm limit."),
    "v_shift": (
        "Vertical shift in dots.\n"
        "Moves the whole label up (negative = toward the top edge,\n"
        "positive = toward the bottom edge) in the 128-dot grid."),
    "text_size": (
        "Exact text width in mm (0 = auto).\n"
        "Force the label to be exactly this wide, scaling the font\n"
        "proportionally. Ignored when 0."),
    "fixed_width": (
        "Fixed tape length in mm (0 = off).\n"
        "Pad/shrink the label to exactly this many millimeters.\n"
        "Ignored when 0."),
    "fixed_font_size": (
        "Fixed font size in points (0 = auto).\n"
        "Use an exact font size instead of auto-fitting. Ignored when 0."),
    "fill": (
        "Text fill color (name or hex, e.g. black, #ff0000).\n"
        "Only pure black is truly dark on most tape colors; other colors "
        "print as grayscale."),
    "stroke_fill": (
        "Text outline color (name or hex; empty = no outline).\n"
        "Adds a stroke around each glyph, useful on light tape."),
    "stroke_width": (
        "Outline thickness in pixels (0 = no outline).\n"
        "Larger values make the text bolder."),
    "end_margin": (
        "Extra blank tape at the end of the label in dots.\n"
        "Appends margin after the last pixel (top-bottom for portrait)."),
    "unicode": (
        "Interpret the text with \\uXXXX escapes (-u).\n"
        "Off: escape sequences are printed literally."),
    "lines": (
        "Show rulers and guide lines on the image (-l).\n"
        "Draws a ruler and border lines on the preview/label image."),
    "image": (
        "Legacy single image to merge (-i).\n"
        "A PNG/JPG/GIF/BMP/PDF placed on the tape. For multiple images\n"
        "use the merge list instead."),
    "merge_list": (
        "Merge image files to print side by side (-M, repeatable).\n"
        "Each image is composited onto the label; order matters.\n"
        "Use \"Add merge image\" to append and \"Remove selected\" to "
        "delete."),
    "resize": (
        "Scale factor for merged images (-R).\n"
        "1.0 = original size; 0.5 = half; 2.0 = double."),
    "x_merge": (
        "Horizontal position offset for merged images (-X, dots)."),
    "y_merge": (
        "Vertical position offset for merged images (-Y, dots)."),
    "merge_gap": (
        "Gap between merged images in dots.\n"
        "0 = images touch edge to edge."),
    "white_level": (
        "White threshold for the legacy -i image (0-255).\n"
        "Pixels brighter than this are treated as blank."),
    "threshold": (
        "Binarization threshold for merged images (0-255).\n"
        "Pixels darker than this become black, the rest white."),
    "comport": (
        "Printer port.\n"
        "COM port on Windows, or bt:NAME / bt:MAC for Bluetooth.\n"
        "Examples: COM4, bt:PT-P300, bt:68:84:7e:61:5a:2f.\n"
        "Use \"Refresh\" to rescan serial and Bluetooth devices."),
    "no_print": (
        "Simulation mode (-n): no paper output.\n"
        "Builds the label and shows the raster but never sends data to "
        "the printer."),
    "auto_cut": (
        "Cut the tape automatically after printing (-a).\n"
        "Requires a printer with an auto-cutter (PT-P300 series)."),
    "no_feed": (
        "Disable the end-of-label feed (-F).\n"
        "Chains labels back-to-back without the feed gap."),
    "raw": (
        "Send the raster raw without built-in formatting (-r).\n"
        "For custom drivers or hand-crafted raster data."),
    "nocomp": (
        "Disable RLE compression (-C).\n"
        "Sends uncompressed raster data; slightly slower but "
        "compatibility-safe."),
    "preview_conv": (
        "Show the converted raster in the preview.\n"
        "What you see is exactly what is sent to the printer "
        "(1-bit, rotated)."),
    "auto_preview": (
        "Live preview.\n"
        "Regenerate the preview automatically whenever any control changes;"
        " turn off on slow machines."),
    "zoom": (
        "Preview zoom.\n"
        "Fit = scale the tape to fit the window.\n"
        "100/150/200% = fixed magnification (max 200%).\n"
        "Ctrl+wheel zooms, wheel pans when zoomed."),
}


class LabelGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x450")
        self.minsize(1000, 400)
        self._busy = False
        self._photo = None  # keep PhotoImage references

        # Accent style for the PRINT button: falls back gracefully on themes
        # without an Accent.TButton layout (just keeps the default look).
        self._style = ttk.Style(self)
        try:
            self._style.configure("Accent.TButton",
                                  font=("Segoe UI", 10, "bold"))
        except Exception:
            pass

        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        # Status bar at the very bottom (packed first with side="bottom" so
        # it always spans the full window width). A little breathing room
        # separates it from the two columns above.
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(outer, textvariable=self.status, anchor="w",
                  relief="sunken", padding=(6, 3)).pack(
            side="bottom", fill="x", pady=(6, 0))

        columns = tk.PanedWindow(outer, orient="horizontal",
                                 sashwidth=3, sashrelief="raised")
        columns.pack(side="top", fill="both", expand=True)

        controls = ttk.Frame(columns, width=400)
        columns.add(controls, minsize=400)
        ctrl_sb = ttk.Scrollbar(controls, orient="vertical")
        right = ttk.Frame(columns)
        columns.add(right)
        self._columns = columns

        self._build_controls(controls, ctrl_sb)
        self._build_right(right)

        # First paint + live preview on any control change
        self.bind("<Configure>", self._on_resize)
        self._preview_scheduled = False
        self._resize_scheduled = False
        # Text box emoji state: the emoji sequence rendered as color
        # images and references to the PhotoImage objects Tk must keep
        # alive. The real text is derived from the widget itself
        # (placeholder "" -> emoji chars) in _get_text().
        self._emoji_sequence = []
        self._emoji_photos = []
        self._updating_emoji = False
        self.after(200, self._schedule_preview)
        self.after(300, self._refresh_emoji_view)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def _build_controls(self, parent, sb):
        canvas = tk.Canvas(parent, highlightthickness=0)
        # Always show the scrollbar (visible affordance), even when the
        # content fits: ttk auto-hides it, which made it look missing.
        style = ttk.Style(self)
        style.layout("Always.Vertical.TScrollbar",
                     style.layout("Vertical.TScrollbar"))
        sb.configure(style="Always.Vertical.TScrollbar")
        sb.configure(command=canvas.yview)
        frame = ttk.Frame(canvas, padding=4)
        frame.columnconfigure(0, weight=1)  # stretch groups full width
        frame.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        # Bind the inner frame width to the canvas width so the controls fill
        # the column and the vertical scrollbar appears when they overflow.
        canvas.create_window((0, 0), window=frame, anchor="nw", tags="inner")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(
            "inner", width=e.width))
        # Always show the scrollbar as enabled: report a fixed fraction so
        # ttk never greys it out, and keep the real scroll behavior.
        canvas.configure(yscrollcommand=lambda first, last: sb.set(0.0, 1.0)
                         if float(first) == 0.0 and float(last) == 1.0
                         else sb.set(first, last))
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _wheel(e):
            # Scroll the controls column only when the wheel is over its
            # content: walk up from the event widget — if we hit this canvas
            # the pointer is inside the left column (preview/log on the right
            # never scroll it). Widgets that scroll themselves (text boxes,
            # combos, lists, spinboxes) are left alone.
            node = e.widget
            # Some events (e.g. generated ones) carry the widget as a plain
            # string path; treat those as "not over the canvas".
            if not hasattr(node, "master"):
                return
            while node is not None and node is not canvas and node is not self:
                node = node.master
            if node is not canvas:
                return
            if isinstance(e.widget, (tk.Listbox, tk.Text, ttk.Combobox,
                                     ttk.Spinbox, scrolledtext.ScrolledText,
                                     ttk.Scrollbar, ttk.Treeview)):
                return
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind_all("<MouseWheel>", _wheel)

        self.vars = {}
        row = [0]

        def group(title, expert=False):
            """Create a group; expert groups start collapsed.

            Collapsing: double-click the title of regular groups; expert
            groups also expose a visible ▶/▼ toggle on the right. Returns
            the body frame.
            """
            lf = ttk.LabelFrame(frame, text=title, padding=8)
            lf.grid(row=row[0], column=0, sticky="ew", pady=4)
            lf.columnconfigure(1, weight=1)
            row[0] += 1
            body = ttk.Frame(lf)
            body.grid(row=0, column=0, columnspan=3, sticky="ew")
            body.columnconfigure(1, weight=1)
            state = {"open": True, "marker": None}

            def _update_marker():
                m = state.get("marker")
                if m is not None:
                    try:
                        m.configure(text="▼" if state["open"] else "▶")
                    except Exception:
                        pass

            def toggle(_event=None):
                state["open"] = not state["open"]
                if state["open"]:
                    body.grid()
                else:
                    body.grid_remove()
                _update_marker()
                _schedule_preview_safe()

            def _schedule_preview_safe():
                if hasattr(self, "_schedule_preview"):
                    self._schedule_preview()

            if expert:
                state["open"] = False
                body.grid_remove()
                lbl = tk.Label(lf, text="▶", cursor="hand2",
                               font=("Segoe UI", 9))
                lbl.grid(row=0, column=2, sticky="e")
                lbl.bind("<Button-1>", toggle)
                state["marker"] = lbl
                # The whole title line toggles as well (single click).
                lf.bind("<Button-1>", toggle)
                lf.bind("<Double-1>", toggle)
                lf._expert_marker = lbl
            else:
                # Regular groups: double-click the title to collapse.
                lf.bind("<Double-1>", toggle)
            return body

        def _changed(*_):
            self._schedule_preview()

        def entry(g, label, key, default="", browse=None,
                  filetypes=(("All files", "*.*"),)):
            var = tk.StringVar(value=default)
            self.vars[key] = var
            f = ttk.Frame(g)
            f.grid(row=len(g.grid_size()[1]) - 1 if False else g.grid_size()[1],
                   column=0, columnspan=3, sticky="ew", pady=1)
            ttk.Label(f, text=label, width=17).pack(side="left")
            e = ttk.Entry(f, textvariable=var)
            e.pack(side="left", fill="x", expand=True)
            help_text = HELPS.get(key)
            if help_text:
                Tooltip(e, help_text); Tooltip(f, help_text)
            if browse:
                def pick():
                    fn = filedialog.askopenfilename(filetypes=filetypes)
                    if fn:
                        var.set(fn)
                        _changed()
                        if key == "fontname":
                            self._sync_font_combo()
                ttk.Button(f, text="...", width=3, command=pick).pack(
                    side="left", padx=2)
            var.trace_add("write", _changed)

        def pair(g, label, key, default="", validate=None):
            var = tk.StringVar(value=str(default))
            self.vars[key] = var
            r = g.grid_size()[1]
            lab = ttk.Label(g, text=label)
            lab.grid(row=r, column=0, sticky="w", pady=1)
            e = ttk.Entry(g, textvariable=var, width=10)
            e.grid(row=r, column=1, sticky="ew", pady=1)
            help_text = HELPS.get(key)
            if help_text:
                Tooltip(lab, help_text); Tooltip(e, help_text)
            var.trace_add("write", _changed)
            return var

        def check(g, label, key, command=_changed, default=False):
            var = tk.BooleanVar(value=default)
            self.vars[key] = var
            cb = ttk.Checkbutton(g, text=label, variable=var,
                                 command=command)
            cb.grid(row=g.grid_size()[1], column=0, columnspan=3,
                    sticky="w", pady=1)
            help_text = HELPS.get(key)
            if help_text:
                Tooltip(cb, help_text)
            return var

        def combo(g, label, key, values, default="", command=_changed):
            var = tk.StringVar(value=default)
            self.vars[key] = var
            r = g.grid_size()[1]
            lab = ttk.Label(g, text=label)
            lab.grid(row=r, column=0, sticky="w", pady=1)
            cb = ttk.Combobox(g, textvariable=var, values=values,
                              state="readonly", width=20)
            cb.grid(row=r, column=1, sticky="w", pady=1)
            help_text = HELPS.get(key)
            if help_text:
                Tooltip(lab, help_text); Tooltip(cb, help_text)
            var.trace_add("write", command)
            return var

        # ---------------- Text / font ----------------
        g = group("Text & Font")
        # Multi-line text box (one label line per row, like \n in the CLI),
        # with scrollbars when content exceeds the visible size.
        tf = ttk.Frame(g)
        tf.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        tf.columnconfigure(0, weight=1)
        self.text_box = tk.Text(tf, width=28, height=4, wrap="word",
                                font=("Segoe UI", 10))
        self.text_box.grid(row=0, column=0, sticky="ew")
        tsb = ttk.Scrollbar(tf, orient="vertical",
                            command=self.text_box.yview)
        self.text_box.configure(yscrollcommand=tsb.set)
        tsb.grid(row=0, column=1, sticky="ns")
        self.text_box.bind("<<Modified>>", self._on_text_modified)
        self.text_box.insert("1.0", "")
        Tooltip(self.text_box, HELPS["text"])
        entry(g, "Font file:", "fontname", self._default_font(), browse=True,
              filetypes=(("Font files", "*.ttf *.otf *.ttc"), ("All", "*.*")))
        self.vars["fontname"] = self.vars.get("fontname", tk.StringVar())
        fb = ttk.Frame(g)
        fb.grid(row=g.grid_size()[1], column=0, columnspan=3, sticky="ew")
        self.font_combo = ttk.Combobox(fb, state="readonly", width=24)
        self.font_combo.pack(side="left", fill="x", expand=True)
        self.font_combo.bind("<<ComboboxSelected>>", self._on_font_picked)
        Tooltip(self.font_combo, HELPS["fontname"])
        ttk.Button(fb, text="Browse...",
                   command=self._browse_fonts).pack(side="left", padx=4)
        self.after(50, self._load_font_combo)
        check(g, "Center text (-H)", "center_text")
        check(g, "Emoji fill print area (--emoji-print-area)",
              "emoji_print_area")
        check(g, "Emoji black & white (--mono-emoji)", "mono_emoji",
              default=True, command=self._on_mono_toggled)
        check(g, "Uniform font sizing (--uniform-font)",
              "uniform_font")
        # OpenType GSUB feature selector: the values are filled from the
        # selected font's GSUB table (see _refresh_ligature_combo).
        self.vars.setdefault("ligatures", tk.StringVar(value=""))
        lg = ttk.Frame(g)
        lg.grid(row=g.grid_size()[1], column=0, columnspan=3, sticky="ew",
                pady=1)
        ttk.Label(lg, text="Ligature feature:", width=17).pack(side="left")
        self.ligature_combo = ttk.Combobox(
            lg, textvariable=self.vars["ligatures"], state="readonly",
            width=22, values=[""])
        self.ligature_combo.pack(side="left", fill="x", expand=True)
        self.ligature_combo.bind("<<ComboboxSelected>>", _changed)
        Tooltip(self.ligature_combo, HELPS["ligatures"])
        self.after(80, self._refresh_ligature_combo)
        pair(g, "TAB width (spaces):", "tab_width", 8)

        # ---------------- Expert: text tuning ----------------
        g = group("Text tuning (advanced)", expert=True)
        pair(g, "Font scale (%):", "font_scale", 100)
        pair(g, "Line spacing:", "line_spacing", 1.2)
        pair(g, "H padding (dots):", "h_padding", 5)
        pair(g, "V shift (dots):", "v_shift", 0)
        pair(g, "Text width (mm, 0=auto):", "text_size", 0)
        pair(g, "Fixed width (mm, 0=off):", "fixed_width", 0)
        pair(g, "Tape width (mm, 12):", "tape_width", 12.0)
        pair(g, "Fixed font size (0=auto):", "fixed_font_size", 0)
        pair(g, "Luma low (emoji B/W):", "luma_lo", 140.0)
        pair(g, "Luma high (emoji B/W):", "luma_hi", 175.0)
        pair(g, "Fill color:", "fill", "black")
        pair(g, "Stroke fill:", "stroke_fill", "")
        pair(g, "Stroke width:", "stroke_width", 0)
        pair(g, "End margin (dots):", "end_margin", 0)
        check(g, "Use Unicode escapes (-u)", "unicode")
        check(g, "Show rulers/lines (-l)", "lines", command=_changed)
        self.vars["lines"].set(True)  # rulers on by default, like CLI output

        # ---------------- Images ----------------
        g = group("Images (merge -M / legacy -i)", expert=True)
        self.merge_list = tk.Listbox(g, height=3)
        self.merge_list.grid(row=0, column=0, columnspan=3,
                             sticky="ew", pady=2)
        Tooltip(self.merge_list, HELPS["merge_list"])
        fb = ttk.Frame(g)
        fb.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Button(fb, text="Add merge image (-M)...",
                   command=self._add_merge).pack(side="left")
        ttk.Button(fb, text="Remove selected", command=self._del_merge).pack(
            side="left", padx=4)
        entry(g, "Legacy image (-i):", "image", browse=True,
              filetypes=(("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.pdf"),
                         ("All", "*.*")))
        pair(g, "Resize (-R):", "resize", 1.0)
        pair(g, "X merge (-X):", "x_merge", 0)
        pair(g, "Y merge (-Y):", "y_merge", 12)
        pair(g, "Merge gap (dots):", "merge_gap", 0)
        pair(g, "White level:", "white_level", 240)
        pair(g, "Threshold:", "threshold", 75)

        # ---------------- Printer ----------------
        g = group("Printer")
        self.vars["comport"] = tk.StringVar(value="bt:PT-P300")
        pf = ttk.Frame(g)
        pf.grid(row=0, column=0, columnspan=3, sticky="ew", pady=1)
        ttk.Label(pf, text="Port:").pack(side="left")
        self.port_combo = ttk.Combobox(pf, textvariable=self.vars["comport"])
        self.port_combo.pack(side="left", fill="x", expand=True)
        Tooltip(self.port_combo, HELPS["comport"])
        ttk.Button(pf, text="Refresh", command=self._refresh_ports).pack(
            side="left", padx=2)
        self.vars["comport"].trace_add("write", lambda *a: None)
        check(g, "No print (-n, no paper output)", "no_print")
        check(g, "Auto cut (-a)", "auto_cut")

        g = group("Printer tuning (advanced)", expert=True)
        check(g, "No feed (-F, chaining)", "no_feed")
        check(g, "Raw (-r)", "raw")
        check(g, "No compression (-C)", "nocomp")

        # ---------------- Preview options ----------------
        g = group("Preview")
        self.vars["preview_conv"] = tk.BooleanVar(value=False)
        pc = ttk.Checkbutton(
            g, text="Show converted raster (as sent to printer)",
            variable=self.vars["preview_conv"],
            command=self._schedule_preview)
        pc.grid(row=0, column=0, columnspan=3, sticky="w")
        Tooltip(pc, HELPS["preview_conv"])
        self.vars["auto_preview"] = tk.BooleanVar(value=True)
        ap = ttk.Checkbutton(
            g, text="Live preview", variable=self.vars["auto_preview"])
        ap.grid(row=1, column=0, columnspan=3, sticky="w")
        Tooltip(ap, HELPS["auto_preview"])

    # ------------------------------------------------------------------
    # Right side: preview (zoom/pan canvas) + log
    # ------------------------------------------------------------------
    def _build_right(self, parent):
        # Vertical paned window: preview on top, log at the bottom. The sash
        # between them is draggable (styled to be clearly visible).
        pwin = ttk.PanedWindow(parent, orient="vertical")
        pwin.pack(fill="both", expand=True)

        pv = ttk.LabelFrame(pwin, text="Label preview", padding=4)
        pwin.add(pv, weight=4)  # preview gets ~2/3 of the vertical space

        # Toolbar
        tb = ttk.Frame(pv)
        tb.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self._zoom_mode = tk.StringVar(value="Fit")
        ttk.Button(tb, text="−", width=3, command=self._zoom_out).grid(
            row=0, column=0, padx=(0, 2))
        ttk.Button(tb, text="+", width=3, command=self._zoom_in).grid(
            row=0, column=1)
        ttk.Button(tb, text="Fit", command=lambda: self._set_zoom("Fit")).grid(
            row=0, column=2, padx=2)
        ttk.Button(tb, text="1:1 tape", command=self._set_tape_zoom).grid(
            row=0, column=3, padx=2)
        self.zoom_info = tk.StringVar(value="")
        ttk.Label(tb, textvariable=self.zoom_info).grid(
            row=0, column=4, padx=6, sticky="w")
        tb.columnconfigure(4, weight=1)

        # Scrollable canvas
        self.canvas = tk.Canvas(pv, bg="#505050", highlightthickness=0)
        hsb = ttk.Scrollbar(pv, orient="horizontal", command=self.canvas.xview)
        vsb = ttk.Scrollbar(pv, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        pv.rowconfigure(1, weight=1)
        pv.columnconfigure(0, weight=1)

        # Pan & zoom interactions
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        # Windows/Linux wheel
        self.canvas.bind("<MouseWheel>", self._wheel_scroll)
        self.canvas.bind("<Shift-MouseWheel>", self._wheel_scroll_h)
        self.canvas.bind("<Control-MouseWheel>", self._wheel_zoom)
        # Linux scroll events
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        self.canvas.bind("<Shift-Button-4>", lambda e: self.canvas.xview_scroll(-2, "units"))
        self.canvas.bind("<Shift-Button-5>", lambda e: self.canvas.xview_scroll(2, "units"))

        # The rendered image
        self._canvas_image = None
        self._pil_img = None          # current PIL image at native size
        self._zoom = None             # None => "Fit"

        # Output log: compact, fixed height (does not squeeze the preview)
        logf = ttk.LabelFrame(pwin, text="Output", padding=6)
        pwin.add(logf, weight=1)
        Tooltip(logf, (
            "Output log.\n"
            "Shows what the app is doing: port connections, print progress,\n"
            "errors and status messages. Read-only; the log is not cleared\n"
            "until the app closes."))
        self.log = scrolledtext.ScrolledText(logf, height=5, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        # Action bar as its own pane under the output log: always visible,
        # never squeezed by the log above. It occupies its natural height and
        # the sash between log and actions stays draggable.
        actf = ttk.Frame(pwin)
        pwin.add(actf, weight=0)
        ttk.Button(actf, text="Refresh preview (F5)",
                   command=self._schedule_preview).pack(
            side="left", padx=(12, 1))
        ttk.Button(actf, text="Save PNG...",
                   command=self._save_png).pack(side="left", padx=1)
        self.print_btn = ttk.Button(actf, text="PRINT",
                                    style="Accent.TButton",
                                    command=self._do_print)
        self.print_btn.pack(side="right", padx=(4, 0))
        ttk.Button(actf, text="Quit",
                   command=self.destroy).pack(side="right")

        # Make the native sash clearly visible and easy to grab via ttk style
        # (a thick, light band with a grip). No extra widget is inserted
        # between the panes — the sash itself is styled.
        style = ttk.Style(self)
        style.layout("Visible.Panedwindow", style.layout("TPanedwindow"))
        style.configure("Visible.Panedwindow", sashthickness=10)
        # Draw grip lines on the sash via the element (best-effort across
        # themes; the thickness alone already makes it visible/grabbable).
        pwin.configure(style="Visible.Panedwindow")

        # Default preview height: most of the right side (~60%), the user can
        # then drag the sash to taste.
        self.after(50, lambda: self._set_default_sash())
        # Re-apply once mapped (winfo_height is 1 before the window is mapped)
        self.bind("<Map>", lambda e: self.after(10, self._set_default_sash),
                  add="+")

        # Keyboard shortcuts
        self.bind("<Control-p>", lambda e: self._do_print())
        self.bind("<F5>", lambda e: self._schedule_preview())
        self.bind("<Control-s>", lambda e: self._save_png())
        self.bind("<Control-0>", lambda e: self._set_zoom("Fit"))
        self.bind("<Control-plus>", lambda e: self._zoom_in())
        self.bind("<Control-equal>", lambda e: self._zoom_in())
        self.bind("<Control-minus>", lambda e: self._zoom_out())
        self.bind("<Control-b>", lambda e: self._browse_fonts())
        self.bind("<Control-r>", lambda e: self._refresh_ports())
        self.bind("<F12>", self._open_inspector)

    # ---- sash handling ----
    def _set_default_sash(self):
        """Give the preview pane most of the right side (~70%)."""
        try:
            pwin = self.canvas.master.master
            if isinstance(pwin, ttk.PanedWindow) and pwin.winfo_height() > 50:
                pwin.sashpos(0, int(pwin.winfo_height() * 0.70))
        except Exception:
            pass

    # ---- widget inspector (F12) ----
    def _open_inspector(self, _event=None):
        self._builtin_inspector()
        return "break"

    def _builtin_inspector(self):
        """Minimal built-in widget tree inspector (F12 fallback)."""
        win = tk.Toplevel(self)
        win.title("Widget inspector")
        win.geometry("640x520")
        tree = ttk.Treeview(win, show="tree")
        tree.pack(fill="both", expand=True, padx=6, pady=6)

        def add(w, parent=""):
            cls = w.winfo_class()
            name = str(w).split(".")[-1] or "root"
            # Unique iid: the same widget path can appear twice when a widget
            # is re-parented (e.g. PanedWindow panes); append a counter.
            iid = str(w)
            n = 1
            while tree.exists(iid):
                n += 1
                iid = f"{w}#{n}"
            try:
                text = f"{cls}: {name}  ({w.winfo_width()}x{w.winfo_height()})"
            except Exception:
                text = cls
            tree.insert(parent, "end", iid=iid, text=text, open=True)
            for c in w.winfo_children():
                add(c, iid)
        add(self, "")

    # ---- zoom / pan helpers ----
    def _set_zoom(self, mode):
        self._zoom_mode.set(mode)
        self._render()

    def _set_tape_zoom(self):
        """Zoom so the tape is shown at its true physical size on screen.

        PT-P300 tape is 12mm wide; at 203 dpi that's ~96 raster dots. We
        need 96 raster px to span exactly the 12mm the monitor will show at
        its own DPI, so the preview matches the real printed tape 1:1.
        """
        if self._pil_img is None:
            return
        try:
            dpi = self.winfo_fpixels("1i")  # real screen DPI
        except Exception:
            dpi = 96.0
        mm_per_inch = 25.4
        tape_mm = 12.0
        # raster dots that make up the tape height (128-grid uses all).
        # The tape height in dots: 12mm * 203dpi / 25.4 ≈ 95.9 → 96.
        tape_dots = round(tape_mm * 203 / mm_per_inch)
        # screen px per mm at the real DPI:
        px_per_mm = dpi / mm_per_inch
        # zoom factor: scale so tape_dots raster → (tape_mm * px_per_mm) screen px
        target = tape_mm * px_per_mm / tape_dots
        self._zoom = min(target, MAX_ZOOM)
        self._zoom_mode.set("")
        self._render()

    def _zoom_in(self):
        z = self._effective_zoom()
        self._set_zoom_pct(min(z * 1.25, MAX_ZOOM))

    def _zoom_out(self):
        z = self._effective_zoom()
        self._set_zoom_pct(max(z / 1.25, 0.1))

    def _effective_zoom(self):
        if self._zoom_mode.get() == "Fit" or self._pil_img is None:
            return self._fit_scale()
        return self._zoom

    def _set_zoom_pct(self, pct):
        self._zoom = pct
        self._zoom_mode.set("")  # custom value not in the list
        self._render()

    def _fit_scale(self):
        """Fit scale for a horizontal tape: match the canvas WIDTH.

        The tape is horizontal, so the label fills the full canvas width and
        its height follows the tape aspect. If the resulting height exceeds
        the canvas, the vertical scrollbar kicks in (no empty space below).
        """
        if self._pil_img is None:
            return 1.0
        w = max(self.canvas.winfo_width() - 8, 100)
        return min(w / self._pil_img.width, MAX_ZOOM)

    def _render(self):
        if self._pil_img is None:
            return
        from PIL import Image, ImageTk
        img = self._pil_img
        if self._zoom_mode.get() == "Fit" or self._zoom is None:
            scale = self._fit_scale()
        else:
            scale = self._zoom
        w = max(1, int(img.width * scale))
        h = max(1, int(img.height * scale))
        resample = Image.NEAREST if scale >= 2 else Image.LANCZOS
        shown = img.resize((w, h), resample)
        self._photo = ImageTk.PhotoImage(shown)
        self.canvas.delete("all")
        self._canvas_image = self.canvas.create_image(
            4, 4, image=self._photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, w + 8, h + 8))
# If the tape is taller than the viewport at this zoom, scroll to the
        # top edge so the label is flush with the canvas (no dead space).
        self.canvas.yview_moveto(0)
        self.zoom_info.set(f"{w}×{h}  ({scale * 100:.0f}%)")

    def _pan_start(self, event):
        # Use canvas scan semantics: reliable pixel panning on all Tk versions
        self.canvas.scan_mark(event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _pan_move(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _wheel_scroll(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120 * 3), "units")

    def _wheel_scroll_h(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120 * 3), "units")

    def _wheel_zoom(self, event):
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _clear_preview(self):
        if hasattr(self, "canvas"):
            self.canvas.delete("all")
        self._canvas_image = None
        self._pil_img = None
        self.zoom_info.set("")

    # ------------------------------------------------------------------
    # Font browsing (cross-platform, via fontbrowser)
    # ------------------------------------------------------------------
    def _load_font_combo(self):
        try:
            from fontbrowser import list_system_fonts
            self._system_fonts = list_system_fonts()
            self.font_combo["values"] = [lbl for lbl, _ in self._system_fonts]
            self.status.set(f"{len(self._system_fonts)} system fonts found.")
        except Exception as e:
            self._log(f"Font discovery failed: {e!r}")
        self._sync_font_combo()

    def _on_font_picked(self, _event=None):
        label = self.font_combo.get()
        for lbl, path in getattr(self, "_system_fonts", []):
            if lbl == label:
                self.vars["fontname"].set(path)
                self._refresh_ligature_combo()
                self._schedule_preview()
                return

    def _sync_font_combo(self):
        """Show the Font file value in the font dropdown, if listed.

        Keeps the dropdown in sync when a font is chosen via the browse
        buttons (the dropdown itself is readonly, so it cannot display a
        typed path: it shows the matching system-font label, or stays
        empty when the path is not in the list).
        """
        if not hasattr(self, "font_combo"):
            return
        self._refresh_ligature_combo()
        current = os.path.basename(self.vars["fontname"].get() or "")
        for lbl, path in getattr(self, "_system_fonts", []):
            if os.path.basename(path).lower() == current.lower():
                try:
                    self.font_combo.set(lbl)
                except tk.TclError:
                    pass
                return
        try:
            self.font_combo.set("")
        except tk.TclError:
            pass

    def _refresh_ligature_combo(self):
        """Fill the ligature-feature combo from the selected font's GSUB.

        The OpenType GSUB table of the current font lists exactly the
        features it can apply (liga, calt, dlig, …). Keep the selection
        when the font is re-picked (features are font-specific).
        """
        try:
            if not hasattr(self, "ligature_combo"):
                return
            from printlabel import _lig_features
            feats = _lig_features(self.vars["fontname"].get() or "arial.ttf")
            values = [""] + list(feats)
            try:
                self.ligature_combo["values"] = values
            except tk.TclError:
                return
        except Exception:
            pass

    def _browse_fonts(self):
        """Open a searchable font-picker window with live per-font previews.

        A Listbox with the font name drives the keyboard (arrows, PgUp/PgDn,
        Home/End to move, Enter to pick, Esc to close). The sample label
        below renders the text in the currently selected font, so browsing is
        visual: what you see is what the label text will look like.
        """
        # Make sure the font list is loaded before showing the window.
        if not getattr(self, "_system_fonts", None):
            try:
                from fontbrowser import list_system_fonts
                self._system_fonts = list_system_fonts()
            except Exception:
                self._system_fonts = []

        win = tk.Toplevel(self)
        win.title("System fonts")
        win.geometry("680x620")
        win.transient(self)

        top = ttk.Frame(win, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Search:").pack(side="left")
        search_var = tk.StringVar()
        search_entry = ttk.Entry(top, textvariable=search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=4)

        SAMPLE = "ABCabc 123"

        mid = ttk.Frame(win)
        mid.pack(fill="both", expand=True, padx=6, pady=4)
        listbox = tk.Listbox(mid, font=("Segoe UI", 11), activestyle="dotbox")
        sb = ttk.Scrollbar(mid, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=sb.set)
        listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Live sample: renders SAMPLE in the currently selected font.
        preview = tk.Label(win, text="", bg="white", anchor="center",
                           justify="left")
        preview.pack(fill="both", expand=False, padx=6, pady=(0, 4))

        fonts = getattr(self, "_system_fonts", [])
        font_path = {lbl: p for lbl, p in fonts}
        ordered = [lbl for lbl, _ in fonts]

        def refresh(*_args):
            term = search_var.get().lower()
            listbox.delete(0, "end")
            for lbl in ordered:
                if not term or term in lbl.lower():
                    listbox.insert("end", lbl)
            if listbox.size():
                listbox.selection_set(0)
                listbox.see(0)
                update_preview()

        def update_preview(_event=None):
            sel = listbox.curselection()
            if not sel:
                return
            lbl = listbox.get(sel[0])
            path = font_path.get(lbl)
            try:
                # Render the sample exactly like printlabel will: with Pillow
                # using the selected TTF. This is a faithful "what you see is
                # what prints" preview that plain Tk fonts cannot give.
                from PIL import Image, ImageDraw, ImageFont, ImageTk
                size = 40
                fnt = ImageFont.truetype(path, size)
                w = preview.winfo_width() or 640
                tmp = Image.new("RGB", (w, 56), "white")
                d = ImageDraw.Draw(tmp)
                d.text((8, (56 - size) // 2), SAMPLE, font=fnt,
                       fill=(20, 20, 20))
                ph = ImageTk.PhotoImage(tmp)
                preview.configure(image=ph, text="")
                preview._img = ph  # keep a reference
            except Exception:
                # Fallback: plain Tk font family name (best effort).
                preview.configure(text=SAMPLE, image="")
                try:
                    preview.configure(font=(lbl, 20))
                except Exception:
                    preview.configure(font=("Segoe UI", 20))

        listbox.bind("<<ListboxSelect>>", update_preview)

        def pick(_event=None):
            sel = listbox.curselection()
            if not sel:
                return
            lbl = listbox.get(sel[0])
            self.vars["fontname"].set(font_path.get(lbl, lbl))
            self._sync_font_combo()
            self._schedule_preview()
            win.destroy()

        listbox.bind("<Return>", pick)
        listbox.bind("<Double-Button-1>", pick)
        win.bind("<Escape>", lambda e: win.destroy())
        search_var.trace_add("write", refresh)

        refresh()

        # Pre-select the current font
        current = os.path.basename(self.vars["fontname"].get() or "")
        for i, lbl in enumerate(ordered):
            if os.path.basename(font_path.get(lbl, "")).lower() == \
                    current.lower():
                listbox.selection_clear(0, "end")
                listbox.selection_set(i)
                listbox.see(i)
                break

        btns = ttk.Frame(win, padding=6)
        btns.pack(fill="x")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        search_entry.focus_set()

    # ------------------------------------------------------------------
    # Preview pipeline (uses printlabel API)
    # ------------------------------------------------------------------
    def _get_text(self):
        """Contents of the multi-line text box, one label line per row.

        The widget stores emoji as a zero-width placeholder with an inline
        color image attached; reading the widget gives the real emoji back
        by replacing those placeholders with the emoji chars (in order).
        """
        if not hasattr(self, "text_box"):
            return ""
        raw = self.text_box.get("1.0", "end-1c")
        if "\ufeff" not in raw:
            return raw
        seq = list(self._emoji_sequence)
        out = []
        for ch in raw:
            if ch == "\ufeff":
                out.append(seq.pop(0) if seq else "")
            else:
                out.append(ch)
        return "".join(out)

    # Emoji chars get replaced by a zero-width placeholder in the widget;
    # the color raster is attached via image_create. This keeps typing
    # fluid while the emoji render in full color (Tk/GDI would draw them
    # monochrome).
    _EMOJI_PLACEHOLDER = "\ufeff"

    def _refresh_emoji_view(self, force=False):
        """Rebuild the text box, drawing each emoji as a raster image.

        Reads the widget content (plain text + placeholders), restores the
        real emoji characters, then re-inserts the text segment by
        segment, pasting a PhotoImage raster for each emoji (the same
        renderer printlabel uses). Called on edits that affect emoji;
        plain-text-only edits skip the rebuild so the cursor does not jump.
        `force=True` re-renders even when the emoji layout is unchanged
        (e.g. when the black & white option is toggled).
        """
        tb = self.text_box
        if not hasattr(self, "_updating_emoji") or self._updating_emoji:
            return
        self._updating_emoji = True
        try:
            raw = tb.get("1.0", "end-1c")
            seq = list(self._emoji_sequence)
            real_parts = []
            for ch in raw:
                if ch == self._EMOJI_PLACEHOLDER:
                    if seq:
                        real_parts.append(seq.pop(0))
                else:
                    real_parts.append(ch)
            real = "".join(real_parts)

            # If the emoji layout is unchanged the widget is already
            # correct (only plain characters were edited): keep it as-is
            # so the cursor position is preserved while typing.
            if not force and self._emoji_sequence == \
                    [c for c in real if printlabel.is_emoji_char(c)]:
                return

            # Re-render everything.
            new_seq = []
            photos = []
            tb.delete("1.0", "end")
            # Determine the emoji target height: match the widget font.
            try:
                font_obj = tkfont.nametofont(tb.cget("font"))
                emoji_h = max(14, int(font_obj.metrics("linespace") * 0.9))
            except Exception:
                emoji_h = 17
            from PIL import Image, ImageTk
            mono = bool(self.vars["mono_emoji"].get())
            for ch in real:
                if printlabel.is_emoji_char(ch):
                    raster = printlabel.emoji_thumbnail(ch, emoji_h,
                                                        mono=mono)
                    if raster is not None:
                        # Flatten onto the widget background for crispness.
                        bg = tb.cget("background") or "#ffffff"
                        try:
                            from PIL import ImageColor
                            bg_rgb = ImageColor.getrgb(bg)
                        except Exception:
                            bg_rgb = (255, 255, 255)
                        flat = Image.new("RGBA", raster.size,
                                         bg_rgb + (255,))
                        flat.alpha_composite(raster)
                        raster = flat.convert("RGB")
                        img = ImageTk.PhotoImage(raster)
                        photos.append(img)
                        tb.insert("end", self._EMOJI_PLACEHOLDER)
                        tb.image_create("end-1c", image=img)
                        new_seq.append(ch)
                        continue
                tb.insert("end", ch)
            self._emoji_sequence = new_seq
            self._emoji_photos = photos
        except Exception:
            # On any rendering problem, keep the real text usable: drop the
            # image layout and store the text plainly.
            try:
                self._emoji_sequence = []
            except Exception:
                pass
        finally:
            self._updating_emoji = False

    def _on_mono_toggled(self):
        # The emoji layout (which chars are emoji) does not change when
        # toggling black & white, so force a re-render of the pictures
        # in the text box, then refresh the preview.
        try:
            self._refresh_emoji_view(force=True)
        except Exception:
            pass
        self._schedule_preview()

    def _on_text_modified(self, _event=None):
        # <<Modified>> fires on every change; re-arm the flag so it keeps
        # firing, then refresh the emoji images and schedule a debounced
        # preview.
        if hasattr(self, "text_box"):
            self.text_box.edit_modified(False)
        self._refresh_emoji_view()
        self._schedule_preview()

    def _make_args(self, no_print):
        """Build an argparse.Namespace for the printlabel API."""
        v = self.vars

        def num(key, cast=float, default=None):
            try:
                val = cast(v[key].get())
                return val
            except (ValueError, TypeError, KeyError):
                return default

        text = self._get_text()
        merge = [self.merge_list.get(i)
                 for i in range(self.merge_list.size())] or None
        image = v["image"].get().strip() or None
        if image:  # legacy mode: -i becomes first merge, text ignored
            merge = [image] + (merge or [])
            image = None

        font_scale = num("font_scale")
        if font_scale == 100:
            font_scale = None
        fixed_width = num("fixed_width", int) or None
        fixed_font = num("fixed_font_size", int) or None
        text_size = num("text_size", int) or None

        # printlabel joins text_to_print with spaces and detects the literal
        # token "\n" to switch to multiline mode, so rejoin the text-box
        # lines with that exact token (same rendering as the CLI).
        cli_text = "\\n".join(text.split("\n")) if text else ""

        # Resolve bare font names (e.g. "arial.ttf") against the system font
        # directories, whatever the OS.
        try:
            from fontbrowser import find_font_path
            font_path = find_font_path(v["fontname"].get() or "arial.ttf")
        except Exception:
            font_path = v["fontname"].get() or "arial.ttf"

        return argparse.Namespace(
            comport=v["comport"].get() or "bt:PT-P300",
            list_bt=False,
            fixed_width=fixed_width,
            fixed_font_size=fixed_font,
            fontname=font_path,
            text_to_print=[cli_text] if cli_text else [],
            unicode=v["unicode"].get(),
            lines=v["lines"].get(),
            show=False,
            show_conv=False,
            image=image,
            merge=merge,
            resize=num("resize", float, 1.0),
            x_merge=num("x_merge", int, 0),
            y_merge=num("y_merge", int, 12),
            merge_gap=num("merge_gap", int, 0),
            save=None,
            save_conv=None,
            no_print=no_print,
            no_feed=v["no_feed"].get(),
            auto_cut=v["auto_cut"].get(),
            end_margin=num("end_margin", int, 0),
            raw=v["raw"].get(),
            nocomp=v["nocomp"].get(),
            fill=v["fill"].get() or "black",
            stroke_fill=v["stroke_fill"].get().strip() or None,
            stroke_width=num("stroke_width", int, 0),
            text_size=text_size,
            font_scale=font_scale,
            h_padding=num("h_padding", int, 5),
            v_shift=num("v_shift", int, 0),
            line_spacing=num("line_spacing", float, 1.2),
            center_text=v["center_text"].get(),
            tape_width=num("tape_width", float, 12.0),
            emoji_print_area=v["emoji_print_area"].get(),
            uniform_font=v["uniform_font"].get(),
            ligatures=v["ligatures"].get().strip() or None,
            mono_emoji=v["mono_emoji"].get(),
            luma_lo=num("luma_lo", float, 140.0),
            luma_hi=num("luma_hi", float, 175.0),
            tab_width=num("tab_width", int, 8),
            white_level=num("white_level", int, 240),
            threshold=num("threshold", int, 75),
        )

    def _schedule_preview(self, *_):
        if not self._preview_ready():
            return
        if self._busy:
            # A preview is already running: re-schedule so the latest text is
            # picked up as soon as it finishes (fixes the "last letter missing"
            # effect when typing fast).
            self._preview_scheduled = True
            self.after(120, self._schedule_preview)
            return
        if self.vars.get("auto_preview") and \
                not self.vars["auto_preview"].get():
            return
        self._preview_scheduled = True
        self.after(150, self._run_preview)

    def _preview_ready(self):
        return hasattr(self, "text_box") and "comport" in self.vars

    def _run_preview(self):
        self._preview_scheduled = False
        if self._busy:
            return
        self._busy = True

        def worker():
            try:
                args = self._make_args(no_print=True)
                # Empty text: just clear the preview silently (no error).
                if not args.text_to_print and not args.merge:
                    self.after(0, lambda: self._clear_preview())
                    self.after(0, lambda: self.status.set(
                        "Type some text to see the preview."))
                    return
                image = printlabel.build_label(args)
                padded = printlabel.rasterize_label(image, args)
                conv = padded.convert("L")
                self.after(0, lambda: self._show_image(
                    conv if self.vars["preview_conv"].get() else image))
                self.after(0, lambda: self.status.set(
                    f"{padded.width}x{padded.height} raster, "
                    f"{padded.height * 0.149 / 10:.1f} cm of tape."))
            except printlabel._LabelError as e:
                msg = str(e)
                if "Null image" in msg:
                    # Empty label: clear silently instead of showing an error.
                    self.after(0, lambda: self._clear_preview())
                    self.after(0, lambda: self.status.set(
                        "Type some text to see the preview."))
                    return
                self.after(0, lambda m=msg: self.status.set(f"Invalid: {m}"))
                self.after(0, lambda: self._clear_preview())
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: self.status.set(f"Error: {m}"))
                self.after(0, lambda m=msg: self._log(f"ERROR: {m}"))
            finally:
                self._busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _show_image(self, pil_img):
        img = pil_img
        if self.vars["preview_conv"].get():
            img = img.rotate(-90, expand=True)  # raster is vertical
        self._pil_img = img
        self._render()

    # ------------------------------------------------------------------
    # Print
    # ------------------------------------------------------------------
    def _confirm_print(self, port):
        """Modal confirm dialog; Enter sends, ESC/Close cancels."""
        dlg = tk.Toplevel(self)
        dlg.title("Confirm print")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        result = {"ok": False}

        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Send the label to '{port}'?",
                  justify="center").grid(row=0, column=0, columnspan=2,
                                         pady=(0, 10))
        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2)
        ttk.Button(btns, text="Send", default="active",
                   command=lambda: _finish(True)).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel",
                   command=lambda: _finish(False)).pack(side="left", padx=4)

        def _finish(ok):
            result["ok"] = ok
            dlg.destroy()

        dlg.bind("<Return>", lambda e: _finish(True))
        dlg.bind("<Escape>", lambda e: _finish(False))
        dlg.protocol("WM_DELETE_WINDOW", lambda: _finish(False))
        # Center over the parent
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.focus_set()
        self.wait_window(dlg)
        return result["ok"]

    def _do_print(self):
        port = self.vars["comport"].get().strip()
        if not port:
            messagebox.showwarning(APP_TITLE, "Select a port first.")
            return
        if self._busy:
            return
        # Modal confirmation; ESC cancels.
        if not self._confirm_print(port):
            return
        self._busy = True
        self.print_btn.state(["disabled"])

        def worker():
            ser = None
            try:
                args = self._make_args(no_print=False)
                self._log(f"=> Connecting to {args.comport} ...")
                image = printlabel.build_label(args)
                padded = printlabel.rasterize_label(image, args)
                data = padded.tobytes()
                ser = printlabel.open_printer(args.comport)
                printlabel.do_print_job(ser, args, data)
                self.after(0, lambda: self.status.set("Print job sent."))
            except printlabel._LabelError as e:
                msg = str(e)
                self.after(0, lambda m=msg: messagebox.showerror(APP_TITLE, m))
                self.after(0, lambda m=msg: self.status.set(f"Invalid: {m}"))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: messagebox.showerror(APP_TITLE, m))
                self.after(0, lambda m=msg: self.status.set(f"Error: {m}"))
                self.after(0, lambda m=msg: self._log(f"ERROR: {m}"))
            finally:
                if ser is not None:
                    try:
                        printlabel.reset_printer(ser)
                        ser.close()
                    except Exception:
                        pass
                self._busy = False
                self.after(0, lambda: self.print_btn.state(["!disabled"]))
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def _log(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_ports(self):
        ports = []
        try:
            from serial.tools import list_ports
            ports += [p.device for p in list_ports.comports()]
        except Exception:
            pass
        try:
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "native"))
            from btcommon import list_devices as bt_list, BTError
            for name, addr, chan in bt_list():
                ports.append(f"bt:{name}")
        except Exception:
            pass
        self.port_combo["values"] = ports
        self.status.set(f"{len(ports)} port(s) available.")

    def _add_merge(self):
        fn = filedialog.askopenfilename(
            filetypes=(("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.pdf"),
                       ("All", "*.*")))
        if fn:
            self.merge_list.insert("end", fn)
            self._schedule_preview()

    def _del_merge(self):
        sel = self.merge_list.curselection()
        if sel:
            self.merge_list.delete(sel[0])
            self._schedule_preview()

    def _save_png(self):
        fn = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=(("PNG image", "*.png"),))
        if not fn:
            return
        try:
            args = self._make_args(no_print=True)
            image = printlabel.build_label(args)
            if self.vars["preview_conv"].get():
                printlabel.rasterize_label(image, args).convert("L").save(fn)
            else:
                image.save(fn)
            self.status.set(f"Saved {fn}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def _on_resize(self, _event):
        # Re-fit only in Fit mode and only when the window size really changed
        if self._pil_img is not None and self._zoom_mode.get() == "Fit":
            if not self._resize_scheduled:
                self._resize_scheduled = True
                self.after(150, self._after_resize)

    def _after_resize(self):
        self._resize_scheduled = False
        if self._pil_img is not None and self._zoom_mode.get() == "Fit":
            self._render()

    @staticmethod
    def _default_font():
        if sys.platform == "darwin":
            return MAC_FONT_CHOICES[0]
        return "arial.ttf"

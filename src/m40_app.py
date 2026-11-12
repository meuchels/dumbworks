#!/usr/bin/env python3
"""SmartLF M40 Scanner: standalone app for the Colortrac SmartLF M-series scanner
(Canon iPF MFP M40, USB 148c:0060).

  m40scan                      open the app
  m40scan scan out.pdf [...]   scan one page from the terminal
"""
import os
import queue
import shlex
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m40lib  # noqa: E402

APP_TITLE = "SmartLF M40 Scanner"
FORMATS = [("PDF", "pdf"), ("PNG", "png"), ("JPEG", "jpg"), ("TIFF", "tif")]
RES_LABELS = ["Draft (100 dpi)", "Normal (200 dpi)", "Fine (300 dpi)", "High (400 dpi)",
              "High (600 dpi)", "Max (1200 dpi)"]
RES_VALUES = [100, 200, 300, 400, 600, 1200]


def default_folder():
    try:
        out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        out = ""
    base = out if out and os.path.isdir(out) else os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(base, "Scans")


def save_image(img, path, res=200):
    ext = path.rsplit(".", 1)[-1].lower()
    dpi = (res, res)
    if ext == "pdf":
        img.convert("RGB").save(path, "PDF", resolution=res)
    elif ext in ("jpg", "jpeg"):
        img.convert("RGB").save(path, "JPEG", quality=92, dpi=dpi)
    elif ext in ("tif", "tiff"):
        img.save(path, "TIFF", compression="tiff_lzw", dpi=dpi)
    else:
        img.save(path, "PNG", dpi=dpi)


def app_executable():
    """Path to launch this app again (the AppImage/standalone file if packaged)."""
    for var in ("APPIMAGE",):
        if os.environ.get(var):
            return os.environ[var]
    if getattr(sys, "frozen", False):
        return sys.executable
    return f"{sys.executable} {os.path.abspath(__file__)}"


def install_udev_rule():
    """Write the USB permission rule using pkexec (graphical admin password prompt)."""
    script = (f"printf '%s' {shlex.quote(m40lib.UDEV_RULE)} > {m40lib.UDEV_PATH} && "
              "udevadm control --reload-rules && "
              "udevadm trigger --subsystem-match=usb --attr-match=idVendor=148c")
    for cmd in (["pkexec", "sh", "-c", script],):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            return r.returncode == 0, (r.stderr or r.stdout).strip()
        except FileNotFoundError:
            pass
    return False, "pkexec is not available. Run this in a terminal instead:\n\n" \
                  f"echo '{m40lib.UDEV_RULE.strip().splitlines()[-1]}' | sudo tee {m40lib.UDEV_PATH}\n" \
                  "sudo udevadm control --reload-rules && sudo udevadm trigger"


def add_menu_entry():
    apps = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
    os.makedirs(apps, exist_ok=True)
    path = os.path.join(apps, "m40scan.desktop")
    with open(path, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=SmartLF M40 Scanner\n"
                "Comment=Scan with the Colortrac SmartLF M / Canon iPF MFP M40\n"
                f"Exec={app_executable()}\nIcon=scanner\nTerminal=false\nCategories=Graphics;Scanning;\n")
    return path


# ---------------------------------------------------------------------------
# GUI (Tk): scan -> preview and adjust -> save, like SmartWorks MFP
# ---------------------------------------------------------------------------
# Sizes chosen for touch screens: buttons at least ~48 px tall.
FONT = ("Sans", 14)
FONT_BOLD = ("Sans", 16, "bold")
FONT_SMALL = ("Sans", 11)
PANEL_BG = "#17485a"
PANEL_FG = "#ffffff"
BTN_BG = "#5b9bd5"


def next_name(folder, ext):
    stamp = time.strftime("%Y%m%d")
    n = 1
    while True:
        name = f"Image_{stamp}_{n:04d}"
        if not os.path.exists(os.path.join(folder, f"{name}.{ext}")):
            return name
        n += 1


# ---------------------------------------------------------------------------
# saved defaults (~/.config/dumbworks/settings.json)
# ---------------------------------------------------------------------------
FACTORY = dict(resolution=200, auto_straighten=True, batch=False, margin=0.5, format="PDF",
               folder="", brightness=0, white=1, black=5, sharpen=2)
TONE_KEYS = ("brightness", "white", "black", "sharpen")


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "dumbworks", "settings.json")


def load_settings():
    import json
    s = dict(FACTORY)
    try:
        with open(config_path()) as f:
            saved = json.load(f)
        s.update({k: v for k, v in saved.items() if k in FACTORY})
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    import json
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({k: s[k] for k in FACTORY}, f, indent=2)
    os.replace(tmp, path)
    return path


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from PIL import Image, ImageTk

    import m40_edit
    from m40_deskew import analyze

    class App:
        def __init__(self, root):
            self.root = root
            root.title(APP_TITLE)
            root.geometry("1280x920")
            root.minsize(820, 600)
            self.q = queue.Queue()
            self.cancel = threading.Event()
            self.worker = None
            self.raw = None            # full-resolution scan
            self.proxy = None          # reduced copy for fast preview
            self.settings = load_settings()
            self.adj = self.default_adjust()
            self.saved = True
            self.zoom = 1.0
            self.crop_mode = False
            self.crop_start = None
            self.view = None           # (image shown, x0, y0, scale) for mapping clicks
            self._geom_key = None
            self._geom_img = None
            self._tk_img = None

            st = self.settings
            self.folder = tk.StringVar(value=st["folder"] or default_folder())
            self.fmt = tk.StringVar(value=st["format"] if st["format"] in dict(FORMATS) else "PDF")
            self.margin = tk.DoubleVar(value=float(st["margin"]))
            self.margin_text = tk.StringVar(value=f"{float(st['margin']):.1f}")
            self.res = tk.StringVar(value=RES_LABELS[RES_VALUES.index(st["resolution"])]
                                    if st["resolution"] in RES_VALUES else RES_LABELS[1])
            self.raw_dpi = 200
            self.auto = tk.BooleanVar(value=bool(st["auto_straighten"]))
            self.quick = tk.BooleanVar(value=bool(st["batch"]))
            self.quick_on = bool(st["batch"])
            self.quick.trace_add("write", lambda *_: setattr(self, "quick_on", bool(self.quick.get())))
            self.filename = tk.StringVar(value=next_name(self.folder.get(), dict(FORMATS)[self.fmt.get()]))
            self.status = tk.StringVar(value="Ready. Press Scan, then feed a page.")

            self.build_toolbar()
            bar = ttk.Frame(root)
            bar.pack(side="bottom", fill="x", padx=8, pady=(0, 4))
            ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)
            self.progress = ttk.Progressbar(bar, mode="determinate", length=220)
            self.progress.pack(side="right")
            self.build_savebar()
            body = tk.Frame(root)
            body.pack(fill="both", expand=True)
            self.build_panel(body)
            self.build_canvas(body)

            root.protocol("WM_DELETE_WINDOW", self.on_close)
            root.bind("<Control-s>", lambda e: self.on_save())
            root.after(100, self.poll)
            self.update_panel()

        # ---------------- layout ----------------
        def build_toolbar(self):
            top = ttk.Frame(self.root)
            top.pack(fill="x", padx=8, pady=(8, 2))
            top2 = ttk.Frame(self.root)
            top2.pack(fill="x", padx=8, pady=(2, 8))
            self.scan_btn = ttk.Button(top, text="Scan", command=self.on_scan, width=10, style="Big.TButton")
            self.scan_btn.pack(side="left")
            self.stop_btn = ttk.Button(top, text="Stop", command=self.on_stop, state="disabled", width=8,
                                       style="Big.TButton")
            self.stop_btn.pack(side="left", padx=8)
            ttk.Label(top, text="Resolution:").pack(side="left", padx=(20, 6))
            ttk.Combobox(top, textvariable=self.res, values=RES_LABELS, state="readonly",
                         width=16, font=FONT).pack(side="left", ipady=6)
            ttk.Checkbutton(top2, text="Auto straighten", variable=self.auto).pack(side="left", padx=(0, 20))
            ttk.Button(top2, text="Reset adjustments", command=self.on_reset).pack(side="left", padx=(0, 8))
            ttk.Button(top2, text="Save as default", command=self.on_save_defaults).pack(side="left", padx=(0, 20))
            ttk.Checkbutton(top2, text="Batch (no preview)",
                            variable=self.quick).pack(side="left", padx=4)
            mb = ttk.Menubutton(top, text="Setup", style="Big.TMenubutton")
            menu = tk.Menu(mb, tearoff=False, font=FONT)
            menu.add_command(label="Fix USB permission (asks for admin password)", command=self.on_fix_perm)
            menu.add_command(label="Add to applications menu", command=self.on_menu_entry)
            menu.add_separator()
            menu.add_command(label="Save current settings as default", command=self.on_save_defaults)
            menu.add_command(label="Restore factory defaults", command=self.on_factory_defaults)
            mb["menu"] = menu
            mb.pack(side="right")
            ttk.Button(top2, text="+", width=3, command=lambda: self.bump_margin(0.1)).pack(side="right")
            ttk.Label(top2, textvariable=self.margin_text, width=5, anchor="center").pack(side="right")
            ttk.Button(top2, text="-", width=3, command=lambda: self.bump_margin(-0.1)).pack(side="right")
            ttk.Label(top2, text="Edge margin (in):").pack(side="right", padx=(10, 6))


        def build_savebar(self):
            f = ttk.Frame(self.root)
            f.pack(side="bottom", fill="x", padx=8, pady=8)
            ttk.Label(f, text="File:").pack(side="left")
            ttk.Entry(f, textvariable=self.filename, font=FONT, width=24).pack(side="left", padx=6, ipady=8)
            ttk.Combobox(f, textvariable=self.fmt, values=[x[0] for x in FORMATS], state="readonly",
                         width=6, font=FONT).pack(side="left", ipady=6)
            ttk.Button(f, text="Folder...", command=self.on_browse).pack(side="left", padx=(12, 4))
            ttk.Button(f, text="Open folder", command=self.on_open_folder).pack(side="left", padx=4)
            self.save_btn = tk.Button(f, text="Save", command=self.on_save, bg="#2e9e5b", fg="white",
                                      activebackground="#41b870", relief="flat", font=("Sans", 18, "bold"),
                                      padx=40, pady=8)
            self.save_btn.pack(side="right")
            ttk.Label(f, textvariable=self.folder, foreground="#555", font=FONT_SMALL).pack(
                side="left", padx=10, fill="x", expand=True)

        def bump_margin(self, d):
            v = round(min(3.0, max(0.1, float(self.margin.get()) + d)), 1)
            self.margin.set(v)
            self.margin_text.set(f"{v:.1f}")

        def build_panel(self, parent):
            p = tk.Frame(parent, bg=PANEL_BG, width=360)
            p.pack(side="right", fill="y")
            p.pack_propagate(False)
            self.panel = p
            self.value_labels = {}

            def btn(parent, text, cmd, width=3):
                return tk.Button(parent, text=text, command=cmd, width=width, bg=BTN_BG, fg="white",
                                 activebackground="#7fb3e0", relief="flat", font=FONT_BOLD,
                                 padx=8, pady=9)

            def caption(text):
                tk.Label(p, text=text, bg=PANEL_BG, fg=PANEL_FG, font=FONT_SMALL).pack(pady=(1, 4))

            def stepper(key, label, minus="-", plus="+"):
                row = tk.Frame(p, bg=PANEL_BG)
                row.pack(pady=(8, 0))
                btn(row, minus, lambda: self.step(key, -1)).pack(side="left")
                v = tk.Label(row, text="", width=9, bg=PANEL_BG, fg=PANEL_FG, font=FONT)
                v.pack(side="left", padx=6)
                btn(row, plus, lambda: self.step(key, +1)).pack(side="left")
                self.value_labels[key] = v
                caption(label)

            stepper("brightness", "Brightness")
            stepper("white", "White Point")
            stepper("black", "Black Point")
            stepper("sharpen", "Sharpening")

            row = tk.Frame(p, bg=PANEL_BG)
            row.pack(pady=(8, 0))
            btn(row, "<", lambda: self.rotate_by(-0.1)).pack(side="left")
            v = tk.Label(row, text="", width=9, bg=PANEL_BG, fg=PANEL_FG, font=FONT)
            v.pack(side="left", padx=6)
            btn(row, ">", lambda: self.rotate_by(+0.1)).pack(side="left")
            self.value_labels["angle"] = v
            caption("Deskew (0.1° steps)")

            row = tk.Frame(p, bg=PANEL_BG)
            row.pack(pady=(4, 0))
            self.crop_btn = btn(row, "Crop", self.on_crop_mode, width=5)
            self.crop_btn.pack(side="left", padx=3)
            btn(row, "Auto", self.on_auto, width=5).pack(side="left", padx=3)
            btn(row, "Full", self.on_full, width=5).pack(side="left", padx=3)
            caption("Crop: drag on the preview")

            row = tk.Frame(p, bg=PANEL_BG)
            row.pack(pady=(4, 0))
            self.inv_btn = btn(row, "Invert", lambda: self.toggle("invert"), width=8)
            self.inv_btn.pack(side="left", padx=4)
            self.mir_btn = btn(row, "Mirror", lambda: self.toggle("mirror"), width=8)
            self.mir_btn.pack(side="left", padx=4)
            caption("")

            row = tk.Frame(p, bg=PANEL_BG)
            row.pack(pady=(0, 0))
            btn(row, "-", lambda: self.set_zoom(self.zoom / 1.5)).pack(side="left", padx=3)
            btn(row, "Fit", lambda: self.set_zoom(1.0), width=4).pack(side="left", padx=3)
            btn(row, "+", lambda: self.set_zoom(self.zoom * 1.5)).pack(side="left", padx=3)
            caption("Zoom")



        def build_canvas(self, parent):
            frame = tk.Frame(parent)
            frame.pack(side="left", fill="both", expand=True)
            self.canvas = tk.Canvas(frame, background="#9a9a9a", highlightthickness=0, cursor="arrow")
            xs = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
            ys = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
            ys.pack(side="right", fill="y")
            xs.pack(side="bottom", fill="x")
            self.canvas.pack(fill="both", expand=True)
            self.canvas.bind("<Configure>", lambda e: self.render())
            self.canvas.bind("<ButtonPress-1>", self.on_press)
            self.canvas.bind("<B1-Motion>", self.on_drag)
            self.canvas.bind("<ButtonRelease-1>", self.on_release)
            self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
            self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))
            self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        # ---------------- adjustments ----------------
        def bars(self, v, lo, hi):
            n = round(5 * (v - lo) / (hi - lo)) if hi > lo else 0
            return "|" * n + "." * (5 - n)

        def update_panel(self):
            a = self.adj
            for key, (lo, hi) in m40_edit.LIMITS.items():
                v = getattr(a, key)
                self.value_labels[key].configure(text=f"{v}  {self.bars(v, lo, hi)}")
            self.value_labels["angle"].configure(text=f"{a.angle:+.1f}°")
            self.inv_btn.configure(relief="sunken" if a.invert else "flat",
                                   bg="#2c6aa0" if a.invert else BTN_BG)
            self.mir_btn.configure(relief="sunken" if a.mirror else "flat",
                                   bg="#2c6aa0" if a.mirror else BTN_BG)
            self.crop_btn.configure(bg="#e0a030" if self.crop_mode else BTN_BG)

        def changed(self):
            self.saved = False
            self.update_panel()
            self.render()

        def step(self, key, d):
            lo, hi = m40_edit.LIMITS[key]
            setattr(self.adj, key, max(lo, min(hi, getattr(self.adj, key) + d)))
            self.changed()

        def rotate_by(self, d):
            self.adj.angle = round(self.adj.angle + d, 2)
            self.changed()

        def toggle(self, key):
            setattr(self.adj, key, not getattr(self.adj, key))
            if key == "mirror":
                l, t, r, b = self.adj.box
                self.adj.box = (1 - r, t, 1 - l, b)
            self.changed()

        def on_auto(self):
            if self.raw is None:
                return
            if not self.adj.auto:
                self.status.set("Measuring skew...")
                self.root.update_idletasks()
                ang, box = analyze(self.proxy)
                self.adj.auto = {"angle": ang, "box": box}
            self.adj.angle = self.adj.auto["angle"]
            self.adj.box = self.adj.auto["box"]
            if self.adj.mirror:
                l, t, r, b = self.adj.box
                self.adj.box = (1 - r, t, 1 - l, b)
            self.status.set(f"Straightened by {self.adj.angle:+.2f}° and cropped to the paper.")
            self.changed()

        def on_full(self):
            self.adj.box = (0.0, 0.0, 1.0, 1.0)
            self.changed()

        def default_adjust(self, **kw):
            return m40_edit.Adjust(**{k: int(self.settings[k]) for k in TONE_KEYS}, **kw)

        def current_settings(self):
            try:
                margin = round(float(self.margin.get()), 1)
            except (tk.TclError, ValueError):
                margin = FACTORY["margin"]
            s = dict(resolution=RES_VALUES[RES_LABELS.index(self.res.get())],
                     auto_straighten=bool(self.auto.get()), batch=bool(self.quick.get()),
                     margin=margin, format=self.fmt.get(), folder=self.folder.get())
            s.update({k: getattr(self.adj, k) for k in TONE_KEYS})
            return s

        def on_save_defaults(self):
            new = self.current_settings()
            if not messagebox.askyesno(
                    APP_TITLE, "Save these as the defaults used every time the app starts?\n\n"
                    f"Resolution: {new['resolution']} dpi\n"
                    f"Auto straighten: {'on' if new['auto_straighten'] else 'off'}    "
                    f"Batch: {'on' if new['batch'] else 'off'}\n"
                    f"Edge margin: {new['margin']:.1f} in\n"
                    f"Format: {new['format']}\nFolder: {new['folder']}\n"
                    f"Brightness {new['brightness']}, White point {new['white']}, "
                    f"Black point {new['black']}, Sharpening {new['sharpen']}"):
                return
            try:
                path = save_settings(new)
            except OSError as e:
                messagebox.showerror(APP_TITLE, f"Could not save the defaults: {e}")
                return
            self.settings = new
            self.status.set(f"Defaults saved ({path}).")

        def on_factory_defaults(self):
            if not messagebox.askyesno(APP_TITLE, "Restore the original factory settings and forget your "
                                       "saved defaults?"):
                return
            try:
                os.remove(config_path())
            except OSError:
                pass
            self.settings = dict(FACTORY)
            st = self.settings
            self.res.set(RES_LABELS[RES_VALUES.index(st["resolution"])])
            self.auto.set(st["auto_straighten"])
            self.quick.set(st["batch"])
            self.margin.set(st["margin"])
            self.margin_text.set(f"{st['margin']:.1f}")
            self.fmt.set(st["format"])
            self.folder.set(default_folder())
            self.on_reset()
            self.status.set("Factory defaults restored.")

        def on_reset(self):
            auto = self.adj.auto
            self.adj = self.default_adjust(auto=auto)
            if auto:
                self.adj.angle, self.adj.box = auto["angle"], auto["box"]
            self.changed()

        def set_zoom(self, z):
            self.zoom = max(1.0, min(z, 16.0))
            self.render()

        # ---------------- crop by dragging ----------------
        def on_crop_mode(self):
            if self.raw is None:
                return
            self.crop_mode = not self.crop_mode
            self.canvas.configure(cursor="crosshair" if self.crop_mode else "arrow")
            self.status.set("Drag a rectangle on the preview to crop." if self.crop_mode else "")
            self.update_panel()
            self.render()

        def on_press(self, e):
            if not self.crop_mode:
                self.canvas.scan_mark(e.x, e.y)   # drag to pan when zoomed in
                return
            if self.crop_mode and self.view:
                self.crop_start = (self.canvas.canvasx(e.x), self.canvas.canvasy(e.y))
                self.canvas.delete("croprect")

        def on_drag(self, e):
            if not self.crop_mode:
                if self.zoom > 1.0:
                    self.canvas.scan_dragto(e.x, e.y, gain=1)
                return
            if self.crop_mode and self.crop_start:
                x0, y0 = self.crop_start
                self.canvas.delete("croprect")
                self.canvas.create_rectangle(x0, y0, self.canvas.canvasx(e.x), self.canvas.canvasy(e.y),
                                             outline="#ff3030", width=2, dash=(6, 3), tags="croprect")

        def on_release(self, e):
            if not (self.crop_mode and self.crop_start and self.view):
                return
            x0, y0 = self.crop_start
            x1, y1 = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
            self.crop_start = None
            w, h, ox, oy = self.view
            fx = sorted(((x0 - ox) / w, (x1 - ox) / w))
            fy = sorted(((y0 - oy) / h, (y1 - oy) / h))
            fx = [min(1, max(0, v)) for v in fx]
            fy = [min(1, max(0, v)) for v in fy]
            if fx[1] - fx[0] < 0.02 or fy[1] - fy[0] < 0.02:
                return
            self.adj.box = (fx[0], fy[0], fx[1], fy[1])
            self.crop_mode = False
            self.canvas.configure(cursor="arrow")
            self.status.set("Cropped. Use Full to undo the crop.")
            self.changed()

        # ---------------- preview ----------------
        def render(self):
            self.canvas.delete("all")
            if self.proxy is None:
                self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                        text="Press Scan, then feed a page", fill="white", font=("Sans", 16))
                return
            a = self.adj
            shown = m40_edit.Adjust(**{**a.__dict__})
            if self.crop_mode:
                shown.box = (0.0, 0.0, 1.0, 1.0)
            key = (shown.angle, shown.mirror, shown.box)
            if key != self._geom_key:
                self._geom_img = m40_edit.geometry(self.proxy, shown)
                self._geom_key = key
            img = m40_edit.tone(self._geom_img, shown, scale=self.proxy_scale, dpi=self.raw_dpi)
            cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
            fit = min((cw - 10) / img.width, (ch - 10) / img.height)
            s = fit * self.zoom
            w, h = max(1, int(img.width * s)), max(1, int(img.height * s))
            disp = img.resize((w, h), Image.BILINEAR if s < 1 else Image.NEAREST)
            self._tk_img = ImageTk.PhotoImage(disp)
            ox, oy = max(5, (cw - w) // 2), max(5, (ch - h) // 2)
            self.canvas.create_image(ox, oy, image=self._tk_img, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, max(cw, w + 10), max(ch, h + 10)))
            self.view = (w, h, ox, oy)
            if self.crop_mode and a.box != (0.0, 0.0, 1.0, 1.0):
                l, t, r, b = a.box
                self.canvas.create_rectangle(ox + l * w, oy + t * h, ox + r * w, oy + b * h,
                                             outline="#ffb020", width=2, tags="croprect")

        def load_scan(self, img):
            self.raw = img
            f = min(1.0, 1600 / max(img.size))
            self.proxy = img if f >= 1 else img.resize((int(img.width * f), int(img.height * f)), Image.BILINEAR)
            self.proxy_scale = f
            self.adj = self.default_adjust()
            self._geom_key = None
            self.crop_mode = False
            self.zoom = 1.0
            self.saved = False
            if self.auto.get():
                self.on_auto()
            self.update_panel()
            self.render()

        # ---------------- scanning ----------------
        def busy(self, on):
            self.scan_btn.configure(state="disabled" if on else "normal")
            self.stop_btn.configure(state="normal" if on else "disabled")
            if on:
                self.progress.configure(mode="indeterminate")
                self.progress.start(15)
            else:
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)

        def on_scan(self):
            if self.worker and self.worker.is_alive():
                return
            if self.raw is not None and not self.saved and not self.quick_on:
                if not messagebox.askyesno(APP_TITLE, "The current scan hasn't been saved. Scan a new page "
                                           "and discard it?"):
                    return
            try:
                margin = float(self.margin.get())
            except (tk.TclError, ValueError):
                margin = 0.5
            self.cancel.clear()
            self.busy(True)
            dpi = RES_VALUES[RES_LABELS.index(self.res.get())]
            if dpi >= 600 and not self.confirm_big(dpi, margin):
                self.busy(False)
                return
            opts = dict(margin=margin, dpi=dpi, folder=self.folder.get() or default_folder(),
                        ext=dict(FORMATS)[self.fmt.get()], auto=self.auto.get(),
                        adjust=self.default_adjust())
            self.worker = threading.Thread(target=self.work, args=(opts,), daemon=True)
            self.worker.start()

        def work(self, o):
            put = self.q.put
            sc = m40lib.Scanner(log=lambda m: put(("status", m)))
            try:
                put(("status", "Connecting to the scanner..."))
                sc.open(cancel=self.cancel)
                while True:
                    put(("status", "Feed a page into the scanner..."))
                    sc.wait_for_paper(cancel=self.cancel)
                    put(("status", "Paper detected, scanning..."))
                    dpi = o["dpi"]
                    width, lines, raw = sc.scan(margin_in=o["margin"], dpi=dpi,
                                                progress=lambda n: put(("lines", n / dpi)))
                    if lines == 0:
                        raise m40lib.ScannerError("The scanner returned no image data.")
                    img = Image.frombytes("RGB", (width, lines), raw)
                    if not self.quick_on:
                        put(("scan", (img, dpi)))
                        break
                    # batch mode: default adjustments, auto straighten, save, next page
                    a = m40_edit.Adjust(**{**o["adjust"].__dict__})
                    if o["auto"]:
                        a.angle, a.box = analyze(img)
                    out = m40_edit.apply(img, a, dpi)
                    os.makedirs(o["folder"], exist_ok=True)
                    path = os.path.join(o["folder"], next_name(o["folder"], o["ext"]) + "." + o["ext"])
                    save_image(out, path, dpi)
                    put(("batchsaved", (img, dpi, path)))
                    while sc.paper_state() != 0 and not self.cancel.is_set():
                        time.sleep(0.2)
                    if not self.quick_on or self.cancel.is_set():
                        break
            except m40lib.Cancelled:
                put(("status", "Stopped."))
            except m40lib.PermissionProblem:
                put(("perm", None))
            except Exception as e:
                put(("error", str(e)))
            finally:
                sc.close()
                put(("done", None))

        def confirm_big(self, dpi, margin):
            return messagebox.askyesno(
                APP_TITLE, f"{dpi} dpi makes very large images (a 36-inch-wide sheet at {dpi} dpi is "
                f"about {36 * dpi * 3 * dpi * 12 / 1e9:.1f} GB per foot of length in memory).\n\n"
                "Fine for letter/tabloid pages; for full-width drawings use 400 dpi or less. Continue?")

        def on_save(self):
            if self.raw is None:
                return
            name = self.filename.get().strip() or next_name(self.folder.get(), "pdf")
            ext = dict(FORMATS)[self.fmt.get()]
            folder = self.folder.get() or default_folder()
            path = os.path.join(folder, name if name.lower().endswith("." + ext) else f"{name}.{ext}")
            if os.path.exists(path) and not messagebox.askyesno(APP_TITLE, f"{path} exists. Replace it?"):
                return
            self.save_btn.configure(state="disabled")
            self.status.set("Saving...")
            raw, adj, dpi = self.raw, m40_edit.Adjust(**{**self.adj.__dict__}), self.raw_dpi

            def job():
                try:
                    os.makedirs(folder, exist_ok=True)
                    save_image(m40_edit.apply(raw, adj, dpi), path, dpi)
                    self.q.put(("saved", path))
                except Exception as e:
                    self.q.put(("error", f"Could not save: {e}"))
            threading.Thread(target=job, daemon=True).start()

        def poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "status":
                        self.status.set(val)
                    elif kind == "lines":
                        self.progress.stop()
                        inches = val
                        self.progress.configure(mode="determinate", maximum=36, value=min(inches, 36))
                        self.status.set(f"Scanning... {inches:.1f} in")
                    elif kind == "scan":
                        self.raw_dpi = val[1]
                        self.load_scan(val[0])
                        self.filename.set(next_name(self.folder.get(), dict(FORMATS)[self.fmt.get()]))
                        self.status.set("Scan ready. Adjust if needed, then Save.")
                    elif kind == "batchsaved":
                        img, self.raw_dpi, path = val
                        self.load_scan(img)
                        self.saved = True
                        self.status.set(f"Saved {path}. Feed the next page...")
                    elif kind == "saved":
                        self.saved = True
                        self.save_btn.configure(state="normal")
                        self.status.set("Saved " + val)
                        self.filename.set(next_name(self.folder.get(), dict(FORMATS)[self.fmt.get()]))
                    elif kind == "perm":
                        self.busy(False)
                        self.status.set("No permission to use the scanner yet.")
                        if messagebox.askyesno(APP_TITLE, "This user isn't allowed to use the scanner's USB "
                                               "device yet.\n\nInstall a USB permission rule now? You'll be "
                                               "asked for an administrator password."):
                            self.on_fix_perm()
                    elif kind == "error":
                        self.busy(False)
                        self.save_btn.configure(state="normal")
                        self.status.set("Error: " + val)
                        messagebox.showerror(APP_TITLE, val)
                    elif kind == "done":
                        self.busy(False)
            except queue.Empty:
                pass
            self.root.after(100, self.poll)

        # ---------------- misc ----------------
        def on_browse(self):
            d = filedialog.askdirectory(initialdir=self.folder.get() or os.path.expanduser("~"))
            if d:
                self.folder.set(d)
                self.filename.set(next_name(d, dict(FORMATS)[self.fmt.get()]))

        def on_open_folder(self):
            os.makedirs(self.folder.get(), exist_ok=True)
            subprocess.Popen(["xdg-open", self.folder.get()])

        def on_fix_perm(self):
            ok, msg = install_udev_rule()
            if ok:
                messagebox.showinfo(APP_TITLE, "USB permission installed.\n\nUnplug the scanner's USB "
                                    "cable, plug it back in, then press Scan.")
            else:
                messagebox.showerror(APP_TITLE, "Could not install the USB permission rule.\n\n" + msg)

        def on_menu_entry(self):
            messagebox.showinfo(APP_TITLE, f"Added to the applications menu:\n{add_menu_entry()}")

        def on_stop(self):
            self.cancel.set()
            self.status.set("Stopping... (a page already feeding finishes first)")

        def on_close(self):
            if self.raw is not None and not self.saved:
                if not messagebox.askyesno(APP_TITLE, "The current scan hasn't been saved. Quit anyway?"):
                    return
            self.cancel.set()
            self.root.destroy()

    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.option_add("*Font", FONT)
    root.option_add("*TCombobox*Listbox.font", FONT)
    style.configure(".", font=FONT)
    style.configure("TButton", padding=(12, 8))
    style.configure("Big.TButton", font=FONT_BOLD, padding=(18, 12))
    style.configure("Big.TMenubutton", font=FONT_BOLD, padding=(14, 10))
    style.configure("TCheckbutton", padding=(4, 8), indicatorsize=22)
    style.configure("TCombobox", arrowsize=26, padding=4)
    style.configure("Vertical.TScrollbar", arrowsize=24)
    style.configure("Horizontal.TScrollbar", arrowsize=24)
    style.configure("TProgressbar", thickness=18)
    root.app = App(root)
    root.mainloop()


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def run_cli(argv):
    import argparse

    from PIL import Image
    ap = argparse.ArgumentParser(prog="m40scan scan", description="Scan one page from the SmartLF M40 scanner")
    ap.add_argument("out", nargs="?", default="scan.png", help="output file (.pdf .png .jpg .tif)")
    st = load_settings()
    ap.add_argument("--margin", type=float, default=float(st["margin"]),
                    help=f"extra width each side, inches (default {float(st['margin']):.1f})")
    ap.add_argument("--no-deskew", action="store_true", help="skip straightening and cropping")
    ap.add_argument("--dpi", type=int, default=int(st["resolution"]), choices=m40lib.RESOLUTIONS,
                    help=f"resolution (default {st['resolution']})")
    a = ap.parse_args(argv)
    sc = m40lib.Scanner()
    try:
        sc.open()
        print("Feed a page…", flush=True)
        sc.wait_for_paper()
        width, lines, raw = sc.scan(margin_in=a.margin, dpi=a.dpi,
                                    progress=lambda n: print(f"  {n / a.dpi:.1f} in", end="\r", flush=True))
        print()
    except m40lib.PermissionProblem as e:
        sys.exit(f"{e}\nRun `m40scan setup-usb` once (needs admin), then unplug and replug the scanner.")
    except m40lib.ScannerError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        sys.exit("Stopped.")
    finally:
        sc.close()
    import m40_edit
    img = Image.frombytes("RGB", (width, lines), raw)
    adj = m40_edit.Adjust(**{k: int(st[k]) for k in TONE_KEYS})   # saved default adjustments
    if not a.no_deskew:
        from m40_deskew import analyze
        adj.angle, adj.box = analyze(img)
    save_image(m40_edit.apply(img, adj, a.dpi), a.out, a.dpi)
    print("Saved", a.out)


def main():
    args = sys.argv[1:]
    if args and args[0] == "scan":
        run_cli(args[1:])
    elif args and args[0] == "setup-usb":
        ok, msg = install_udev_rule()
        print("USB permission installed. Unplug and replug the scanner." if ok else msg)
    elif args and args[0] in ("-h", "--help", "help"):
        print(__doc__.strip() + "\n  m40scan setup-usb            install the USB permission rule (admin)")
    else:
        run_gui()


if __name__ == "__main__":
    main()

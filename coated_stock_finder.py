"""
Coated Stock Finder — FiveNine Optics
Search the coated stock inventory by wavelength and/or transmission.
Double-click a row to open the corresponding AB (After Bake) scan PDF.

Usage:
    python coated_stock_finder.py
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.coated_stock_parser import load_coated_stock, search, CoatedItem
from utils.scan_index import ScanIndex


class _ScanPickerDialog(tk.Toplevel):
    """Small modal dialog to choose among multiple AB scan files."""

    def __init__(self, parent: tk.Widget, run: str, files: list[Path]):
        super().__init__(parent)
        self.title(f"Scan files — {run}")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        ttk.Label(self, text=f"Multiple scans found for {run}.\nSelect one to open:",
                  padding=(12, 8)).pack()

        lb_frame = ttk.Frame(self)
        lb_frame.pack(fill=tk.BOTH, padx=12, pady=4)
        self._lb = tk.Listbox(lb_frame, width=60, height=min(len(files), 12),
                               selectmode=tk.SINGLE, activestyle="dotbox")
        sb = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL, command=self._lb.yview)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._files = files
        for f in files:
            self._lb.insert(tk.END, f.name)
        self._lb.selection_set(0)
        self._lb.bind("<Double-Button-1>", lambda _: self._open())

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=12, pady=(4, 12))
        ttk.Button(btns, text="Open", command=self._open).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

    def _open(self) -> None:
        sel = self._lb.curselection()
        if sel:
            os.startfile(str(self._files[sel[0]]))
        self.destroy()


class CoatedStockFinder(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Coated Stock Finder  —  FiveNine Optics")
        self.geometry("1280x740")
        self.resizable(True, True)
        self._items: list[CoatedItem] = []
        self._scan_index = ScanIndex()
        self._build_ui()
        self._load_data()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        p = {"padx": 6, "pady": 4}

        # ── Search inputs ──────────────────────────────────────────────────
        sf = ttk.LabelFrame(self, text="Search", padding=10)
        sf.pack(fill=tk.X, padx=10, pady=(10, 4))

        ttk.Label(sf, text="Wavelength:").grid(row=0, column=0, sticky="e", **p)
        self._wl_min_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self._wl_min_var, width=8).grid(
            row=0, column=1, sticky="w", **p)
        ttk.Label(sf, text="nm  to").grid(row=0, column=2, sticky="w", **p)
        self._wl_max_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self._wl_max_var, width=8).grid(
            row=0, column=3, sticky="w", **p)
        ttk.Label(sf, text="nm  (leave 'to' blank for single wavelength ±10 nm)",
                  foreground="gray").grid(row=0, column=4, sticky="w", padx=4)

        ttk.Label(sf, text="Transmission:").grid(row=1, column=0, sticky="e", **p)
        self._trans_min_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self._trans_min_var, width=8).grid(
            row=1, column=1, sticky="w", **p)
        ttk.Label(sf, text="ppm  to").grid(row=1, column=2, sticky="w", **p)
        self._trans_max_var = tk.StringVar()
        ttk.Entry(sf, textvariable=self._trans_max_var, width=8).grid(
            row=1, column=3, sticky="w", **p)
        ttk.Label(sf, text="ppm  (optional — only filters items with explicit T= values)",
                  foreground="gray").grid(row=1, column=4, sticky="w", padx=4)

        btn_frame = ttk.Frame(sf)
        btn_frame.grid(row=0, column=5, rowspan=2, padx=(16, 0), sticky="ns")
        ttk.Button(btn_frame, text="Search", command=self._do_search, width=10).pack(
            side=tk.TOP, pady=2)
        ttk.Button(btn_frame, text="Show all", command=self._show_all, width=10).pack(
            side=tk.TOP, pady=2)

        # ── Status bar ────────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Loading…")
        ttk.Label(self, textvariable=self._status_var, foreground="gray").pack(
            anchor="w", padx=12, pady=(0, 2))

        # ── Results table ─────────────────────────────────────────────────
        rf = ttk.Frame(self)
        rf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        cols = ("id", "material", "size", "roc",
                "hr_run", "ar_run", "qty",
                "wavelength", "trans_ppm", "notes", "customer")
        headings = {
            "id":         ("ID",          48),
            "material":   ("Material",    72),
            "size":       ("Size",        68),
            "roc":        ("ROC",        140),
            "hr_run":     ("HR Run",      92),
            "ar_run":     ("AR Run",      92),
            "qty":        ("QTY",         48),
            "wavelength": ("Wavelength", 130),
            "trans_ppm":  ("T (ppm)",     72),
            "notes":      ("Notes",      310),
            "customer":   ("Customer",   170),
        }

        self._tree = ttk.Treeview(rf, columns=cols, show="headings", height=28,
                                   selectmode="browse")
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            anchor = "center" if col in ("id", "qty", "trans_ppm") else "w"
            self._tree.column(col, width=width, anchor=anchor, stretch=(col == "notes"))

        vsb = ttk.Scrollbar(rf, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(rf, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        rf.rowconfigure(0, weight=1)
        rf.columnconfigure(0, weight=1)

        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<Return>", lambda _: self._do_search())

        # Hint label
        ttk.Label(self, text="Double-click a row to open its HR AB scan",
                  foreground="gray").pack(anchor="w", padx=12, pady=(0, 4))

    # ── Data ────────────────────────────────────────────────────────────────

    def _load_data(self) -> None:
        try:
            self._items = load_coated_stock()
        except Exception as e:
            self._status_var.set(f"Error loading workbook: {e}")
            messagebox.showerror("Load error", str(e))
            return

        self._populate_table(self._items)
        self._status_var.set(
            f"Loaded {len(self._items)} items. Indexing scans…")

        # Build scan index in background so the UI stays responsive
        threading.Thread(target=self._build_index, daemon=True).start()

    def _build_index(self) -> None:
        def _progress(msg: str) -> None:
            self.after(0, lambda m=msg: self._status_var.set(m))

        self._scan_index.build(progress=_progress)

    # ── Search ───────────────────────────────────────────────────────────────

    def _parse_inputs(self):
        def _f(s):
            s = s.strip()
            return float(s) if s else None
        return (
            _f(self._wl_min_var.get()),
            _f(self._wl_max_var.get()),
            _f(self._trans_min_var.get()),
            _f(self._trans_max_var.get()),
        )

    def _do_search(self) -> None:
        try:
            wl_min, wl_max, tr_min, tr_max = self._parse_inputs()
        except ValueError:
            messagebox.showerror("Input error",
                                 "Enter numeric values for wavelength / transmission.")
            return

        if wl_min is None and tr_min is None and tr_max is None:
            self._show_all()
            return

        results = search(self._items, wl_min, wl_max, tr_min, tr_max)
        self._populate_table(results)
        self._status_var.set(f"{len(results)} match{'es' if len(results) != 1 else ''} found.")

    def _show_all(self) -> None:
        self._populate_table(self._items)
        self._status_var.set(f"Showing all {len(self._items)} items.")

    # ── Table ────────────────────────────────────────────────────────────────

    def _populate_table(self, items: list[CoatedItem]) -> None:
        for row in self._tree.get_children():
            self._tree.delete(row)

        for item in items:
            if item.wl_min is not None and item.wl_max is not None and item.wl_min != item.wl_max:
                wl_str = f"{item.wl_min:.0f} – {item.wl_max:.0f} nm"
            elif item.wl_min is not None:
                wl_str = f"{item.wl_min:.0f} nm"
            else:
                wl_str = "—"

            trans_str = f"{item.transmission_ppm:.0f}" if item.transmission_ppm is not None else "—"

            self._tree.insert("", tk.END, iid=str(item.row_id), values=(
                item.row_id, item.material, item.size, item.roc,
                item.hr_run, item.ar_run, item.qty,
                wl_str, trans_str, item.notes, item.customer,
            ))

    # ── Open scan ────────────────────────────────────────────────────────────

    def _on_double_click(self, event) -> None:
        iid = self._tree.identify_row(event.y)
        if not iid:
            return

        # Find the item by row_id (iid)
        item = next((it for it in self._items if str(it.row_id) == iid), None)
        if item is None:
            return

        hr_run = item.hr_run
        if not hr_run:
            messagebox.showinfo("No run", "This item has no HR run number.")
            return

        if not self._scan_index.ready:
            messagebox.showinfo("Indexing",
                                "Scan index is still building. Please wait a moment.")
            return

        files = self._scan_index.lookup(hr_run)

        if not files:
            messagebox.showinfo("Not found",
                                f"No AB scan found for {hr_run}.\n"
                                "Check: PE scans cloud folder.")
            return

        if len(files) == 1:
            os.startfile(str(files[0]))
        else:
            _ScanPickerDialog(self, hr_run, files)


if __name__ == "__main__":
    app = CoatedStockFinder()
    app.mainloop()

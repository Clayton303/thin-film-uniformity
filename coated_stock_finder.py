"""
Coated Stock Finder — FiveNine Optics
Search the coated stock inventory by wavelength and/or transmission.

Usage:
    python coated_stock_finder.py
"""

import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.coated_stock_parser import load_coated_stock, search, CoatedItem


class CoatedStockFinder(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Coated Stock Finder  —  FiveNine Optics")
        self.geometry("1280x740")
        self.resizable(True, True)
        self._items: list[CoatedItem] = []
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

        self.bind("<Return>", lambda _: self._do_search())

    # ── Data ────────────────────────────────────────────────────────────────

    def _load_data(self) -> None:
        try:
            self._items = load_coated_stock()
            self._status_var.set(
                f"Loaded {len(self._items)} items. Enter wavelength and click Search.")
            self._populate_table(self._items)
        except Exception as e:
            self._status_var.set(f"Error loading workbook: {e}")
            messagebox.showerror("Load error", str(e))

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

            self._tree.insert("", tk.END, values=(
                item.row_id, item.material, item.size, item.roc,
                item.hr_run, item.ar_run, item.qty,
                wl_str, trans_str, item.notes, item.customer,
            ))


if __name__ == "__main__":
    app = CoatedStockFinder()
    app.mainloop()

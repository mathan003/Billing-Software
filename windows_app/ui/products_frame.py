import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
try:
    from .. import db_local
except (ImportError, ValueError):
    import db_local


class ProductsFrame(ctk.CTkFrame):
    """
    Local Products Catalog View.
    Shows all products available offline with their prices and stock levels.
    """
    def __init__(self, parent, sync_manager):
        super().__init__(parent, fg_color="transparent")
        self.sync_manager = sync_manager
        self.products = []

        self._build_ui()
        self.load_products()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header
        top_bar = ctk.CTkFrame(self, fg_color=("gray95", "gray20"), corner_radius=10)
        top_bar.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))

        ctk.CTkLabel(top_bar, text="Product Catalog & Local Inventory", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left", padx=14, pady=10)

        # Pull from server button
        self.pull_btn = ctk.CTkButton(top_bar, text="⬇️ Pull Latest from Cloud", width=160, command=self._pull_catalog, fg_color="#3b82f6")
        self.pull_btn.pack(side="right", padx=10, pady=8)

        # Search bar
        filter_bar = ctk.CTkFrame(self, fg_color="transparent")
        filter_bar.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 6))

        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(filter_bar, textvariable=self.search_var, width=320)
        self.search_entry.pack(side="left", padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda e: self.load_products())

        ctk.CTkButton(filter_bar, text="Refresh", width=80, command=self.load_products, fg_color="gray50").pack(side="left")

        # Table Card
        table_card = ctk.CTkFrame(self, corner_radius=10)
        table_card.grid(row=2, column=0, sticky="nsew", padx=0, pady=(4, 0))
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        columns = ("sku", "name", "category", "price", "tax", "stock", "unit")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("sku", text="SKU / Barcode")
        self.tree.heading("name", text="Product Name")
        self.tree.heading("category", text="Category")
        self.tree.heading("price", text="Price (₹)")
        self.tree.heading("tax", text="Tax %")
        self.tree.heading("stock", text="Stock")
        self.tree.heading("unit", text="Unit")

        self.tree.column("sku", width=120, anchor="w")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("category", width=110, anchor="w")
        self.tree.column("price", width=90, anchor="e")
        self.tree.column("tax", width=60, anchor="center")
        self.tree.column("stock", width=80, anchor="center")
        self.tree.column("unit", width=60, anchor="center")

        scrollbar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)

    def load_products(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        search = self.search_var.get().strip() or None
        self.products = db_local.get_all_products(search=search)

        for p in self.products:
            self.tree.insert("", "end", values=(
                p["sku"],
                p["name"],
                p.get("category", "General"),
                f"₹{p['price']:.2f}",
                f"{p['tax_percent']:.0f}%",
                f"{p['stock_quantity']:.0f}",
                p.get("unit", "pcs"),
            ))

    def _pull_catalog(self):
        self.sync_manager.force_sync()
        self.after(1200, self.load_products)

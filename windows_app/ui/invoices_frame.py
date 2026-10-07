import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
try:
    from .. import db_local
    from .billing_frame import ReceiptDialog
except (ImportError, ValueError):
    import db_local
    from ui.billing_frame import ReceiptDialog


class InvoicesFrame(ctk.CTkFrame):
    """
    Invoices & Bills Management Frame.
    Lists all local transactions with their synchronization status (SYNCED vs PENDING).
    """
    def __init__(self, parent, sync_manager):
        super().__init__(parent, fg_color="transparent")
        self.sync_manager = sync_manager
        self.invoices = []

        self._build_ui()
        self.load_invoices()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header controls
        top_bar = ctk.CTkFrame(self, fg_color=("gray95", "gray20"), corner_radius=10)
        top_bar.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 10))

        ctk.CTkLabel(top_bar, text="Invoice History & Sync Status", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left", padx=14, pady=10)

        # Sync Now button
        self.sync_btn = ctk.CTkButton(top_bar, text="🔄 Sync Now", width=100, command=self._on_sync_click, fg_color="#3b82f6")
        self.sync_btn.pack(side="right", padx=10, pady=8)

        # View Receipt button
        self.view_btn = ctk.CTkButton(top_bar, text="📄 View Receipt", width=110, command=self._view_selected_receipt, fg_color="#10b981")
        self.view_btn.pack(side="right", padx=6, pady=8)

        # Filter controls
        filter_bar = ctk.CTkFrame(self, fg_color="transparent")
        filter_bar.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 6))

        # Search box
        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(filter_bar, textvariable=self.search_var, width=260)
        self.search_entry.pack(side="left", padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda e: self.load_invoices())

        # Status filter
        self.status_var = tk.StringVar(value="ALL")
        self.status_combo = ctk.CTkComboBox(filter_bar, values=["ALL", "SYNCED", "PENDING"], variable=self.status_var, command=lambda v: self.load_invoices(), width=130)
        self.status_combo.pack(side="left", padx=(0, 8))

        ctk.CTkButton(filter_bar, text="Refresh", width=80, command=self.load_invoices, fg_color="gray50").pack(side="left")

        # Table container
        table_card = ctk.CTkFrame(self, corner_radius=10)
        table_card.grid(row=2, column=0, sticky="nsew", padx=0, pady=(4, 0))
        self.grid_rowconfigure(2, weight=1)
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        columns = ("inv_num", "date", "customer", "phone", "amount", "method", "sync_status")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("inv_num", text="Invoice Number")
        self.tree.heading("date", text="Date & Time")
        self.tree.heading("customer", text="Customer")
        self.tree.heading("phone", text="Phone")
        self.tree.heading("amount", text="Total (₹)")
        self.tree.heading("method", text="Payment")
        self.tree.heading("sync_status", text="Sync Status")

        self.tree.column("inv_num", width=140, anchor="w")
        self.tree.column("date", width=130, anchor="center")
        self.tree.column("customer", width=130, anchor="w")
        self.tree.column("phone", width=100, anchor="center")
        self.tree.column("amount", width=90, anchor="e")
        self.tree.column("method", width=80, anchor="center")
        self.tree.column("sync_status", width=110, anchor="center")

        scrollbar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)

        self.tree.bind("<Double-1>", lambda e: self._view_selected_receipt())

    def load_invoices(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        search = self.search_var.get().strip() or None
        status = self.status_var.get()
        self.invoices = db_local.get_all_invoices(status_filter=status, search=search)

        for inv in self.invoices:
            sync_tag = inv["sync_status"]
            status_display = "🟢 SYNCED" if sync_tag == "SYNCED" else "🔴 PENDING"

            created_display = inv["created_at"][:19].replace("T", " ")
            self.tree.insert("", "end", iid=inv["invoice_uuid"], values=(
                inv["invoice_number"],
                created_display,
                inv["customer_name"],
                inv["customer_phone"] or "-",
                f"₹{inv['grand_total']:.2f}",
                inv["payment_method"],
                status_display,
            ))

    def _on_sync_click(self):
        self.sync_manager.force_sync()
        self.after(1000, self.load_invoices)

    def _view_selected_receipt(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select Invoice", "Please select an invoice from the table to view.")
            return

        inv_uuid = selected[0]
        inv_data = db_local.get_invoice_with_items(inv_uuid)
        if inv_data:
            ReceiptDialog(self, inv_data, inv_data.get("items", []))
        else:
            messagebox.showerror("Error", "Could not find invoice details.")

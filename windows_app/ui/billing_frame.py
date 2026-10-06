import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from datetime import datetime
try:
    from .. import db_local
except (ImportError, ValueError):
    import db_local


class ReceiptDialog(ctk.CTkToplevel):
    """Clean popup receipt preview with print simulation"""
    def __init__(self, parent, invoice_data, items_data):
        super().__init__(parent)
        self.title(f"Receipt - {invoice_data['invoice_number']}")
        self.geometry("450x600")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        # Content frame
        container = ctk.CTkScrollableFrame(self, fg_color="#ffffff", corner_radius=12)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        # Header
        ctk.CTkLabel(container, text="SMART RETAIL & BILLING", font=ctk.CTkFont(size=18, weight="bold"), text_color="#1e293b").pack(pady=(10, 2))
        ctk.CTkLabel(container, text="Main Market Road • Sector 4", font=ctk.CTkFont(size=12), text_color="#64748b").pack()
        ctk.CTkLabel(container, text="Phone: +91 98765 43210 • GST: 27AABCS1429B1Z", font=ctk.CTkFont(size=11), text_color="#64748b").pack(pady=(0, 10))

        # Divider
        ctk.CTkFrame(container, height=1, fg_color="#cbd5e1").pack(fill="x", pady=6)

        # Info
        info_text = f"Invoice: {invoice_data['invoice_number']}\nDate: {invoice_data['created_at'][:19]}\nCustomer: {invoice_data['customer_name']}\nPhone: {invoice_data.get('customer_phone') or 'N/A'}\nMode: {invoice_data['payment_method']}"
        ctk.CTkLabel(container, text=info_text, justify="left", font=ctk.CTkFont(size=12), text_color="#334155").pack(anchor="w", padx=4)

        ctk.CTkFrame(container, height=1, fg_color="#cbd5e1").pack(fill="x", pady=6)

        # Items
        for item in items_data:
            item_row = ctk.CTkFrame(container, fg_color="transparent")
            item_row.pack(fill="x", pady=2)
            left_text = f"{item['product_name']}  (x{item['quantity']})"
            right_text = f"₹{item['total_price']:.2f}"
            ctk.CTkLabel(item_row, text=left_text, font=ctk.CTkFont(size=12), text_color="#1e293b").pack(side="left")
            ctk.CTkLabel(item_row, text=right_text, font=ctk.CTkFont(size=12, weight="bold"), text_color="#1e293b").pack(side="right")

        ctk.CTkFrame(container, height=1, fg_color="#cbd5e1").pack(fill="x", pady=8)

        # Totals
        sub_row = ctk.CTkFrame(container, fg_color="transparent")
        sub_row.pack(fill="x", pady=1)
        ctk.CTkLabel(sub_row, text="Subtotal:", font=ctk.CTkFont(size=12), text_color="#64748b").pack(side="left")
        ctk.CTkLabel(sub_row, text=f"₹{invoice_data['subtotal']:.2f}", font=ctk.CTkFont(size=12), text_color="#64748b").pack(side="right")

        tax_row = ctk.CTkFrame(container, fg_color="transparent")
        tax_row.pack(fill="x", pady=1)
        ctk.CTkLabel(tax_row, text="Tax / GST:", font=ctk.CTkFont(size=12), text_color="#64748b").pack(side="left")
        ctk.CTkLabel(tax_row, text=f"₹{invoice_data['tax_amount']:.2f}", font=ctk.CTkFont(size=12), text_color="#64748b").pack(side="right")

        total_row = ctk.CTkFrame(container, fg_color="transparent")
        total_row.pack(fill="x", pady=4)
        ctk.CTkLabel(total_row, text="GRAND TOTAL:", font=ctk.CTkFont(size=15, weight="bold"), text_color="#0f172a").pack(side="left")
        ctk.CTkLabel(total_row, text=f"₹{invoice_data['grand_total']:.2f}", font=ctk.CTkFont(size=16, weight="bold"), text_color="#2563eb").pack(side="right")

        ctk.CTkLabel(container, text="Thank you for your purchase!", font=ctk.CTkFont(size=11, slant="italic"), text_color="#94a3b8").pack(pady=(16, 4))

        # Close button
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="Close Receipt", command=self.destroy, fg_color="#3b82f6").pack(fill="x")


class BillingFrame(ctk.CTkFrame):
    """
    Cashier Fast POS Screen.
    Works 100% offline, saves to local SQLite, and triggers sync when online.
    """
    def __init__(self, parent, sync_manager):
        super().__init__(parent, fg_color="transparent")
        self.sync_manager = sync_manager
        self.cart_items = []
        self.products = []

        self._build_ui()
        self.load_products()

    def _build_ui(self):
        # Main layout: Left column (Customer & Item Entry + Cart), Right column (Summary & Bill Action)
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # LEFT COLUMN
        left_panel = ctk.CTkFrame(self, corner_radius=12)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)
        left_panel.grid_columnconfigure(0, weight=1)
        left_panel.grid_rowconfigure(2, weight=1)

        # 1. Customer Section
        cust_box = ctk.CTkFrame(left_panel, fg_color=("gray95", "gray20"), corner_radius=10)
        cust_box.grid(row=0, column=0, sticky="ew", padx=12, pady=10)

        ctk.CTkLabel(cust_box, text="Customer Information", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4))

        self.cust_name_var = tk.StringVar(value="Cash Customer")
        self.cust_phone_var = tk.StringVar()

        ctk.CTkLabel(cust_box, text="Name:").grid(row=1, column=0, sticky="w", padx=(10, 4), pady=(0, 8))
        self.cust_name_entry = ctk.CTkEntry(cust_box, textvariable=self.cust_name_var, width=180, placeholder_text="Customer Name")
        self.cust_name_entry.grid(row=1, column=1, sticky="w", padx=(0, 15), pady=(0, 8))

        ctk.CTkLabel(cust_box, text="Mobile:").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(0, 8))
        self.cust_phone_entry = ctk.CTkEntry(cust_box, textvariable=self.cust_phone_var, width=160, placeholder_text="10-digit phone")
        self.cust_phone_entry.grid(row=1, column=3, sticky="w", padx=(0, 10), pady=(0, 8))

        # 2. Add Item Section
        item_box = ctk.CTkFrame(left_panel, fg_color=("gray95", "gray20"), corner_radius=10)
        item_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(item_box, text="Product Entry", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=5, sticky="w", padx=10, pady=(8, 4))

        ctk.CTkLabel(item_box, text="Select Product:").grid(row=1, column=0, sticky="w", padx=(10, 4), pady=(0, 8))
        self.product_combo = ctk.CTkComboBox(item_box, width=240, command=self._on_product_selected)
        self.product_combo.grid(row=1, column=1, sticky="w", padx=(0, 10), pady=(0, 8))

        ctk.CTkLabel(item_box, text="Qty:").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(0, 8))
        self.qty_var = tk.StringVar(value="1")
        self.qty_entry = ctk.CTkEntry(item_box, textvariable=self.qty_var, width=60)
        self.qty_entry.grid(row=1, column=3, sticky="w", padx=(0, 10), pady=(0, 8))

        self.add_item_btn = ctk.CTkButton(item_box, text="+ Add to Cart", width=110, command=self.add_to_cart, fg_color="#2563eb")
        self.add_item_btn.grid(row=1, column=4, sticky="w", padx=(0, 10), pady=(0, 8))

        # 3. Cart Items Table
        cart_box = ctk.CTkFrame(left_panel, corner_radius=10)
        cart_box.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        cart_box.grid_columnconfigure(0, weight=1)
        cart_box.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(cart_box, text="Current Cart Items", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        # Tkinter Treeview for fast table rendering
        table_container = ttk.Frame(cart_box)
        table_container.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        table_container.columnconfigure(0, weight=1)
        table_container.rowconfigure(0, weight=1)

        columns = ("name", "sku", "price", "qty", "tax", "total")
        self.tree = ttk.Treeview(table_container, columns=columns, show="headings", height=8, selectmode="browse")
        self.tree.heading("name", text="Product Name")
        self.tree.heading("sku", text="SKU")
        self.tree.heading("price", text="Price (₹)")
        self.tree.heading("qty", text="Qty")
        self.tree.heading("tax", text="Tax %")
        self.tree.heading("total", text="Total (₹)")

        self.tree.column("name", width=180, anchor="w")
        self.tree.column("sku", width=90, anchor="center")
        self.tree.column("price", width=70, anchor="e")
        self.tree.column("qty", width=50, anchor="center")
        self.tree.column("tax", width=50, anchor="center")
        self.tree.column("total", width=80, anchor="e")

        scrollbar = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Cart Action Buttons
        cart_action_frame = ctk.CTkFrame(cart_box, fg_color="transparent")
        cart_action_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=6)

        ctk.CTkButton(cart_action_frame, text="Remove Selected", width=120, command=self.remove_from_cart, fg_color="#ef4444").pack(side="left", padx=4)
        ctk.CTkButton(cart_action_frame, text="Clear All", width=90, command=self.clear_cart, fg_color="gray50").pack(side="left", padx=4)

        # RIGHT COLUMN: Order Summary & Checkout
        right_panel = ctk.CTkFrame(self, corner_radius=12)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        right_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right_panel, text="Bill Summary", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(16, 12), padx=16, anchor="w")

        # Payment Method
        ctk.CTkLabel(right_panel, text="Payment Mode:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(4, 2))
        self.payment_mode_var = tk.StringVar(value="Cash")
        self.payment_combo = ctk.CTkComboBox(right_panel, values=["Cash", "UPI / QR", "Card", "Credit"], variable=self.payment_mode_var)
        self.payment_combo.pack(fill="x", padx=16, pady=(0, 10))

        # Calculation breakdown box
        calc_box = ctk.CTkFrame(right_panel, fg_color=("gray95", "gray20"), corner_radius=10)
        calc_box.pack(fill="x", padx=16, pady=10)

        # Subtotal
        sub_row = ctk.CTkFrame(calc_box, fg_color="transparent")
        sub_row.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(sub_row, text="Subtotal:", font=ctk.CTkFont(size=12)).pack(side="left")
        self.subtotal_label = ctk.CTkLabel(sub_row, text="₹0.00", font=ctk.CTkFont(size=12))
        self.subtotal_label.pack(side="right")

        # Tax
        tax_row = ctk.CTkFrame(calc_box, fg_color="transparent")
        tax_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(tax_row, text="Tax / GST:", font=ctk.CTkFont(size=12)).pack(side="left")
        self.tax_label = ctk.CTkLabel(tax_row, text="₹0.00", font=ctk.CTkFont(size=12))
        self.tax_label.pack(side="right")

        # Divider
        ctk.CTkFrame(calc_box, height=1, fg_color="gray70").pack(fill="x", padx=12, pady=8)

        # Grand Total
        total_row = ctk.CTkFrame(calc_box, fg_color="transparent")
        total_row.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(total_row, text="GRAND TOTAL:", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.grand_total_label = ctk.CTkLabel(total_row, text="₹0.00", font=ctk.CTkFont(size=18, weight="bold"), text_color="#22c55e")
        self.grand_total_label.pack(side="right")

        # Checkout Buttons
        self.checkout_btn = ctk.CTkButton(
            right_panel,
            text="⚡ Complete & Print Bill",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=46,
            command=self.complete_sale,
            fg_color="#16a34a",
            hover_color="#15803d"
        )
        self.checkout_btn.pack(fill="x", padx=16, pady=(14, 8))

        # Status note
        ctk.CTkLabel(
            right_panel,
            text="Bills are saved locally immediately.\nAuto-sync uploads to Django when online.",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            justify="center"
        ).pack(padx=16, pady=8)

    def load_products(self):
        self.products = db_local.get_all_products()
        if self.products:
            values = [f"{p['name']} (₹{p['price']:.2f})" for p in self.products]
            self.product_combo.configure(values=values)
            self.product_combo.set(values[0])
        else:
            self.product_combo.configure(values=["No products available"])

    def _on_product_selected(self, choice):
        pass

    def add_to_cart(self):
        sel_idx = self.product_combo.cget("values").index(self.product_combo.get()) if self.product_combo.get() in self.product_combo.cget("values") else -1
        if sel_idx < 0 or sel_idx >= len(self.products):
            messagebox.showwarning("Product Required", "Please select a valid product.")
            return

        product = self.products[sel_idx]

        try:
            qty = float(self.qty_var.get())
            if qty <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showwarning("Invalid Quantity", "Please enter a valid positive quantity.")
            return

        price = float(product["price"])
        tax_pct = float(product.get("tax_percent", 0.0))
        item_subtotal = price * qty
        item_tax = (item_subtotal * tax_pct) / 100.0
        line_total = item_subtotal + item_tax

        # Check if already in cart, increment quantity
        for item in self.cart_items:
            if item["product_sku"] == product["sku"]:
                item["quantity"] += qty
                item["tax_amount"] += item_tax
                item["total_price"] += line_total
                self._refresh_cart_view()
                return

        self.cart_items.append({
            "product_name": product["name"],
            "product_sku": product["sku"],
            "unit_price": price,
            "quantity": qty,
            "tax_percent": tax_pct,
            "tax_amount": item_tax,
            "discount_percent": 0.0,
            "total_price": line_total,
        })
        self._refresh_cart_view()

    def remove_from_cart(self):
        selected_item = self.tree.selection()
        if not selected_item:
            return
        idx = self.tree.index(selected_item[0])
        if 0 <= idx < len(self.cart_items):
            self.cart_items.pop(idx)
            self._refresh_cart_view()

    def clear_cart(self):
        self.cart_items.clear()
        self._refresh_cart_view()

    def _refresh_cart_view(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        subtotal = 0.0
        tax_total = 0.0

        for item in self.cart_items:
            subtotal += (item["unit_price"] * item["quantity"])
            tax_total += item["tax_amount"]
            self.tree.insert("", "end", values=(
                item["product_name"],
                item["product_sku"],
                f"{item['unit_price']:.2f}",
                item["quantity"],
                f"{item['tax_percent']:.0f}%",
                f"{item['total_price']:.2f}"
            ))

        grand_total = subtotal + tax_total

        self.subtotal_label.configure(text=f"₹{subtotal:.2f}")
        self.tax_label.configure(text=f"₹{tax_total:.2f}")
        self.grand_total_label.configure(text=f"₹{grand_total:.2f}")

    def complete_sale(self):
        if not self.cart_items:
            messagebox.showwarning("Empty Cart", "Please add at least one product to the cart.")
            return

        subtotal = sum(i["unit_price"] * i["quantity"] for i in self.cart_items)
        tax_total = sum(i["tax_amount"] for i in self.cart_items)
        grand_total = subtotal + tax_total

        # Generate unique invoice number: WIN-YYYYMMDD-HHMMSS
        now = datetime.now()
        inv_number = f"WIN-{now.strftime('%Y%m%d-%H%M%S')}"

        invoice_data = {
            "invoice_number": inv_number,
            "customer_name": self.cust_name_var.get().strip() or "Cash Customer",
            "customer_phone": self.cust_phone_var.get().strip(),
            "customer_email": "",
            "subtotal": subtotal,
            "tax_amount": tax_total,
            "discount_amount": 0.0,
            "grand_total": grand_total,
            "payment_method": self.payment_mode_var.get(),
            "payment_status": "Paid",
            "notes": "Generated from Windows POS",
            "created_at": now.isoformat(),
        }

        # 1. Save to local SQLite (Works completely offline!)
        saved = db_local.save_invoice(invoice_data, self.cart_items)

        # 2. Trigger instant sync if online
        self.sync_manager.force_sync()

        # 3. Show receipt dialog
        ReceiptDialog(self, invoice_data, self.cart_items)

        # 4. Reset cart and inputs
        self.clear_cart()
        self.cust_phone_var.set("")
        self.cust_name_var.set("Cash Customer")
        self.load_products()  # Reload stock


if __name__ == "__main__":
    import os, sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from windows_app.app import main
    main()

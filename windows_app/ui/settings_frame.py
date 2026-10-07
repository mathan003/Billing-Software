import time
import requests
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
try:
    from .. import db_local
    from ..config import DB_PATH
except (ImportError, ValueError):
    import db_local
    from config import DB_PATH


class SettingsFrame(ctk.CTkFrame):
    """
    Settings & Cloud Sync Configuration.
    Allows configuring the Django server endpoint (Localhost or Render deployment URL),
    testing network connectivity, and inspecting local sync metrics.
    """
    def __init__(self, parent, sync_manager):
        super().__init__(parent, fg_color="transparent")
        self.sync_manager = sync_manager

        self._build_ui()
        self.load_settings()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # LEFT CARD: Server Configuration
        left_card = ctk.CTkFrame(self, corner_radius=12)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)

        ctk.CTkLabel(left_card, text="Cloud Backend Settings", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 12))

        # Server URL
        ctk.CTkLabel(left_card, text="Django Server URL:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(4, 2))
        self.server_url_var = tk.StringVar()
        self.server_url_entry = ctk.CTkEntry(
            left_card,
            textvariable=self.server_url_var,
            placeholder_text="http://127.0.0.1:8000 or https://billing-software-production-d0f2.up.railway.app"
        )
        self.server_url_entry.pack(fill="x", padx=16, pady=(0, 6))

        ctk.CTkLabel(
            left_card,
            text="Tip: For Railway cloud deployment, paste: https://billing-software-production-d0f2.up.railway.app",
            font=ctk.CTkFont(size=11),
            text_color="gray60"
        ).pack(anchor="w", padx=16, pady=(0, 12))

        # Device ID
        ctk.CTkLabel(left_card, text="Device ID / Register Name:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(4, 2))
        self.device_id_var = tk.StringVar()
        self.device_id_entry = ctk.CTkEntry(left_card, textvariable=self.device_id_var, placeholder_text="WIN-POS-01")
        self.device_id_entry.pack(fill="x", padx=16, pady=(0, 16))

        # Action Buttons
        btn_row = ctk.CTkFrame(left_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 14))

        self.save_btn = ctk.CTkButton(btn_row, text="💾 Save Settings", command=self.save_settings, fg_color="#2563eb", width=120)
        self.save_btn.pack(side="left", padx=(0, 8))

        self.test_btn = ctk.CTkButton(btn_row, text="⚡ Test Connection", command=self.test_connection, fg_color="#10b981", width=130)
        self.test_btn.pack(side="left")

        # Connection Test Result Box
        self.test_result_box = ctk.CTkFrame(left_card, fg_color=("gray90", "gray25"), corner_radius=8)
        self.test_result_box.pack(fill="x", padx=16, pady=(0, 16))

        self.test_result_label = ctk.CTkLabel(
            self.test_result_box,
            text="Click 'Test Connection' to verify server reachability.",
            font=ctk.CTkFont(size=12),
            text_color="gray50",
            wraplength=320
        )
        self.test_result_label.pack(padx=12, pady=10)

        # RIGHT CARD: Synchronization Diagnostics
        right_card = ctk.CTkFrame(self, corner_radius=12)
        right_card.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)

        ctk.CTkLabel(right_card, text="Sync Diagnostics & Statistics", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 12))

        # Stats Container
        stats_box = ctk.CTkFrame(right_card, fg_color=("gray95", "gray20"), corner_radius=10)
        stats_box.pack(fill="x", padx=16, pady=(0, 14))

        self.total_bills_label = self._create_stat_row(stats_box, "Total Local Invoices:", "0")
        self.pending_bills_label = self._create_stat_row(stats_box, "Pending Offline Invoices:", "0", color="#ef4444")
        self.synced_bills_label = self._create_stat_row(stats_box, "Synced to Cloud:", "0", color="#22c55e")
        self.cached_products_label = self._create_stat_row(stats_box, "Cached Products:", "0")
        self.last_sync_label = self._create_stat_row(stats_box, "Last Successful Sync:", "Never")

        # Force Sync Action
        ctk.CTkButton(
            right_card,
            text="🔄 Force Sync Now",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            command=self.force_sync_now,
            fg_color="#3b82f6"
        ).pack(fill="x", padx=16, pady=(4, 12))

        # Local DB Path info
        ctk.CTkLabel(
            right_card,
            text=f"Local SQLite Database:\n{DB_PATH}",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            justify="center",
            wraplength=320
        ).pack(padx=16, pady=(4, 16))

    def _create_stat_row(self, parent, label_text, default_val, color=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(row, text=label_text, font=ctk.CTkFont(size=12)).pack(side="left")
        val_label = ctk.CTkLabel(row, text=default_val, font=ctk.CTkFont(size=12, weight="bold"), text_color=color)
        val_label.pack(side="right")
        return val_label

    def load_settings(self):
        url = db_local.get_setting("server_url", "http://127.0.0.1:8000")
        device = db_local.get_setting("device_id", "WIN-DESKTOP-POS-01")
        self.server_url_var.set(url)
        self.device_id_var.set(device)
        self.refresh_stats()

    def refresh_stats(self):
        stats = db_local.get_sync_statistics()
        self.total_bills_label.configure(text=str(stats["total_invoices"]))
        self.pending_bills_label.configure(text=str(stats["pending_count"]))
        self.synced_bills_label.configure(text=str(stats["synced_count"]))
        self.cached_products_label.configure(text=str(stats["products_count"]))
        self.last_sync_label.configure(text=stats["last_sync_time"] or "Never")

    def save_settings(self):
        url = self.server_url_var.get().strip().rstrip("/")
        device = self.device_id_var.get().strip()

        if not url:
            messagebox.showwarning("Validation", "Server URL cannot be empty.")
            return

        db_local.set_setting("server_url", url)
        db_local.set_setting("device_id", device)
        messagebox.showinfo("Saved", "Settings saved successfully! Sync worker updated.")
        self.sync_manager.force_sync()

    def test_connection(self):
        url = self.server_url_var.get().strip().rstrip("/")
        self.test_result_label.configure(text="Pinging server...", text_color="gray60")
        self.update()

        try:
            start_t = time.time()
            resp = requests.get(f"{url}/api/health/", timeout=4.0)
            latency_ms = int((time.time() - start_t) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                self.test_result_label.configure(
                    text=f"✅ Connected successfully!\nServer: {data.get('server')}\nLatency: {latency_ms} ms",
                    text_color="#16a34a"
                )
            else:
                self.test_result_label.configure(
                    text=f"⚠️ Server returned error status {resp.status_code}",
                    text_color="#d97706"
                )
        except requests.exceptions.ConnectionError:
            self.test_result_label.configure(
                text="❌ Connection Failed: Server is unreachable. Check URL or verify Django is running.",
                text_color="#dc2626"
            )
        except Exception as e:
            self.test_result_label.configure(
                text=f"❌ Error: {str(e)}",
                text_color="#dc2626"
            )

    def force_sync_now(self):
        self.sync_manager.force_sync()
        self.after(1000, self.refresh_stats)

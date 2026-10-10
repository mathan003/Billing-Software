"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.8.3"
APP_TITLE = "Modular Architecture, Dedicated Admin/Client/Customer Modules & Database Connection Synchronization"
APP_RELEASE_NOTES = (
    "• Modular Architecture: Complete modular separation of frontend and backend for Admin, Client, and Customer modules (admin.html, admin.css, admin.js, client.html, client.css, client.js, customer.html, customer.css, customer.js)\n"
    "• Modular Database Connections: Dedicated db_connections.py providing connection diagnostics, health checks, multi-tenant isolation, and resilient transactions across SQLite, MySQL, and PostgreSQL\n"
    "• MySQL Timezone Query Bounds Fix: Converted all date-based filtering in Dashboard, Customer Statements, and Live Status Poller to robust timezone-aware datetime ranges, preventing MySQL CONVERT_TZ null anomalies\n"
    "• Real-Time Cloud Synchronization: Desktop POS terminal and Web application maintain continuous parity with automatic sync triggers and live status polling\n"
    "• 100% Zero Data Loss: All administrative accounts, client databases, product catalogs, customer histories, invoices, and reports remain completely safe, preserved, and backed up"
)

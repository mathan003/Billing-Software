"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.7.6"
APP_TITLE = "Complete Reports, Invoices & Dashboard Bidirectional Sync Resolution"
APP_RELEASE_NOTES = (
    "• Reports, Invoices & Dashboard Synchronization: Full bidirectional sync ensures all bills, sales totals, customer payments, and dues sync seamlessly between desktop and web\n"
    "• Invoice Edit & Payment Sync: Editing invoices or recording payments immediately triggers cloud sync and updates financial figures in real time\n"
    "• Safe Tombstone Deletion: Replaced destructive invoice wiping with safe DeletedInvoice tombstones, preventing accidental bill loss across all devices\n"
    "• Multi-Tenant Scoping: Client-level and unassigned bills are correctly attributed and aggregated across Dashboard, Invoices, and Reports\n"
    "• Asia/Kolkata Business Day Alignment: Dashboard and Reports date calculations accurately reflect the local business day\n"
    "• 100% Data Protection: All client databases, bills, invoices, customer records, and active products preserved untouched"
)



"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.7.5"
APP_TITLE = "Product Delete 404 Fix, Sync Constraint Resolution & Live Status Indicator"
APP_RELEASE_NOTES = (
    "• Product Deletion 404 Resolution: Replaced rigid get_object_or_404 with multi-tenant resilient lookups to completely prevent 404 Page Not Found errors during product deletion and edits\n"
    "• Sync Constraint Resolution: Fixed customer address constraint in cloud sync push, allowing pending offline invoices to synchronize cleanly\n"
    "• Accurate Sync Status Indicator: Live sync indicator clearly distinguishes 'Pending Sync' with item count from 'Online • Synced' when fully up to date\n"
    "• Clean Product Form: All input fields display clean, label-focused controls without distracting placeholders\n"
    "• 100% Data Protection: All client databases, bills, invoices, customer records, and active products preserved untouched"
)



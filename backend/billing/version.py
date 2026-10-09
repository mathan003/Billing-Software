"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.5.9"
APP_TITLE = "Responsive Login Alignment, Safe Updates & Zero Data Loss Architecture"
APP_RELEASE_NOTES = (
    "• Responsive Login Page Alignment: Completely restructured layout with balanced spacing, responsive update cards, and unclipped touch-friendly action buttons\n"
    "• Zero Data Loss Guarantee: Safe update mechanism with automatic pre-update database backups and detached rollback support\n"
    "• Strict Multi-Tenant Data Protection: Client customer records, bills, invoices, products, and categories remain 100% isolated and preserved across updates\n"
    "• Seamless Database Migration Handling: Safe database snapshotting prior to migrations preventing SQLite schema corruption"
)

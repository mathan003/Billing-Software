"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.7.4"
APP_TITLE = "Clean Product Form & Permanent Product Deletion Persistence"
APP_RELEASE_NOTES = (
    "• Clean Product Addition Form: Removed all placeholder text from product name and unit fields for a clean, label-focused input experience\n"
    "• Permanent Product Deletion: Introduced persistent DeletedProduct tombstones across web and desktop POS to ensure deleted products stay permanently deleted across page refreshes and sync cycles\n"
    "• Anti-Resurrection Guard: Cloud and desktop bidirectional sync strictly ignores and tombstones soft-deleted and purged products\n"
    "• 100% Data Protection: All client databases, bills, invoices, customer records, and active products preserved untouched"
)


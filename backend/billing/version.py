"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.7.3"
APP_TITLE = "Permanent Category & Product Sync, Modal Fix & Fast Launch"
APP_RELEASE_NOTES = (
    "• Category & Product Persistence: Added, edited, and deleted categories and products now stay permanently synchronized without resurrecting after refresh or restart\n"
    "• Bidirectional Soft-Delete Sync: Desktop and Cloud synchronization now properly communicates soft-deleted items across all sync cycles\n"
    "• Modal Click & Add Product Fix: Resolved modal launch and button conflicts for Add Product and Add/Remove Category dialogs\n"
    "• Fast Subsequent EXE Launch: Eliminated process locking with automatic zombie process cleanup and instant migration bypass\n"
    "• 100% Data Protection: All client databases, bills, invoices, customer records, and active products preserved untouched"
)

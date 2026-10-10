"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.8.0"
APP_TITLE = "Strict Stock Control, Out of Stock Validation & Real-Time Inventory Sync"
APP_RELEASE_NOTES = (
    "• Strict Out-of-Stock Protection: Products with 0 stock display an explicit Out of Stock alert and are strictly barred from being billed\n"
    "• Insufficient Stock Prevention: Billing quantities exceeding available stock are blocked with precise error messages showing max available units\n"
    "• Client & Multi-Row Aggregation: Sums quantities across multiple rows for the same product in a single invoice to guarantee no stock overrun\n"
    "• Automatic Stock Decrement & Restore: Stock levels decrement accurately upon bill creation and are safely restored during order edits\n"
    "• Instant Multi-Device Inventory Sync: Real-time synchronization updates stock counts across Web and Windows Desktop POS instantly\n"
    "• 100% Data Preservation: All client records, customer data, historical invoices, and admin settings remain completely untouched"
)



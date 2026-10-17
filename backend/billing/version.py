"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.9.1"
APP_TITLE = "Local Offline POS Enforcement & Return URL Name Resolution"
APP_RELEASE_NOTES = (
    "• Resolved Return Items URL Lookup: Fixed NoReverseMatch for 'return_invoice_items' across dashboard return console.\n"
    "• Strict Local Offline Execution: Offline POS users strictly use local SQLite database with zero cloud push/pull.\n"
    "• First-Login Device Authorization: Only admin-created accounts on allowed systems can log in via Admin OTP verification on first login.\n"
    "• Zero Data Loss & Backward Compatibility: Complete preservation of all customer data, products, and invoices."
)

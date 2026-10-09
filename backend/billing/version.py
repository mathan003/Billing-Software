"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.7.7"
APP_TITLE = "Multi-Client Database Isolation & Secure Multi-Device Synchronization"
APP_RELEASE_NOTES = (
    "• Strict Client Database Isolation: Each client has a dedicated, isolated data partition (products, customers, invoices, reports, categories). Zero data leakage between different clients.\n"
    "• Seamless Multi-Device Client Sync: Multiple devices belonging to the same client connect to and synchronize with that client's database in real time.\n"
    "• Admin Database & Settings Preserved: Administrative records, settings, and client management remain completely untouched and fully functional.\n"
    "• Eliminated Cross-Client Leaks: Removed unassigned/null client fallbacks across all web views, sync push, and sync pull APIs.\n"
    "• 100% Data Protection: All existing databases, customer details, client profiles, and historical bills preserved with complete integrity."
)



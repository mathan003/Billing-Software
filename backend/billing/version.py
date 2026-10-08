"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.5.7"
APP_TITLE = "Bill Paper Size Customization, 3-Day Recycle Bin & Multi-Tenant Isolation"
APP_RELEASE_NOTES = (
    "• Configurable Bill Paper Size in Client Profile (58mm, 80mm, 100mm, A5, A4, Custom width/height)\n"
    "• Continuous auto-fit paper roll & auto-expanding height for long bills\n"
    "• Responsive on-screen and downloadable PDF receipts adapted to configured dimensions\n"
    "• 3-Day Deleted Items / Recycle Bin: soft-deleted bills, customers, products, and reports are recoverable for 3 days\n"
    "• Automated background purge of expired items older than 3 days across admin & client databases\n"
    "• Complete multi-tenant data isolation: client customers, bills, reports, and products remain strictly partitioned"
)


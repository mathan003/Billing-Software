"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.9.2"
APP_TITLE = "Continuous GST Invoice Format (GC/26/27/001) & Bill Print Redesign"
APP_RELEASE_NOTES = (
    "• Continuous GST Invoice Numbering: Format GC/26/27/001 with continuous sequence across both PC software and Web application.\n"
    "• Automatic Indian Financial Year: Automatically updates financial year from April to March (e.g. 26/27 for 2026-2027).\n"
    "• Dynamic Prefix from Profile: Change company prefix/name from Profile page, automatically used for all new invoices.\n"
    "• GST Tax Invoice Print Layout: Exact match to physical tax invoice with HSN/SAC codes, CGST/SGST breakdown, amount in words, bank details, and signature box.\n"
    "• Administrator Account Consolidation: Removed secondary 'admin' account; 'Mathan003' is the single Primary Administrator.\n"
    "• Zero Data Loss Guarantee: All existing customer records, products, categories, invoices, and reports are 100% preserved."
)

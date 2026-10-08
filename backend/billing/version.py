"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.5.6"
APP_TITLE = "Customer Address, Manual Pricing, Other Product & Isolated Multi-Tenant POS"
APP_RELEASE_NOTES = (
    "• Added Customer Address field to billing terminal, invoice records, and PDF printouts\n"
    "• Enabled manual unit price adjustments during billing for pre-priced items\n"
    "• Added 'Other Product' button for billing custom uncataloged products\n"
    "• Fixed 'Remove Category' functionality with live UI refresh and fallback reassignments\n"
    "• Enforced strict multi-tenant isolation: categories and products are private to each client"
)


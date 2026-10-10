"""
Centralized Application Version & Release Metadata
Used to coordinate simultaneous automatic updates across Web and Desktop POS.
"""

APP_VERSION = "v2.8.4"
APP_TITLE = "Standardized Invoice Number Format & Strict Web/POS Sequential Numbering"
APP_RELEASE_NOTES = (
    "• Standardized Invoice Number Format: Guaranteed clean, uniform invoice numbers for Web (WEB-YYYYMMDD-XXXX) and Desktop POS (POS-YYYYMMDD-XXXX) with no unwanted letters, extra characters, or hex suffixes\n"
    "• Sequential Collision-Free Allocation: Automatic sequence detection scans existing database records to assign the next available invoice number without gaps or collisions\n"
    "• Zero Data Loss & Backward Compatibility: All existing invoices, customer details, client profiles, products, reports, and administrative data remain 100% preserved and intact\n"
    "• Multi-Device Cloud Parity: Web application and Desktop POS terminal maintain seamless real-time invoice synchronization"
)

"""
Modular Admin API Layer - MathanHub POS
Endpoints for Admin Device Management, App Updates, and System Diagnostics.
"""

from .api_views import (
    DeviceRevokeApiView,
    DeviceLimitUpdateApiView,
    UpdateCheckView,
    UpdateDownloadView,
)

__all__ = [
    "DeviceRevokeApiView",
    "DeviceLimitUpdateApiView",
    "UpdateCheckView",
    "UpdateDownloadView",
]

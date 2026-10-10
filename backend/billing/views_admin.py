"""
Modular Admin View Layer - MathanHub POS
Handles Administrative Control Center, Diagnostics, Multi-Client and Multi-Device Management.
"""

import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Count, Q
from django.contrib.auth.models import User

from .db_connections import check_database_connections, get_admin_db_connection, is_admin_user
from .models import (
    UserProfile, RegisteredDevice, ActiveUserSession, Branch,
    ActivityLog, SoftwareUpdate, CompanySettings
)

logger = logging.getLogger(__name__)


def admin_required(view_func):
    """Restricts access strictly to administrators."""
    from functools import wraps
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not is_admin_user(request.user):
            messages.warning(request, "Access Denied: Administrator authentication required.")
            return redirect("billing:dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
@admin_required
def admin_module_view(request):
    """
    Renders the dedicated Admin Module Overview & Database Hub.
    Provides diagnostic health checks and administrative shortcuts.
    """
    db_diagnostics = check_database_connections()

    # Query administrative stats
    total_clients = User.objects.filter(is_superuser=False).count()
    total_devices = RegisteredDevice.objects.count()
    active_sessions = ActiveUserSession.objects.count()
    branches_count = Branch.objects.count()
    recent_logs = ActivityLog.objects.order_by("-created_at")[:10]

    context = {
        "db_diagnostics": db_diagnostics,
        "total_clients": total_clients,
        "total_devices": total_devices,
        "active_sessions": active_sessions,
        "branches_count": branches_count,
        "recent_logs": recent_logs,
        "is_admin_user": True,
    }
    return render(request, "billing/admin.html", context)


@login_required
@admin_required
def admin_db_health_api(request):
    """REST endpoint providing real-time database connection diagnostics."""
    diagnostics = check_database_connections()
    return JsonResponse(diagnostics)

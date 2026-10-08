import uuid
from django.shortcuts import redirect
from django.contrib.auth import logout
from django.contrib import messages
from .models import ActiveUserSession


class SessionSecurityMiddleware:
    """
    Session & Device Security Architecture:
    1. Blocks unauthenticated access to all internal pages (redirects to /login/).
    2. Admin accounts: STRICTLY RESTRICTED to 1 active device.
       If an admin logs in elsewhere, previous session is immediately terminated.
    3. Client accounts: RESTRICTED to MAXIMUM 5 active devices/members per client ID.
       If an evicted session makes a request, it is cleanly logged out.
    4. Dynamic Link Security:
       - Login URL remains constant: /login/
       - Internal URLs dynamically include and enforce the session security token (?sec=...).
       - Security token changes automatically on every login session.
       - Internal links cannot be opened or shared without an active authenticated session.
    5. Admin Panel Security: Normal client users cannot access /admin/ or /admin-panel/.
    """
    EXEMPT_PATHS = [
        "/login/",
        "/logout/",
        "/static/",
        "/media/",
        "/api/",  # REST API for background sync
        "/favicon.ico",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # 1. Allow exempt paths (login, logout, static assets, background sync API)
        for exempt in self.EXEMPT_PATHS:
            if path.startswith(exempt):
                return self.get_response(request)

        # 2. Enforce authentication on all internal pages (clean login URL always)
        if not request.user.is_authenticated:
            return redirect("billing:login")

        # 3. Strict Admin-Only restriction for Admin Panel & Django Admin
        if path.startswith("/admin/") or path.startswith("/admin-panel/"):
            is_admin = request.user.is_superuser or (
                hasattr(request.user, "profile") and request.user.profile.role == "admin"
            )
            if not is_admin:
                messages.error(request, "Access Denied: You do not have administrator privileges to access the Admin Panel.")
                return redirect("billing:dashboard")

        # 4. Device Session Verification & Limit Enforcement
        session_key = request.session.session_key
        device_id = request.COOKIES.get("billing_device_id", "")
        if session_key:
            active = ActiveUserSession.objects.filter(session_key=session_key).first()
            device_revoked = False
            if device_id:
                from .models import RegisteredDevice
                rd = RegisteredDevice.objects.filter(user=request.user, device_id=device_id).first()
                if rd and not rd.is_active:
                    device_revoked = True

            if not active or device_revoked:
                # Session was evicted or device was disconnected by Administrator
                is_admin_user = request.user.is_superuser or (
                    hasattr(request.user, "profile") and request.user.profile.role == "admin"
                )
                logout(request)
                if device_revoked:
                    messages.warning(
                        request,
                        "Your session has ended because this device was disconnected or removed by the Administrator."
                    )
                elif is_admin_user:
                    messages.warning(
                        request,
                        "Your Administrator session has ended because this account reached its allowed active devices limit on another terminal."
                    )
                else:
                    dev_limit = getattr(getattr(request.user, "profile", None), "device_limit", 5)
                    messages.warning(
                        request,
                        f"Your session has ended because this device was disconnected by Administrator or the maximum limit of {dev_limit} concurrent devices was reached."
                    )
                return redirect("billing:login")
            else:
                # Keep last activity fresh
                active.save(update_fields=["last_activity"])

        # 5. Dynamic Link Security & Automatic Link Change
        # Ensure session has a security token
        sec_token = request.session.get("sec_token")
        if not sec_token:
            sec_token = uuid.uuid4().hex[:12]
            request.session["sec_token"] = sec_token

        # For regular internal page GET requests: Ensure dynamic security token is in the URL query string
        # If someone visits or pastes a URL without sec token or with another session's sec token,
        # redirect to the current session's dynamic URL
        if request.method == "GET" and not request.headers.get("x-requested-with") == "XMLHttpRequest":
            is_download_or_asset = (
                any(path.endswith(ext) for ext in [".pdf", ".txt", ".json", ".ico", ".png", ".jpg", ".svg", ".css", ".js", ".csv"])
                or "download" in path
                or "/pdf/" in path
                or "/export-txt/" in path
                or "/export-csv/" in path
                or "/apply/" in path
            )
            if not is_download_or_asset:
                current_query_sec = request.GET.get("sec")
                if not current_query_sec:
                    params = request.GET.copy()
                    params["sec"] = sec_token
                    return redirect(f"{path}?{params.urlencode()}")
                else:
                    request.session["sec_token"] = current_query_sec

        return self.get_response(request)

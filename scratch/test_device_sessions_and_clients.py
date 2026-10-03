import os
import sys
import django
from decimal import Decimal

# Configure Django
sys.path.insert(0, r"D:\billing software\backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
django.setup()

from django.test import Client, RequestFactory
from django.contrib.auth.models import User
from billing.models import UserProfile, ActiveUserSession, ActivityLog, Product, Customer, Invoice
from billing.middleware import SessionSecurityMiddleware

def test_all():
    print("=" * 60)
    print("STARTING TEST: CLIENTS, DEVICE LIMITS & DYNAMIC LINKS")
    print("=" * 60)

    # 1. Setup Admin User
    admin_user, _ = User.objects.get_or_create(username="Mathan003", defaults={"email": "mathan@test.com"})
    admin_user.set_password("M@th@n93612003")
    admin_user.is_superuser = True
    admin_user.is_staff = True
    admin_user.save()
    admin_profile, _ = UserProfile.objects.get_or_create(user=admin_user)
    admin_profile.role = "admin"
    admin_profile.save()

    # 2. Setup Client User
    client_user, _ = User.objects.get_or_create(username="TestClient01", defaults={"email": "client@test.com"})
    client_user.set_password("ClientPass123")
    client_user.is_superuser = False
    client_user.is_staff = False
    client_user.save()
    client_profile, _ = UserProfile.objects.get_or_create(user=client_user)
    client_profile.role = "client"
    client_profile.phone = "9876543210"
    client_profile.shop_name = "Mega Mart"
    client_profile.avatar_base64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    client_profile.save()

    print("[PASS] Admin and Client users prepared.")

    # -------------------------------------------------------------
    # TEST 1: Admin Single-Device Enforcement
    # -------------------------------------------------------------
    ActiveUserSession.objects.filter(user=admin_user).delete()

    c_admin_1 = Client()
    resp1 = c_admin_1.post("/login/", {"username": "Mathan003", "password": "M@th@n93612003"})
    assert resp1.status_code == 302, f"Admin login 1 failed: {resp1.status_code}"
    admin_sessions_count1 = ActiveUserSession.objects.filter(user=admin_user).count()
    assert admin_sessions_count1 == 1, f"Expected 1 admin session, got {admin_sessions_count1}"
    session1_key = c_admin_1.session.session_key
    print(f"[PASS] Admin Device 1 logged in. Active sessions: {admin_sessions_count1} (Key: {session1_key[:8]})")

    # Admin Device 2 logs in -> Should terminate Device 1!
    c_admin_2 = Client()
    resp2 = c_admin_2.post("/login/", {"username": "Mathan003", "password": "M@th@n93612003"})
    assert resp2.status_code == 302, f"Admin login 2 failed: {resp2.status_code}"
    admin_sessions_count2 = ActiveUserSession.objects.filter(user=admin_user).count()
    assert admin_sessions_count2 == 1, f"Expected strictly 1 admin session after device 2 login, got {admin_sessions_count2}"
    session2_key = c_admin_2.session.session_key
    assert session2_key != session1_key, "Device 2 should have distinct session key"
    assert not ActiveUserSession.objects.filter(session_key=session1_key).exists(), "Old session 1 must be evicted!"
    print(f"[PASS] Admin Device 2 logged in. Device 1 was cleanly terminated. Active count: {admin_sessions_count2}")

    # Device 1 tries to access dashboard -> Should be redirected to /login/
    sec1 = c_admin_1.session.get("sec_token", "invalid")
    resp_evicted = c_admin_1.get(f"/?sec={sec1}")
    assert resp_evicted.status_code == 302 and "/login/" in resp_evicted.url, f"Device 1 should be kicked out: {resp_evicted.url}"
    print("[PASS] Evicted Admin Device 1 was intercepted and redirected to /login/.")

    # -------------------------------------------------------------
    # TEST 2: Client Maximum 5 Concurrent Devices Enforcement
    # -------------------------------------------------------------
    ActiveUserSession.objects.filter(user=client_user).delete()

    client_clients = []
    for i in range(1, 6):
        c = Client()
        r = c.post("/login/", {"username": "TestClient01", "password": "ClientPass123"})
        assert r.status_code == 302, f"Client device {i} login failed"
        client_clients.append(c)

    count_5 = ActiveUserSession.objects.filter(user=client_user).count()
    assert count_5 == 5, f"Expected 5 active client sessions, got {count_5}"
    print(f"[PASS] 5 Client devices successfully logged in concurrently. Active count: {count_5}")

    first_device_session_key = client_clients[0].session.session_key
    assert ActiveUserSession.objects.filter(session_key=first_device_session_key).exists()

    # 6th Device logs in -> Oldest device (client_clients[0]) must be evicted!
    c6 = Client()
    r6 = c6.post("/login/", {"username": "TestClient01", "password": "ClientPass123"})
    assert r6.status_code == 302, "Client device 6 login failed"
    count_after_6 = ActiveUserSession.objects.filter(user=client_user).count()
    assert count_after_6 == 5, f"Expected strictly 5 active client sessions, got {count_after_6}"
    assert not ActiveUserSession.objects.filter(session_key=first_device_session_key).exists(), "Oldest client device must be evicted!"
    print(f"[PASS] 6th Client device logged in. Oldest device was evicted. Active count stays strictly at {count_after_6}.")

    # Oldest client device tries to make a request -> Kicked to /login/
    sec_old = client_clients[0].session.get("sec_token", "invalid")
    r_old = client_clients[0].get(f"/?sec={sec_old}")
    assert r_old.status_code == 302 and "/login/" in r_old.url, f"Evicted client device was not redirected to /login/: {r_old.url}"
    print("[PASS] Evicted Client Device was intercepted and redirected to /login/.")

    # -------------------------------------------------------------
    # TEST 3: Dynamic Security Links & Protection
    # -------------------------------------------------------------
    # Unauthenticated client visits /billing/ -> Redirects to /login/
    unauth = Client()
    r_unauth = unauth.get("/billing/?sec=fake123")
    assert r_unauth.status_code == 302 and "/login/" in r_unauth.url, "Unauthenticated user must be redirected to /login/"
    print("[PASS] Unauthenticated access to /billing/ blocked and redirected to /login/.")

    # Authenticated client visits /billing/ with missing sec -> Redirected to /billing/?sec=<valid_token>
    valid_client = c6
    valid_sec = valid_client.session.get("sec_token")
    r_auto_sec = valid_client.get("/billing/")
    assert r_auto_sec.status_code == 302 and f"sec={valid_sec}" in r_auto_sec.url, f"Should redirect to url with sec token: {r_auto_sec.url}"
    print(f"[PASS] Dynamic security token attached to URL automatically: {r_auto_sec.url}")

    # -------------------------------------------------------------
    # TEST 4: Client Restricted to Billing Software Only
    # -------------------------------------------------------------
    # Client attempts to access /admin-panel/
    r_admin_access = valid_client.get(f"/admin-panel/?sec={valid_sec}")
    assert r_admin_access.status_code == 302 and "/admin-panel/" not in r_admin_access.url, "Client user must not access admin panel!"
    print("[PASS] Client user blocked from accessing Admin Panel.")

    # -------------------------------------------------------------
    # TEST 5: Admin Creates Client & Deletes Client
    # -------------------------------------------------------------
    admin_session_client = c_admin_2
    admin_sec = admin_session_client.session.get("sec_token")

    # Admin creates client via POST
    new_client_data = {
        "username": "BranchRetail99",
        "first_name": "Ravi",
        "shop_name": "Ravi Supermarket",
        "phone": "9443322110",
        "email": "ravi@supermarket.com",
        "role": "client",
        "password": "RaviPassword123",
        "password_confirm": "RaviPassword123",
    }
    r_create = admin_session_client.post("/admin-panel/users/create/", new_client_data)
    assert r_create.status_code == 302, f"Client creation failed: {r_create.status_code}"
    created_u = User.objects.get(username="BranchRetail99")
    assert created_u.profile.role == "client"
    assert created_u.profile.shop_name == "Ravi Supermarket"
    assert not created_u.is_staff
    print(f"[PASS] Admin successfully created new client '{created_u.username}' with shop '{created_u.profile.shop_name}'.")

    # Client logs in and creates a session
    c_ravi = Client()
    c_ravi.post("/login/", {"username": "BranchRetail99", "password": "RaviPassword123"})
    assert ActiveUserSession.objects.filter(user=created_u).count() == 1

    # Admin deletes client
    r_delete = admin_session_client.post(f"/admin-panel/users/{created_u.id}/delete/")
    assert r_delete.status_code == 302, f"Client deletion failed: {r_delete.status_code}"
    assert not User.objects.filter(username="BranchRetail99").exists(), "Client user must be deleted"
    assert not ActiveUserSession.objects.filter(user_id=created_u.id).exists(), "Active sessions for deleted user must be purged"
    print(f"[PASS] Admin successfully deleted client '{created_u.username}'. All sessions purged.")

    # Verify ActivityLog contains USER_DELETE
    del_log = ActivityLog.objects.filter(action_type="USER_DELETE", description__icontains="BranchRetail99").first()
    assert del_log is not None, "ActivityLog must record USER_DELETE"
    print(f"[PASS] Cloud database logged USER_DELETE: {del_log.description}")

    print("=" * 60)
    print("ALL TESTS PASSED 100% SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    test_all()

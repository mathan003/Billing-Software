from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.schemas import get_schema_view
from rest_framework.renderers import JSONOpenAPIRenderer
from . import views, api_views

app_name = "billing"

router = DefaultRouter()
router.register(r"products", api_views.ProductViewSet, basename="api-products")
router.register(r"customers", api_views.CustomerViewSet, basename="api-customers")
router.register(r"invoices", api_views.InvoiceViewSet, basename="api-invoices")

urlpatterns = [
    # Authentication (Login only, Max 5 concurrent sessions)
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Dashboard & Dedicated Quick Billing Terminal & Client Data Cleanup
    path("", views.dashboard, name="dashboard"),
    path("billing/", views.billing_page, name="billing_page"),
    path("quick-bill/", views.quick_bill_create, name="quick_bill_create"),
    path("delete-data/", views.client_delete_data, name="client_delete_data"),

    # Invoices & Receipts & Payments & Offers/Discounts & Edit/Alter
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/create/", views.billing_page, name="invoice_create"),
    path("invoices/<int:invoice_id>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:invoice_id>/edit/", views.invoice_edit, name="invoice_edit"),
    path("invoices/<int:invoice_id>/delete/", views.invoice_delete, name="invoice_delete"),
    path("invoices/<int:invoice_id>/download/", views.invoice_download_pdf, name="invoice_download_pdf"),
    path("invoices/<int:invoice_id>/pay/", views.invoice_update_payment, name="invoice_update_payment"),
    path("invoices/<int:invoice_id>/discount/", views.invoice_apply_discount, name="invoice_apply_discount"),

    # Customer Account Statements (Selected Date Range)
    path("customers/<int:customer_id>/statement/", views.customer_statement_view, name="customer_statement"),
    path("customers/<int:customer_id>/statement/pdf/", views.customer_statement_pdf, name="customer_statement_pdf"),

    # Admin Panel (Users Management, Company Profile/Branding & Activity Logs - Strict Admin Only)
    path("admin-panel/", views.admin_panel, name="admin_panel"),
    path("admin-panel/company/", views.company_settings_update, name="company_settings_update"),
    path("admin-panel/purge-45-days/", views.run_45day_purge, name="run_45day_purge"),
    path("admin-panel/users/create/", views.admin_user_create, name="admin_user_create"),
    path("admin-panel/users/<int:user_id>/edit/", views.admin_user_edit, name="admin_user_edit"),
    path("admin-panel/users/<int:user_id>/delete/", views.admin_client_delete, name="admin_client_delete"),
    path("admin-panel/users/<int:user_id>/password/", views.admin_user_password_change, name="admin_user_password_change"),
    path("admin-panel/clients/<int:client_id>/wipe-db/", views.admin_wipe_client_database, name="admin_wipe_client_database"),
    path("admin-panel/sessions/<int:session_id>/revoke/", views.admin_revoke_device_session, name="admin_revoke_device_session"),
    path("admin-panel/devices/<int:device_id>/revoke/", views.admin_revoke_registered_device, name="admin_revoke_registered_device"),
    path("admin-panel/data/download-all/", views.admin_download_all_data, name="admin_download_all_data"),
    path("admin-panel/customers/<int:customer_id>/delete/", views.admin_delete_customer_or_data, name="admin_delete_customer_or_data"),
    path("admin-panel/activity/export-txt/", views.admin_export_activity_log_txt, name="admin_export_activity_log_txt"),

    # Products (Add, Edit, Update, Remove, Add Category)
    path("products/", views.product_list, name="product_list"),
    path("products/add/", views.product_add, name="product_add"),
    path("products/category/add/", views.category_add, name="category_add"),
    path("products/<int:product_id>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:product_id>/delete/", views.product_delete, name="product_delete"),

    # Stock Management
    path("stock/", views.stock_management, name="stock_management"),
    path("stock/add/", views.stock_add_inward, name="stock_add_inward"),
    path("stock/<int:product_id>/adjust/", views.stock_adjust_quantity, name="stock_adjust_quantity"),
    path("stock/export-csv/", views.stock_export_csv, name="stock_export_csv"),

    # Statements & Reports (Daily / Weekly / Monthly / Custom Statements & CSV/PDF Exports)
    path("reports/", views.reports_dashboard_view, name="reports_dashboard"),
    path("reports/view/", views.reports_dashboard_view, name="reports"),
    path("reports/export-csv/", views.reports_export_csv, name="reports_export_csv"),
    path("reports/export-pdf/", views.reports_export_pdf, name="reports_export_pdf"),

    # Own Shop Bills (Legacy Route -> Redirects to Statements & Reports)
    path("shop-bills/", views.shop_bills, name="shop_bills"),
    path("shop-bills/export-csv/", views.shop_bills_export_csv, name="shop_bills_export_csv"),

    # Client Profile & Branches Management
    path("profile/", views.client_profile_view, name="client_profile"),
    path("profile/branches/add/", views.client_branch_add, name="client_branch_add"),
    path("profile/branches/<int:branch_id>/edit/", views.client_branch_edit, name="client_branch_edit"),
    path("profile/branches/<int:branch_id>/delete/", views.client_branch_delete, name="client_branch_delete"),

    # Software Updates (Admin Publish & Client Apply)
    path("admin-panel/updates/publish/", views.admin_publish_software_update, name="admin_publish_software_update"),
    path("software-update/apply/", views.client_apply_software_update, name="client_apply_software_update"),

    # Customers (Directory, Ledger, Statement, Manual Delete, CSV Exports)
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/add/", views.customer_add, name="customer_add"),
    path("customers/<int:customer_id>/edit/", views.customer_edit, name="customer_edit"),
    path("customers/<int:customer_id>/delete/", views.customer_delete, name="customer_delete"),
    path("customers/<int:customer_id>/export-csv/", views.customer_export_csv, name="customer_export_csv"),
    path("customers/export-all-csv/", views.customers_export_all_csv, name="customers_export_all_csv"),

    # Admin Panel - Shop Branches Management (Admin Only)
    path("admin-panel/branches/add/", views.branch_add, name="branch_add"),
    path("admin-panel/branches/<int:branch_id>/edit/", views.branch_edit, name="branch_edit"),
    path("admin-panel/branches/<int:branch_id>/delete/", views.branch_delete, name="branch_delete"),

    # Background REST API for Windows App auto-sync and real-time live poller
    path("api/health/", api_views.HealthCheckView.as_view(), name="api-health"),
    path("api/live-status/", api_views.LiveStatusView.as_view(), name="api-live-status"),
    path("api/updates/check/", api_views.UpdateCheckView.as_view(), name="api-update-check"),
    path("api/updates/download/exe/", api_views.UpdateDownloadView.as_view(), name="api-update-download-exe"),
    path("api/sync/push/", api_views.SyncPushView.as_view(), name="api-sync-push"),
    path("api/sync/pull/", api_views.SyncPullView.as_view(), name="api-sync-pull"),
    path(
        "api/schema/",
        get_schema_view(
            title="SmartBilling Cloud & POS API",
            description="REST API documentation and endpoints for SmartBilling Windows Desktop App and Postman",
            version="1.0.0",
            renderer_classes=[JSONOpenAPIRenderer],
        ),
        name="openapi-schema",
    ),
    path("api/", include(router.urls)),
]

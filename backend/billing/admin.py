from django.contrib import admin
from .models import (
    Product, Customer, Invoice, InvoiceItem, Purchase, PaymentRecord,
    ActiveUserSession, SyncLog, UserProfile, ActivityLog, CompanySettings,
    Branch, StockLog, SoftwareUpdate
)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    readonly_fields = ("total_price",)


class PaymentRecordInline(admin.TabularInline):
    model = PaymentRecord
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name_tamil", "name", "sku", "unit", "price", "cost_price", "stock_quantity", "is_active", "updated_at")
    list_filter = ("category", "unit", "is_active")
    search_fields = ("name_tamil", "name", "sku")
    list_editable = ("price", "cost_price", "stock_quantity", "unit", "is_active")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "gst_number", "created_at")
    search_fields = ("name", "phone", "email")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("purchase_number", "supplier_name", "total_amount", "payment_status", "created_at")
    search_fields = ("purchase_number", "supplier_name")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer_name", "grand_total", "paid_amount", "balance_amount", "payment_method", "payment_status", "created_at")
    list_filter = ("payment_status", "payment_method", "created_at")
    search_fields = ("invoice_number", "customer_name", "customer_phone", "invoice_uuid")
    inlines = [InvoiceItemInline, PaymentRecordInline]
    readonly_fields = ("invoice_uuid", "synced_at")


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = ("invoice", "amount", "payment_method", "created_at")
    list_filter = ("payment_method", "created_at")


@admin.register(ActiveUserSession)
class ActiveUserSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "session_key", "device_info", "ip_address", "created_at", "last_activity")
    search_fields = ("user__username", "device_info", "ip_address")


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ("device_id", "sync_type", "records_count", "status", "created_at")
    list_filter = ("sync_type", "status", "created_at")
    search_fields = ("device_id", "details")
    readonly_fields = ("created_at",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "business_type", "device_limit", "phone", "shop_name", "created_at")
    list_filter = ("role", "business_type")
    search_fields = ("user__username", "user__email", "phone", "shop_name", "bank_name")


@admin.register(SoftwareUpdate)
class SoftwareUpdateAdmin(admin.ModelAdmin):
    list_display = ("version", "title", "is_published", "published_at", "created_at")
    list_filter = ("is_published", "created_at")
    search_fields = ("version", "title", "release_notes")



@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "action_type", "description", "ip_address")
    list_filter = ("action_type", "created_at")
    search_fields = ("username", "description", "ip_address")
    readonly_fields = ("created_at", "user", "username", "action_type", "description", "ip_address")


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ("company_name", "phone", "email", "gst_number", "updated_at")


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "branch_code", "phone", "manager_name", "is_active", "is_default", "created_at")
    list_filter = ("is_active", "is_default")
    search_fields = ("name", "branch_code", "phone", "manager_name")


@admin.register(StockLog)
class StockLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "change_type", "quantity_change", "previous_quantity", "new_quantity", "created_by")
    list_filter = ("change_type", "created_at")
    search_fields = ("product__name", "product__name_tamil", "product__sku", "notes", "created_by")
    readonly_fields = ("created_at",)

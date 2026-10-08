import uuid
from decimal import Decimal
from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User


class Product(models.Model):
    UNIT_CHOICES = (
        ("KG", "Kilogram (KG)"),
        ("Gram", "Gram (g)"),
        ("Pack", "Pack / Bag (Pack)"),
        ("Box", "Box"),
        ("Pcs", "Pieces (Pcs)"),
        ("Ltr", "Liter (Ltr)"),
        ("Set", "Set"),
        ("Nos", "Numbers (Nos)"),
        ("Unit", "Unit"),
    )

    name_tamil = models.CharField(max_length=200, default="", blank=True, help_text="Tamil Name (e.g. அரிசி, சர்க்கரை)")
    name = models.CharField(max_length=200, blank=True, default="", help_text="English Name (Optional)")
    client = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="products")
    sku = models.CharField(max_length=50, db_index=True)
    category = models.CharField(max_length=100, default="General", db_index=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Selling price")
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Purchase / Buy price")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="GST / Tax percentage")
    stock_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default="KG")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_tamil", "name"]
        unique_together = [("client", "sku")]

    @property
    def display_name(self):
        if self.name_tamil and self.name:
            return f"{self.name_tamil} / {self.name}"
        return self.name_tamil or self.name

    def __str__(self):
        return f"{self.display_name} ({self.unit}) - ₹{self.price}"


class ProductCategory(models.Model):
    """Product Categories created by shop admin / client"""
    name = models.CharField(max_length=100)
    client = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="product_categories")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Customer(models.Model):
    name = models.CharField(max_length=150)
    client = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="customers")
    phone = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    gst_number = models.CharField(max_length=30, blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} {f'({self.phone})' if self.phone else ''}"

    @property
    def total_billed(self):
        return self.invoices.aggregate(total=models.Sum("grand_total"))["total"] or Decimal("0.00")

    @property
    def total_paid(self):
        return self.invoices.aggregate(total=models.Sum("paid_amount"))["total"] or Decimal("0.00")

    @property
    def total_pending(self):
        return self.invoices.aggregate(total=models.Sum("balance_amount"))["total"] or Decimal("0.00")


class Branch(models.Model):
    """
    Shop Branches Management:
    Allows admin to add and manage shop branches before generating Express Bills.
    """
    name = models.CharField(max_length=150, help_text="Branch / Shop Name (e.g. Main Shop, Branch 1)")
    branch_code = models.CharField(max_length=50, unique=True, help_text="Branch Identifier Code (e.g. BR-01)")
    client = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="branches")
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    manager_name = models.CharField(max_length=150, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.branch_code})"

    @classmethod
    def get_default_branch(cls):
        b = cls.objects.filter(is_default=True).first()
        if not b:
            b = cls.objects.filter(is_active=True).first()
        if not b:
            b, _ = cls.objects.get_or_create(
                branch_code="MAIN-01",
                defaults={
                    "name": "Main Shop Branch",
                    "address": "Main Commercial Complex",
                    "phone": "+91 98765 43210",
                    "is_default": True,
                    "is_active": True,
                }
            )
        return b


class Purchase(models.Model):
    """Tracks store purchases / Total Buy from suppliers"""
    purchase_number = models.CharField(max_length=50, unique=True)
    client = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="purchases")
    supplier_name = models.CharField(max_length=200, default="Vendor")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    payment_status = models.CharField(max_length=20, default="Paid")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Purchase #{self.purchase_number} - ₹{self.total_amount}"


class Invoice(models.Model):
    PAYMENT_METHODS = (
        ("Cash", "Cash"),
        ("Online", "Online Payment (UPI/Card)"),
        ("Bank Transfer", "Bank Transfer"),
        ("Credit", "Credit / Due"),
    )

    PAYMENT_STATUS = (
        ("Paid", "Paid"),
        ("Pending", "Pending / Due"),
        ("Partial", "Partial"),
    )

    SOURCE_CHOICES = (
        ("windows_app", "Windows Desktop App"),
        ("web_mobile", "Web / Mobile"),
    )

    invoice_uuid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    client = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="invoices")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    branch_name = models.CharField(max_length=150, blank=True, default="Main Shop Branch")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    customer_name = models.CharField(max_length=150, default="Cash Customer")
    customer_phone = models.CharField(max_length=20, blank=True, default="")
    customer_address = models.TextField(blank=True, default="")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    # Customer Paid & Balance tracking
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Amount customer paid")
    balance_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Remaining balance due")

    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHODS, default="Cash")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="Paid")
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default="windows_app")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Auto-compute grand total and balance with discounts/offers
        if self.subtotal is None:
            self.subtotal = Decimal("0.00")
        self.subtotal = Decimal(str(self.subtotal or "0.00"))
        self.tax_amount = Decimal(str(self.tax_amount or "0.00"))
        self.discount_amount = Decimal(str(self.discount_amount or "0.00"))
        self.paid_amount = Decimal(str(self.paid_amount or "0.00"))

        # Automatically calculate grand total taking discount into account
        expected_grand = max(Decimal("0.00"), self.subtotal + self.tax_amount - self.discount_amount)
        if self.subtotal > Decimal("0.00") or self.discount_amount > Decimal("0.00"):
            self.grand_total = expected_grand
        elif self.grand_total is None:
            self.grand_total = Decimal("0.00")

        self.balance_amount = max(Decimal("0.00"), self.grand_total - self.paid_amount)
        if self.balance_amount == Decimal("0.00") and self.paid_amount > Decimal("0.00"):
            self.payment_status = "Paid"
        elif self.paid_amount > Decimal("0.00") and self.balance_amount > Decimal("0.00"):
            self.payment_status = "Partial"
        else:
            self.payment_status = "Pending"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} - {self.customer_name} - ₹{self.grand_total} (Bal: ₹{self.balance_amount})"


class PaymentRecord(models.Model):
    """Audit log of individual payment additions (Cash or Online)"""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=Invoice.PAYMENT_METHODS, default="Cash")
    notes = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment ₹{self.amount} for {self.invoice.invoice_number} via {self.payment_method}"


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_items")
    product_name = models.CharField(max_length=200)
    product_sku = models.CharField(max_length=50, blank=True, default="")
    unit = models.CharField(max_length=20, default="KG")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1.00)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product_name} ({self.quantity} {self.unit}) = ₹{self.total_price}"


class StockLog(models.Model):
    """
    Stock Management Audit Ledger:
    Tracks all inventory modifications: billing sales, stock additions, and manual adjustments.
    """
    CHANGE_TYPES = (
        ("BILLING_SALE", "Customer Purchase Sale"),
        ("RESTOCK", "Stock Inward / Addition"),
        ("MANUAL_ADJUSTMENT", "Manual Stock Quantity Adjustment"),
        ("BILLING_RESTORE", "Bill Edit / Stock Restored"),
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_logs")
    change_type = models.CharField(max_length=30, choices=CHANGE_TYPES, default="RESTOCK")
    quantity_change = models.DecimalField(max_digits=10, decimal_places=2, help_text="Positive for additions, negative for sales")
    previous_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    new_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_logs")
    notes = models.TextField(blank=True, default="")
    created_by = models.CharField(max_length=150, default="Admin")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.display_name} [{self.change_type}]: {self.quantity_change:+} (Now: {self.new_quantity})"


class ActiveUserSession(models.Model):
    """
    Limits concurrent user sessions to a maximum of 5 active devices/persons
    for the single shared business login ID.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="active_sessions")
    session_key = models.CharField(max_length=40, unique=True)
    device_info = models.CharField(max_length=255, default="Desktop/Mobile Browser")
    ip_address = models.CharField(max_length=50, default="127.0.0.1")
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_activity"]

    def __str__(self):
        return f"{self.user.username} session {self.session_key[:8]} ({self.device_info})"


class RegisteredDevice(models.Model):
    """
    Admin-controlled Device Quota Enforcement:
    Tracks approved devices for each client. When an admin specifies a device limit
    (e.g., 5 devices), only up to 5 unique devices can log in.
    Any attempt by a 6th device is strictly BLOCKED.
    """
    DEVICE_TYPES = (
        ("desktop_exe", "Windows Desktop POS (EXE)"),
        ("web_browser", "Web Browser / Mobile"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="registered_devices")
    device_id = models.CharField(max_length=120, db_index=True, help_text="Unique hardware/device fingerprint")
    device_name = models.CharField(max_length=150, default="POS Terminal")
    device_type = models.CharField(max_length=50, choices=DEVICE_TYPES, default="desktop_exe")
    ip_address = models.CharField(max_length=50, default="127.0.0.1")
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=True, help_text="True if admin verified or approved via OTP")
    otp_code = models.CharField(max_length=10, blank=True, default="", help_text="6-digit activation code sent to admin panel")
    otp_created_at = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "device_id")
        ordering = ["-last_login"]

    def __str__(self):
        return f"{self.user.username} - {self.device_name} ({self.device_id[:12]})"

    def generate_otp(self):
        import random
        self.otp_code = f"{random.randint(100000, 999999)}"
        self.otp_created_at = timezone.now()
        self.is_verified = False
        self.save()
        return self.otp_code

    def verify_otp(self, code):
        if not self.otp_code or not code:
            return False
        if self.otp_created_at and timezone.now() - self.otp_created_at > timedelta(minutes=30):
            return False
        if str(code).strip() == str(self.otp_code).strip():
            self.is_verified = True
            self.is_active = True
            self.otp_code = ""
            self.save()
            return True
        return False


class SyncLog(models.Model):
    device_id = models.CharField(max_length=100, db_index=True)
    sync_type = models.CharField(max_length=20)  # 'push' or 'pull'
    records_count = models.IntegerField(default=0)
    status = models.CharField(max_length=20, default="success")
    details = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {self.device_id} - {self.sync_type} ({self.records_count} records)"


class UserProfile(models.Model):
    """
    Extends standard User with custom attributes including phone, avatar, and role.
    Only users with role='admin' can view or access Admin functions.
    Clients are restricted to billing software only.
    """
    ROLE_CHOICES = (
        ("admin", "Administrator"),
        ("client", "Client User / Staff"),
    )

    BUSINESS_TYPES = (
        ("grocery", "Grocery Shop"),
        ("mobile_computer", "Mobile & Computer Shop"),
    )

    ACCESS_MODES = (
        ("online_offline", "Online & Offline (Web + Desktop App)"),
        ("offline_only", "Offline POS Only (Desktop EXE)"),
        ("online_only", "Online Only (Web Portal)"),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True, default="", help_text="Mobile / Contact number")
    shop_name = models.CharField(max_length=150, blank=True, default="", help_text="Client Business / Shop Name")
    shop_address = models.TextField(blank=True, default="", help_text="Client Business / Shop Address")
    business_type = models.CharField(max_length=30, choices=BUSINESS_TYPES, default="grocery", help_text="Store Business Category")
    access_mode = models.CharField(max_length=30, choices=ACCESS_MODES, default="online_offline", help_text="Platform access permissions")
    device_limit = models.IntegerField(default=5, help_text="Maximum allowed active devices")
    bank_name = models.CharField(max_length=150, blank=True, default="", help_text="Bank Name")
    account_number = models.CharField(max_length=60, blank=True, default="", help_text="Bank Account Number")
    ifsc_code = models.CharField(max_length=30, blank=True, default="", help_text="Bank IFSC Code")
    gst_number = models.CharField(max_length=50, blank=True, default="", help_text="Client Business GSTIN Number")
    avatar_image = models.ImageField(upload_to="client_avatars/", blank=True, null=True)
    avatar_base64 = models.TextField(blank=True, default="", help_text="Base64 encoded client profile image")
    shop_logo_image = models.ImageField(upload_to="client_logos/", blank=True, null=True)
    shop_logo_base64 = models.TextField(blank=True, default="", help_text="Base64 encoded shop logo")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="client")
    initial_password = models.CharField(max_length=128, blank=True, default="", help_text="Client login password set by Admin")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def save(self, *args, **kwargs):
        if self.shop_name and "supermarket" in self.shop_name.lower():
            self.shop_name = self.shop_name.replace("Supermarket", "").replace("supermarket", "").strip()
            if not self.shop_name:
                self.shop_name = "MathanHub"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()}) - {self.get_business_type_display()}"

    @property
    def is_admin(self):
        return self.role == "admin" or self.user.is_superuser

    @property
    def avatar_url(self):
        if self.avatar_base64:
            return self.avatar_base64
        if self.avatar_image:
            try:
                return self.avatar_image.url
            except Exception:
                pass
        return ""

    @property
    def shop_logo_url(self):
        if self.shop_logo_base64:
            return self.shop_logo_base64
        if self.shop_logo_image:
            try:
                return self.shop_logo_image.url
            except Exception:
                pass
        return self.avatar_url


class ActivityLog(models.Model):
    """
    Primary Cloud Database storage for all important application activities.
    Visible only to Administrators inside the Admin Panel.
    Can be exported to a TXT file on demand.
    """
    ACTION_CHOICES = (
        ("LOGIN", "User Login"),
        ("LOGOUT", "User Logout"),
        ("INVOICE_CREATE", "Invoice Created"),
        ("PAYMENT_UPDATE", "Payment Updated"),
        ("DISCOUNT_APPLIED", "Offer / Discount Applied"),
        ("PRODUCT_ADD", "Product Added"),
        ("PRODUCT_EDIT", "Product Edited"),
        ("PRODUCT_DELETE", "Product Removed"),
        ("CUSTOMER_ADD", "Customer Added"),
        ("CUSTOMER_EDIT", "Customer Altered"),
        ("USER_CREATE", "User Created"),
        ("USER_EDIT", "User Account Edited"),
        ("USER_DELETE", "User Account Deleted"),
        ("SESSION_REVOKE", "Device Session Disconnected"),
        ("PASSWORD_CHANGE", "Password Reset"),
        ("INVOICE_EDIT", "Invoice Altered / Edited"),
        ("DATA_CLEANUP", "Data Cleanup"),
        ("COMPANY_UPDATE", "Company Profile & Branding Updated"),
        ("BRANCH_CREATE", "Shop Branch Created"),
        ("BRANCH_EDIT", "Shop Branch Updated"),
        ("BRANCH_DELETE", "Shop Branch Deleted"),
        ("STOCK_ADD", "Stock Inward / Added"),
        ("STOCK_UPDATE", "Stock Quantity Adjusted"),
        ("CUSTOMER_DELETE", "Customer Record Deleted"),
        ("UPDATE_PUBLISH", "Software Update Published"),
        ("PROFILE_UPDATE", "Client Profile & Business Details Updated"),
        ("REPORT_EXPORT", "Statement / Sales Report Exported"),
        ("DEVICE_REGISTER", "Device Registered / Activated"),
        ("DEVICE_REVOKE", "Device Access Revoked"),
        ("DEVICE_LIMIT_BLOCKED", "Device Quota Exceeded Blocked"),
        ("DEVICE_OTP", "New Device Verification OTP"),
        ("DEVICE_VERIFY", "Device OTP Verified & Approved"),
    )

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity_logs")
    username = models.CharField(max_length=150, default="System")
    action_type = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)
    description = models.TextField()
    ip_address = models.CharField(max_length=50, blank=True, default="127.0.0.1")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {self.username} - {self.action_type}: {self.description[:40]}"


def log_activity(request_or_user, action_type, description, ip_address=None):
    """
    Records an application activity directly into the cloud database (main record).
    Visible only inside the Admin Panel.
    """
    try:
        user = None
        username = "System"
        ip = ip_address or "127.0.0.1"

        if hasattr(request_or_user, "user"):
            req = request_or_user
            if req.user.is_authenticated:
                user = req.user
                username = req.user.username
            else:
                username = req.POST.get("username", "Anonymous")
            fwd = req.META.get("HTTP_X_FORWARDED_FOR")
            if fwd:
                ip = fwd.split(",")[0].strip()
            else:
                ip = req.META.get("REMOTE_ADDR", "127.0.0.1")
        elif hasattr(request_or_user, "username"):
            user = request_or_user
            username = user.username

        ActivityLog.objects.create(
            user=user,
            username=username,
            action_type=action_type,
            description=description,
            ip_address=ip[:50] if ip else "127.0.0.1",
        )
    except Exception:
        pass


from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_or_save_user_profile(sender, instance, created, **kwargs):
    """Auto-creates or updates UserProfile for every user"""
    if created:
        role = "admin" if instance.is_superuser else "client"
        UserProfile.objects.get_or_create(user=instance, defaults={"role": role})
    elif hasattr(instance, "profile"):
        instance.profile.save()


class CompanySettings(models.Model):
    """
    Company Profile & Receipt Branding:
    - Company Name, Address, Phone, Email, GST Number
    - Header Logo Image & Watermark Image
    - Base64 encoded cache for offline / standalone EXE portability
    """
    company_name = models.CharField(max_length=200, default="MathanHub")
    address = models.TextField(blank=True, default="Main Market Road, Commercial Complex")
    phone = models.CharField(max_length=50, blank=True, default="+91 98765 43210")
    email = models.CharField(max_length=100, blank=True, default="contact@smartbilling.local")
    gst_number = models.CharField(max_length=50, blank=True, default="")
    bank_name = models.CharField(max_length=150, blank=True, default="State Bank of India", help_text="Bank Name")
    account_number = models.CharField(max_length=60, blank=True, default="30294819283", help_text="Bank Account Number")
    ifsc_code = models.CharField(max_length=30, blank=True, default="SBIN0001234", help_text="Bank IFSC Code")
    logo_image = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    watermark_image = models.ImageField(upload_to="company_watermarks/", blank=True, null=True)
    logo_base64 = models.TextField(blank=True, default="")
    watermark_base64 = models.TextField(blank=True, default="")
    auto_delete_days = models.IntegerField(default=45, help_text="Auto-delete customer data older than X days")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company Profile & Branding"
        verbose_name_plural = "Company Profile & Branding"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        needs_save = False
        if obj.company_name and "supermarket" in obj.company_name.lower():
            obj.company_name = obj.company_name.replace("Supermarket", "").replace("supermarket", "").strip()
            if not obj.company_name:
                obj.company_name = "MathanHub"
            needs_save = True
        if obj.email and "supermarket" in obj.email.lower():
            obj.email = "contact@mathanhub.com"
            needs_save = True
        if needs_save:
            obj.save()
        return obj

    @property
    def logo_data_url(self):
        if self.logo_base64:
            return self.logo_base64
        if self.logo_image:
            try:
                return self.logo_image.url
            except Exception:
                pass
        return ""

    @property
    def watermark_data_url(self):
        if self.watermark_base64:
            return self.watermark_base64
        if self.watermark_image:
            try:
                return self.watermark_image.url
            except Exception:
                pass
        return ""

    def __str__(self):
        return self.company_name


def purge_old_customer_data(days=None):
    """
    Automatic customer data deletion is DISABLED:
    Customer records must NOT be automatically deleted based on date;
    all customer records and invoices are permanently retained unless
    the admin explicitly chooses to manually delete a customer record.
    """
    return 0


class SoftwareUpdate(models.Model):
    """
    Software Updates Published by Administrator:
    Notifies all client terminals of published updates with non-disruptive update workflow.
    """
    version = models.CharField(max_length=50, default="v2.5.0")
    title = models.CharField(max_length=200, default="System Improvements & Updates")
    release_notes = models.TextField(blank=True, default="")
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.version} - {self.title} ({'Published' if self.is_published else 'Draft'})"

    @classmethod
    def get_latest_update(cls):
        try:
            from .version import APP_VERSION, APP_TITLE, APP_RELEASE_NOTES
            latest = cls.objects.filter(is_published=True).order_by("-created_at").first()
            if not latest or latest.version != APP_VERSION:
                latest, _ = cls.objects.update_or_create(
                    version=APP_VERSION,
                    defaults={
                        "title": APP_TITLE,
                        "release_notes": APP_RELEASE_NOTES,
                        "is_published": True,
                    }
                )
            return latest
        except Exception:
            return cls.objects.filter(is_published=True).order_by("-created_at").first()


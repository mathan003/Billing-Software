from rest_framework import serializers
from .models import Product, Customer, Invoice, InvoiceItem, SyncLog


class ProductSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "name_tamil",
            "display_name",
            "sku",
            "category",
            "price",
            "cost_price",
            "tax_percent",
            "stock_quantity",
            "unit",
            "is_active",
            "updated_at",
            "created_at",
        ]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "address",
            "gst_number",
            "created_at",
            "updated_at",
        ]


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "unit_price",
            "quantity",
            "tax_percent",
            "tax_amount",
            "discount_percent",
            "total_price",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_uuid",
            "invoice_number",
            "customer",
            "customer_name",
            "customer_phone",
            "customer_address",
            "subtotal",
            "tax_amount",
            "discount_amount",
            "grand_total",
            "payment_method",
            "payment_status",
            "source",
            "notes",
            "created_at",
            "synced_at",
            "items",
        ]


class SyncItemPayloadSerializer(serializers.Serializer):
    product_sku = serializers.CharField(max_length=50, required=False, allow_blank=True)
    product_name = serializers.CharField(max_length=200)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    tax_percent = serializers.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)


class SyncInvoicePayloadSerializer(serializers.Serializer):
    invoice_uuid = serializers.UUIDField()
    invoice_number = serializers.CharField(max_length=50)
    customer_name = serializers.CharField(max_length=150, default="Cash Customer")
    customer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    customer_address = serializers.CharField(required=False, allow_blank=True, default="")
    customer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0.00)
    balance_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0.00)
    payment_method = serializers.CharField(max_length=30, default="Cash")
    payment_status = serializers.CharField(max_length=20, default="Paid")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    created_at = serializers.DateTimeField()
    items = SyncItemPayloadSerializer(many=True)


class SyncProductPayloadSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    name_tamil = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    category = serializers.CharField(max_length=100, default="General")
    unit = serializers.CharField(max_length=20, default="KG")
    price = serializers.DecimalField(max_digits=12, decimal_places=2)
    cost_price = serializers.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    tax_percent = serializers.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    stock_quantity = serializers.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    is_active = serializers.BooleanField(default=True)


class SyncCustomerPayloadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    gst_number = serializers.CharField(required=False, allow_blank=True, default="")


class SyncPaymentPayloadSerializer(serializers.Serializer):
    invoice_number = serializers.CharField(max_length=50)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_method = serializers.CharField(max_length=30, default="Cash")
    notes = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class SyncClientPayloadSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    role = serializers.CharField(max_length=20, default="client")
    shop_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    shop_address = serializers.CharField(required=False, allow_blank=True, default="")
    business_type = serializers.CharField(max_length=50, default="grocery")
    access_mode = serializers.CharField(max_length=30, default="online_offline")
    device_limit = serializers.IntegerField(default=5)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    gst_number = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    bank_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    account_number = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    ifsc_code = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    avatar_base64 = serializers.CharField(required=False, allow_blank=True, default="")
    shop_logo_base64 = serializers.CharField(required=False, allow_blank=True, default="")
    password_hash = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    initial_password = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    is_active = serializers.BooleanField(default=True)


class SyncPushRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    client_username = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    invoices = SyncInvoicePayloadSerializer(many=True, required=False, default=list)
    products = SyncProductPayloadSerializer(many=True, required=False, default=list)
    customers = SyncCustomerPayloadSerializer(many=True, required=False, default=list)
    payments = SyncPaymentPayloadSerializer(many=True, required=False, default=list)
    categories = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    clients = SyncClientPayloadSerializer(many=True, required=False, default=list)

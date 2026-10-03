import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import status, views, viewsets
from rest_framework.response import Response
from .models import Product, Customer, Invoice, InvoiceItem, SyncLog
from .serializers import (
    ProductSerializer,
    CustomerSerializer,
    InvoiceSerializer,
    SyncPushRequestSerializer,
)

logger = logging.getLogger(__name__)


class HealthCheckView(views.APIView):
    """
    Lightweight health check endpoint for Windows desktop app
    to check connectivity before auto-syncing.
    """
    def get(self, request):
        return Response({
            "status": "online",
            "server": "Django Billing Cloud Server",
            "version": "1.0.0",
            "server_time": timezone.now().isoformat(),
        })


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get("category")
        search = self.request.query_params.get("search")
        if category:
            qs = qs.filter(category__iexact=category)
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(sku__icontains=search)
        return qs


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(phone__icontains=search)
        return qs


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.prefetch_related("items").all()
    serializer_class = InvoiceSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        source = self.request.query_params.get("source")
        payment_status = self.request.query_params.get("status")
        if source:
            qs = qs.filter(source=source)
        if payment_status:
            qs = qs.filter(payment_status=payment_status)
        return qs


class SyncPushView(views.APIView):
    """
    Receives batch of invoices created offline in the Windows Desktop App.
    Guarantees idempotency using invoice_uuid.
    Updates stock and creates customers if needed.
    """
    def post(self, request):
        serializer = SyncPushRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        device_id = data["device_id"]
        invoices_data = data.get("invoices", [])
        products_data = data.get("products", [])
        customers_data = data.get("customers", [])
        payments_data = data.get("payments", [])

        synced_uuids = []
        synced_products = []
        synced_customers = []
        synced_payments = []

        try:
            with transaction.atomic():
                # 1. Sync Products (created, updated, or removed/deactivated offline)
                for p_data in products_data:
                    sku = p_data.get("sku")
                    if not sku:
                        continue
                    product, created = Product.objects.get_or_create(
                        sku=sku,
                        defaults={
                            "name": p_data.get("name", ""),
                            "name_tamil": p_data.get("name_tamil", ""),
                            "category": p_data.get("category", "General"),
                            "unit": p_data.get("unit", "KG"),
                            "price": p_data["price"],
                            "cost_price": p_data.get("cost_price", 0.00),
                            "tax_percent": p_data.get("tax_percent", 0.00),
                            "stock_quantity": p_data.get("stock_quantity", 0.00),
                            "is_active": p_data.get("is_active", True),
                        }
                    )
                    if not created:
                        product.name = p_data.get("name", product.name)
                        product.name_tamil = p_data.get("name_tamil", product.name_tamil)
                        product.category = p_data.get("category", product.category)
                        product.unit = p_data.get("unit", product.unit)
                        product.price = p_data["price"]
                        product.cost_price = p_data.get("cost_price", product.cost_price)
                        product.tax_percent = p_data.get("tax_percent", product.tax_percent)
                        product.stock_quantity = p_data.get("stock_quantity", product.stock_quantity)
                        product.is_active = p_data.get("is_active", product.is_active)
                        product.save()
                    synced_products.append(sku)

                # 2. Sync Customers (created or altered offline)
                for c_data in customers_data:
                    phone = c_data.get("phone", "").strip()
                    if phone:
                        cust, created = Customer.objects.get_or_create(
                            phone=phone,
                            defaults={
                                "name": c_data["name"],
                                "email": c_data.get("email", ""),
                                "address": c_data.get("address", ""),
                                "gst_number": c_data.get("gst_number", ""),
                            }
                        )
                        if not created:
                            cust.name = c_data["name"]
                            cust.email = c_data.get("email", cust.email)
                            cust.address = c_data.get("address", cust.address)
                            cust.gst_number = c_data.get("gst_number", cust.gst_number)
                            cust.save()
                        synced_customers.append(phone)

                # 3. Sync Invoices (created offline)
                for inv_data in invoices_data:
                    inv_uuid = inv_data["invoice_uuid"]

                    # Check if already synced previously
                    existing_inv = Invoice.objects.filter(invoice_uuid=inv_uuid).first()
                    if existing_inv:
                        synced_uuids.append(str(inv_uuid))
                        continue

                    # Check or create customer if phone is provided
                    cust_obj = None
                    cust_phone = inv_data.get("customer_phone", "").strip()
                    cust_name = inv_data.get("customer_name", "Cash Customer").strip()

                    if cust_phone:
                        cust_obj = Customer.objects.filter(phone=cust_phone).first()
                        if not cust_obj and cust_name:
                            cust_obj = Customer.objects.create(
                                name=cust_name,
                                phone=cust_phone,
                                email=inv_data.get("customer_email", ""),
                            )

                    # Ensure unique invoice_number on server
                    inv_num = inv_data["invoice_number"]
                    if Invoice.objects.filter(invoice_number=inv_num).exists():
                        inv_num = f"{inv_num}-{str(inv_uuid)[:6]}"

                    # Create Invoice record
                    invoice = Invoice.objects.create(
                        invoice_uuid=inv_uuid,
                        invoice_number=inv_num,
                        customer=cust_obj,
                        customer_name=cust_name or "Cash Customer",
                        customer_phone=cust_phone,
                        subtotal=inv_data["subtotal"],
                        tax_amount=inv_data["tax_amount"],
                        discount_amount=inv_data["discount_amount"],
                        grand_total=inv_data["grand_total"],
                        paid_amount=inv_data.get("paid_amount", inv_data["grand_total"]),
                        payment_method=inv_data.get("payment_method", "Cash"),
                        payment_status=inv_data.get("payment_status", "Paid"),
                        source="windows_app",
                        notes=inv_data.get("notes", ""),
                        created_at=inv_data["created_at"],
                    )

                    # Create Invoice Items & update stock
                    for item_data in inv_data["items"]:
                        sku = item_data.get("product_sku", "").strip()
                        product_match = None
                        if sku:
                            product_match = Product.objects.filter(sku=sku).first()
                            if product_match:
                                qty = item_data["quantity"]
                                product_match.stock_quantity = max(0, product_match.stock_quantity - qty)
                                product_match.save(update_fields=["stock_quantity", "updated_at"])

                        InvoiceItem.objects.create(
                            invoice=invoice,
                            product=product_match,
                            product_name=item_data["product_name"],
                            product_sku=sku,
                            unit_price=item_data["unit_price"],
                            quantity=item_data["quantity"],
                            tax_percent=item_data.get("tax_percent", 0.00),
                            tax_amount=item_data.get("tax_amount", 0.00),
                            discount_percent=item_data.get("discount_percent", 0.00),
                            total_price=item_data["total_price"],
                        )

                    synced_uuids.append(str(inv_uuid))

                # 4. Sync Payments (recorded offline)
                for pay_data in payments_data:
                    inv_num = pay_data.get("invoice_number")
                    amt = pay_data.get("amount")
                    method = pay_data.get("payment_method", "Cash")
                    notes = pay_data.get("notes", "")
                    inv = Invoice.objects.filter(invoice_number=inv_num).first()
                    if inv and amt:
                        PaymentRecord.objects.create(
                            invoice=inv,
                            amount=amt,
                            payment_method=method,
                            notes=notes or f"Payment received via {method}"
                        )
                        inv.paid_amount += amt
                        inv.payment_method = method
                        inv.save()
                        synced_payments.append(inv_num)

                # Log sync operation
                total_synced = len(synced_uuids) + len(synced_products) + len(synced_customers) + len(synced_payments)
                SyncLog.objects.create(
                    device_id=device_id,
                    sync_type="push",
                    records_count=total_synced,
                    status="success",
                    details=f"Synced {len(synced_uuids)} invoices, {len(synced_products)} products, {len(synced_customers)} customers, {len(synced_payments)} payments.",
                )

            return Response({
                "status": "success",
                "synced_count": total_synced,
                "synced_invoices": synced_uuids,
                "synced_uuids": synced_uuids,
                "synced_products": synced_products,
                "synced_customers": synced_customers,
                "synced_payments": synced_payments,
                "server_time": timezone.now().isoformat(),
            })

        except Exception as e:
            logger.exception("Error syncing offline data")
            SyncLog.objects.create(
                device_id=device_id,
                sync_type="push",
                records_count=0,
                status="error",
                details=str(e),
            )
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SyncPullView(views.APIView):
    """
    Supplies the Windows Desktop App with latest product catalog (active and inactive),
    prices, stock levels, customers, and recent invoices from Django server.
    """
    def get(self, request):
        since_str = request.query_params.get("since")
        products_qs = Product.objects.all()
        customers_qs = Customer.objects.all()

        if since_str:
            try:
                products_qs = products_qs.filter(updated_at__gte=since_str)
                customers_qs = customers_qs.filter(updated_at__gte=since_str)
            except Exception:
                pass

        products_data = ProductSerializer(products_qs, many=True).data
        customers_data = CustomerSerializer(customers_qs, many=True).data

        return Response({
            "status": "success",
            "server_time": timezone.now().isoformat(),
            "products_count": len(products_data),
            "products": products_data,
            "customers_count": len(customers_data),
            "customers": customers_data,
        })

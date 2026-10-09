from django.db import migrations


def enforce_client_isolation(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Product = apps.get_model("billing", "Product")
    Customer = apps.get_model("billing", "Customer")
    Invoice = apps.get_model("billing", "Invoice")
    ProductCategory = apps.get_model("billing", "ProductCategory")

    admin_user = User.objects.filter(username__in=["Mathan003", "admin"], is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.filter(is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.first()

    if not admin_user:
        return

    # 1. Clean up or assign unassigned products
    unassigned_prods = list(Product.objects.filter(client__isnull=True))
    for p in unassigned_prods:
        # Check if this SKU already exists for another client
        client_copy = Product.objects.filter(sku=p.sku, client__isnull=False).first()
        if client_copy:
            # Client already has their own dedicated copy of this product, safe to remove unassigned duplicate
            p.delete()
        else:
            # Assign to admin so it stays in the admin database without leaking to clients
            p.client = admin_user
            p.save(update_fields=["client"])

    # 2. Assign unassigned customers
    unassigned_custs = list(Customer.objects.filter(client__isnull=True))
    for c in unassigned_custs:
        if c.phone:
            client_cust = Customer.objects.filter(phone=c.phone, client__isnull=False).first()
            if client_cust:
                c.delete()
                continue
        c.client = admin_user
        c.save(update_fields=["client"])

    # 3. Assign unassigned invoices
    unassigned_invs = list(Invoice.objects.filter(client__isnull=True))
    for inv in unassigned_invs:
        if inv.customer and inv.customer.client:
            inv.client = inv.customer.client
        else:
            inv.client = admin_user
        inv.save(update_fields=["client"])

    # 4. Assign unassigned categories
    unassigned_cats = list(ProductCategory.objects.filter(client__isnull=True))
    for cat in unassigned_cats:
        cat.client = admin_user
        cat.save(update_fields=["client"])


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0018_deletedinvoice"),
    ]

    operations = [
        migrations.RunPython(enforce_client_isolation, reverse_code=migrations.RunPython.noop),
    ]

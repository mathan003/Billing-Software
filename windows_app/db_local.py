import sqlite3
import uuid
from datetime import datetime
try:
    from .config import DB_PATH, DEFAULT_SERVER_URL, DEFAULT_DEVICE_ID
except (ImportError, ValueError):
    from config import DB_PATH, DEFAULT_SERVER_URL, DEFAULT_DEVICE_ID


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Products cache table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            price REAL NOT NULL,
            cost_price REAL DEFAULT 0.0,
            tax_percent REAL DEFAULT 0.0,
            stock_quantity REAL DEFAULT 0.0,
            unit TEXT DEFAULT 'pcs',
            updated_at TEXT
        )
    """)

    # Customers cache table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            phone TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT
        )
    """)

    # Invoices table (offline first, tracks sync_status: PENDING or SYNCED)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_uuid TEXT PRIMARY KEY,
            invoice_number TEXT UNIQUE NOT NULL,
            customer_name TEXT DEFAULT 'Cash Customer',
            customer_phone TEXT DEFAULT '',
            customer_email TEXT DEFAULT '',
            subtotal REAL NOT NULL,
            tax_amount REAL DEFAULT 0.0,
            discount_amount REAL DEFAULT 0.0,
            grand_total REAL NOT NULL,
            payment_method TEXT DEFAULT 'Cash',
            payment_status TEXT DEFAULT 'Paid',
            notes TEXT DEFAULT '',
            source TEXT DEFAULT 'windows_app',
            sync_status TEXT DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            synced_at TEXT
        )
    """)

    # Invoice line items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_uuid TEXT NOT NULL,
            product_sku TEXT DEFAULT '',
            product_name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            quantity REAL NOT NULL,
            tax_percent REAL DEFAULT 0.0,
            tax_amount REAL DEFAULT 0.0,
            discount_percent REAL DEFAULT 0.0,
            total_price REAL NOT NULL,
            FOREIGN KEY (invoice_uuid) REFERENCES invoices(invoice_uuid)
        )
    """)

    # Set default settings if not exists
    cursor.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", ("server_url", DEFAULT_SERVER_URL))
    cursor.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", ("device_id", DEFAULT_DEVICE_ID))
    cursor.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", ("last_sync_time", ""))

    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def upsert_products(product_list):
    """Updates or inserts products fetched from Django server"""
    conn = get_connection()
    cur = conn.cursor()
    for p in product_list:
        prod_title = p.get("display_name") or p.get("name_tamil") or p.get("name", "")
        cur.execute("""
            INSERT INTO products (sku, name, category, price, cost_price, tax_percent, stock_quantity, unit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                price = excluded.price,
                cost_price = excluded.cost_price,
                tax_percent = excluded.tax_percent,
                stock_quantity = excluded.stock_quantity,
                unit = excluded.unit,
                updated_at = excluded.updated_at
        """, (
            p["sku"],
            prod_title,
            p.get("category", "General"),
            float(p["price"]),
            float(p.get("cost_price", 0.0)),
            float(p.get("tax_percent", 0.0)),
            float(p.get("stock_quantity", 0.0)),
            p.get("unit", "KG"),
            p.get("updated_at", datetime.now().isoformat()),
        ))
    conn.commit()
    conn.close()


def get_all_products(search=None):
    conn = get_connection()
    cur = conn.cursor()
    if search:
        query = "%" + search.strip() + "%"
        cur.execute("SELECT * FROM products WHERE name LIKE ? OR sku LIKE ? OR category LIKE ? ORDER BY name ASC", (query, query, query))
    else:
        cur.execute("SELECT * FROM products ORDER BY name ASC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_product_by_sku(sku):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE sku = ?", (sku,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def save_invoice(inv_data, items_data):
    """
    Saves an invoice locally with 'PENDING' sync status.
    Deducts local stock and stores items.
    """
    conn = get_connection()
    cur = conn.cursor()

    inv_uuid = inv_data.get("invoice_uuid") or str(uuid.uuid4())
    created_at = inv_data.get("created_at") or datetime.now().isoformat()

    subtotal = round(float(inv_data["subtotal"]), 2)
    tax_amount = round(float(inv_data.get("tax_amount", 0.0)), 2)
    discount_amount = round(float(inv_data.get("discount_amount", 0.0)), 2)
    grand_total = round(float(inv_data["grand_total"]), 2)

    cur.execute("""
        INSERT INTO invoices (
            invoice_uuid, invoice_number, customer_name, customer_phone,
            customer_email, subtotal, tax_amount, discount_amount,
            grand_total, payment_method, payment_status, notes,
            source, sync_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
    """, (
        inv_uuid,
        inv_data["invoice_number"],
        inv_data.get("customer_name", "Cash Customer"),
        inv_data.get("customer_phone", ""),
        inv_data.get("customer_email", ""),
        subtotal,
        tax_amount,
        discount_amount,
        grand_total,
        inv_data.get("payment_method", "Cash"),
        inv_data.get("payment_status", "Paid"),
        inv_data.get("notes", ""),
        "windows_app",
        created_at,
    ))

    # Insert items & deduct local stock
    for item in items_data:
        unit_price = round(float(item["unit_price"]), 2)
        quantity = round(float(item["quantity"]), 2)
        tax_percent = round(float(item.get("tax_percent", 0.0)), 2)
        tax_amount = round(float(item.get("tax_amount", 0.0)), 2)
        discount_percent = round(float(item.get("discount_percent", 0.0)), 2)
        total_price = round(float(item["total_price"]), 2)

        cur.execute("""
            INSERT INTO invoice_items (
                invoice_uuid, product_sku, product_name, unit_price,
                quantity, tax_percent, tax_amount, discount_percent, total_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            inv_uuid,
            item.get("product_sku", ""),
            item["product_name"],
            unit_price,
            quantity,
            tax_percent,
            tax_amount,
            discount_percent,
            total_price,
        ))

        # Local stock deduction
        if item.get("product_sku"):
            cur.execute("""
                UPDATE products
                SET stock_quantity = MAX(0, stock_quantity - ?)
                WHERE sku = ?
            """, (quantity, item["product_sku"]))

    conn.commit()
    conn.close()

    return {
        "invoice_uuid": inv_uuid,
        "invoice_number": inv_data["invoice_number"],
        "grand_total": grand_total,
        "sync_status": "PENDING",
    }


def get_pending_invoices():
    """Returns all invoices with 'PENDING' status, packaged for Django API push"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM invoices WHERE sync_status = 'PENDING' ORDER BY created_at ASC")
    invoice_rows = [dict(r) for r in cur.fetchall()]

    payload = []
    for inv in invoice_rows:
        cur.execute("SELECT * FROM invoice_items WHERE invoice_uuid = ?", (inv["invoice_uuid"],))
        items = [dict(i) for i in cur.fetchall()]

        payload.append({
            "invoice_uuid": inv["invoice_uuid"],
            "invoice_number": inv["invoice_number"],
            "customer_name": inv["customer_name"],
            "customer_phone": inv["customer_phone"],
            "customer_email": inv["customer_email"],
            "subtotal": round(float(inv["subtotal"]), 2),
            "tax_amount": round(float(inv["tax_amount"]), 2),
            "discount_amount": round(float(inv["discount_amount"]), 2),
            "grand_total": round(float(inv["grand_total"]), 2),
            "payment_method": inv["payment_method"],
            "payment_status": inv["payment_status"],
            "notes": inv["notes"],
            "created_at": inv["created_at"],
            "items": [
                {
                    "product_sku": it["product_sku"],
                    "product_name": it["product_name"],
                    "unit_price": round(float(it["unit_price"]), 2),
                    "quantity": round(float(it["quantity"]), 2),
                    "tax_percent": round(float(it["tax_percent"]), 2),
                    "tax_amount": round(float(it["tax_amount"]), 2),
                    "discount_percent": round(float(it["discount_percent"]), 2),
                    "total_price": round(float(it["total_price"]), 2),
                }
                for it in items
            ]
        })

    conn.close()
    return payload


def mark_invoices_synced(synced_uuids):
    if not synced_uuids:
        return
    conn = get_connection()
    cur = conn.cursor()
    now_str = datetime.now().isoformat()
    placeholders = ",".join("?" for _ in synced_uuids)
    cur.execute(f"""
        UPDATE invoices
        SET sync_status = 'SYNCED', synced_at = ?
        WHERE invoice_uuid IN ({placeholders})
    """, [now_str] + list(synced_uuids))
    conn.commit()
    conn.close()


def get_all_invoices(status_filter=None, search=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM invoices"
    conditions = []
    params = []

    if status_filter and status_filter != "ALL":
        conditions.append("sync_status = ?")
        params.append(status_filter)

    if search:
        s = f"%{search.strip()}%"
        conditions.append("(invoice_number LIKE ? OR customer_name LIKE ? OR customer_phone LIKE ?)")
        params.extend([s, s, s])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC"
    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_invoice_with_items(invoice_uuid):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE invoice_uuid = ?", (invoice_uuid,))
    inv = cur.fetchone()
    if not inv:
        conn.close()
        return None
    invoice_dict = dict(inv)

    cur.execute("SELECT * FROM invoice_items WHERE invoice_uuid = ?", (invoice_uuid,))
    items = [dict(r) for r in cur.fetchall()]
    invoice_dict["items"] = items
    conn.close()
    return invoice_dict


def get_sync_statistics():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM invoices WHERE sync_status = 'PENDING'")
    pending = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM invoices WHERE sync_status = 'SYNCED'")
    synced = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM invoices")
    total = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM products")
    products_count = cur.fetchone()["cnt"]

    last_sync = get_setting("last_sync_time", "Never")
    server_url = get_setting("server_url", DEFAULT_SERVER_URL)

    conn.close()
    return {
        "pending_count": pending,
        "synced_count": synced,
        "total_invoices": total,
        "products_count": products_count,
        "last_sync_time": last_sync,
        "server_url": server_url,
    }

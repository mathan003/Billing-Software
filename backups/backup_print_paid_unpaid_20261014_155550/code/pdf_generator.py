import io
import base64
import unicodedata
from decimal import Decimal
import pymupdf
from .models import CompanySettings


def _get_image_bytes(base64_str_or_file):
    """Safely extracts image bytes from base64 string or file field"""
    if not base64_str_or_file:
        return None
    try:
        if isinstance(base64_str_or_file, str) and "base64," in base64_str_or_file:
            raw_b64 = base64_str_or_file.split("base64,")[1]
            return base64.b64decode(raw_b64)
        elif hasattr(base64_str_or_file, "read"):
            base64_str_or_file.seek(0)
            return base64_str_or_file.read()
        elif hasattr(base64_str_or_file, "path"):
            with open(base64_str_or_file.path, "rb") as f:
                return f.read()
    except Exception:
        return None
    return None


def _clean_str(text):
    """
    Cleans strings so standard PDF Type 1 fonts (Helvetica) render without errors.
    Transliterates or replaces unsupported Unicode glyphs to readable representation.
    """
    if not text:
        return ""
    text = str(text).strip()
    # Normalize unicode
    norm = unicodedata.normalize("NFKD", text)
    # Encode to ASCII replacing characters with '?' or best effort
    clean = ""
    for ch in norm:
        code = ord(ch)
        if 32 <= code <= 126:
            clean += ch
        elif ch in "\r\n\t":
            clean += ch
        else:
            clean += " "
    return " ".join(clean.split())


def _safe_insert_text(page, point, text, fontsize=9, fontname="helv", color=(0, 0, 0)):
    """Safely inserts text at a point catching any unencodable glyph exceptions"""
    try:
        page.insert_text(point, text, fontsize=fontsize, fontname=fontname, color=color)
    except Exception:
        page.insert_text(point, _clean_str(text), fontsize=fontsize, fontname=fontname, color=color)


def _safe_insert_textbox(page, rect, text, fontsize=9, fontname="helv", color=(0, 0, 0), align=pymupdf.TEXT_ALIGN_LEFT):
    """Safely inserts text into a bounding rect catching any unencodable glyph exceptions"""
    try:
        page.insert_textbox(rect, text, fontsize=fontsize, fontname=fontname, color=color, align=align)
    except Exception:
        page.insert_textbox(rect, _clean_str(text), fontsize=fontsize, fontname=fontname, color=color, align=align)


def generate_invoice_pdf(invoice, client_profile=None):
    """
    Generates a professional PDF receipt modeled after user's bill design specification:
    - Top center: 'Invoice' (underlined)
    - Left: Shop Name (bold), Address, Email, Phone
    - Right: Shop Logo
    - Blue header bar: 'Invoice Number: ...' (left) and 'Date: DD.MM.YYYY' (right)
    - Customer Info Box
    - Items Table: #, Description, Unit, Qty, Rate, Discount, Total
    - Totals: Subtotal, Tax/GST, Discount, Grand Total, Customer Paid, Pending Due
    - Bottom Box: Shop's Bank Account Details (Bank Name, Account Number, IFSC Code)
    """
    company = CompanySettings.get_settings()
    
    # Resolve shop details from client profile with fallback to company settings
    shop_name = company.company_name
    shop_address = company.address
    shop_phone = company.phone
    shop_email = company.email
    shop_logo_b64 = company.logo_base64
    bank_name = company.bank_name or "State Bank of India"
    account_number = company.account_number or "N/A"
    ifsc_code = company.ifsc_code or "N/A"
    shop_gst = getattr(company, "gst_number", "").strip()

    if not client_profile and invoice.client and hasattr(invoice.client, "profile"):
        client_profile = invoice.client.profile

    if client_profile:
        # If client has not provided GST, GST is empty and not mandatory
        shop_gst = getattr(client_profile, "gst_number", "").strip()
        if client_profile.shop_name:
            shop_name = client_profile.shop_name
        if client_profile.shop_address:
            shop_address = client_profile.shop_address
        if client_profile.phone:
            shop_phone = client_profile.phone
        if getattr(client_profile.user, "email", ""):
            shop_email = client_profile.user.email
        if client_profile.shop_logo_base64:
            shop_logo_b64 = client_profile.shop_logo_base64
        elif client_profile.avatar_base64:
            shop_logo_b64 = client_profile.avatar_base64
        if client_profile.bank_name:
            bank_name = client_profile.bank_name
        if client_profile.account_number:
            account_number = client_profile.account_number
        if client_profile.ifsc_code:
            ifsc_code = client_profile.ifsc_code

    item_count = invoice.items.count()

    # Dynamic Paper Dimensions (selectable width, auto-adjusting height, auto-expansion for longer bills)
    paper_width_mm = float(getattr(client_profile, "effective_paper_width_mm", 80.0) if client_profile else 80.0)
    paper_height_mode = getattr(client_profile, "print_paper_height_mode", "auto") if client_profile else "auto"
    paper_height_mm = float(getattr(client_profile, "effective_paper_height_mm", 210.0) or 210.0) if client_profile else 210.0
    auto_expand = getattr(client_profile, "print_auto_expand_height", True) if client_profile else True

    # 1 mm ≈ 2.83465 points
    page_width = max(160.0, paper_width_mm * 2.83465)

    # Dynamic layout scaling factors based on page width
    margin = 8.0 if page_width < 210 else (12.0 if page_width <= 340 else 20.0)
    avail_w = page_width - (2 * margin)

    # Font scale
    if page_width < 210:  # 58mm Thermal
        fs_title, fs_head, fs_body, fs_small, fs_total = 11.0, 10.0, 7.5, 6.5, 9.5
        row_h = 16.0
    elif page_width <= 340: # 80mm - 100mm Thermal
        fs_title, fs_head, fs_body, fs_small, fs_total = 13.0, 11.5, 8.5, 7.5, 11.0
        row_h = 19.0
    else: # A5 & A4
        fs_title, fs_head, fs_body, fs_small, fs_total = 15.0, 13.0, 9.5, 8.5, 12.5
        row_h = 22.0

    # Calculate exact needed height dynamically based on content (no unnecessary blank space)
    cust_addr = getattr(invoice, "customer_address", "") or (invoice.customer.address if invoice.customer else "")
    cust_box_h = (48 if page_width < 210 else 52) if cust_addr else (38 if page_width < 210 else 42)
    header_approx = 105 if page_width < 210 else (125 if page_width <= 340 else 145)
    table_approx = 22 + (item_count * row_h)
    totals_approx = 95 if page_width < 210 else 115
    bank_approx = 45 if page_width < 210 else 52
    footer_approx = 35

    exact_needed_height = header_approx + 24 + cust_box_h + table_approx + totals_approx + bank_approx + footer_approx + 20

    if paper_height_mode == "auto":
        page_height = max(260.0, exact_needed_height)
    else:
        fixed_pt = paper_height_mm * 2.83465
        if auto_expand:
            # Automatically support longer paper size when bill contains more items so nothing is cut off
            page_height = max(fixed_pt, exact_needed_height)
        else:
            page_height = fixed_pt

    doc = pymupdf.open()
    page = doc.new_page(width=page_width, height=page_height)

    # Color palette
    color_primary = (0.10, 0.20, 0.45)      # Deep Navy/Indigo
    color_dark = (0.12, 0.16, 0.22)         # Slate Dark
    color_muted = (0.42, 0.48, 0.58)        # Gray
    color_border = (0.82, 0.86, 0.92)       # Light border
    color_success = (0.08, 0.62, 0.30)      # Green
    color_danger = (0.84, 0.18, 0.18)       # Red
    color_bar_bg = (0.75, 0.86, 0.95)       # Soft Blue bar per user image (#bfe0f7)

    # 1. Background Watermark (overlay=False)
    wm_bytes = _get_image_bytes(company.watermark_base64 or shop_logo_b64)
    if wm_bytes:
        try:
            wm_dim = min(page_width * 0.6, 200)
            wm_rect = pymupdf.Rect((page_width - wm_dim) / 2, (page_height - wm_dim) / 2, (page_width + wm_dim) / 2, (page_height + wm_dim) / 2)
            page.insert_image(wm_rect, stream=wm_bytes, keep_proportion=True, overlay=False)
        except Exception:
            pass

    y = 10 if page_width < 210 else 12

    # 2. Centered Top Title: 'Invoice' (underlined)
    _safe_insert_textbox(
        page,
        pymupdf.Rect(0, y, page_width, y + 18),
        "Invoice",
        fontsize=fs_title,
        fontname="helv",
        color=color_dark,
        align=pymupdf.TEXT_ALIGN_CENTER
    )
    underline_w = 22 if page_width < 210 else 28
    page.draw_line(pymupdf.Point(page_width / 2 - underline_w, y + 16), pymupdf.Point(page_width / 2 + underline_w, y + 16), color=color_dark, width=1.1)
    y += (22 if page_width < 210 else 26)

    # 3. Header: Left = Shop Name & Address, Right = Shop Logo
    logo_bytes = _get_image_bytes(shop_logo_b64)
    logo_w = min(avail_w * 0.26, 75.0) if logo_bytes else 0
    if logo_bytes and logo_w > 0:
        try:
            logo_rect = pymupdf.Rect(page_width - margin - logo_w, y, page_width - margin, y + 55)
            page.insert_image(logo_rect, stream=logo_bytes, keep_proportion=True)
        except Exception:
            pass

    text_right_bound = page_width - margin - (logo_w + 6) if logo_bytes else page_width - margin

    # Shop Name (Bold Large)
    _safe_insert_textbox(
        page,
        pymupdf.Rect(margin, y, text_right_bound, y + 18),
        shop_name,
        fontsize=fs_head,
        fontname="helv",
        color=color_dark,
        align=pymupdf.TEXT_ALIGN_LEFT
    )
    y += (16 if page_width < 210 else 18)

    # Address Lines
    addr_lines = [line.strip() for line in shop_address.replace("\r\n", "\n").split("\n") if line.strip()]
    if not addr_lines:
        addr_lines = ["Commercial Complex, Main Road"]

    for al in addr_lines[:2]:
        _safe_insert_textbox(
            page,
            pymupdf.Rect(margin, y, text_right_bound, y + 12),
            al,
            fontsize=fs_small,
            fontname="helv",
            color=color_muted
        )
        y += (11 if page_width < 210 else 12)

    # Email & GSTIN
    if shop_email and page_width >= 210:
        _safe_insert_textbox(page, pymupdf.Rect(margin, y, text_right_bound, y + 12), f"Email  : {shop_email}", fontsize=fs_small, fontname="helv", color=color_dark)
        y += 12

    if shop_gst:
        _safe_insert_textbox(page, pymupdf.Rect(margin, y, text_right_bound, y + 12), f"GSTIN  : {shop_gst}", fontsize=fs_small, fontname="helv", color=color_dark)
        y += (11 if page_width < 210 else 12)

    if shop_phone:
        phone_box_w = min(avail_w * 0.65, 180.0)
        page.draw_rect(pymupdf.Rect(margin, y, margin + phone_box_w, y + 14), color=(0.10, 0.45, 0.20), width=0.8)
        _safe_insert_textbox(
            page,
            pymupdf.Rect(margin + 4, y + 1, margin + phone_box_w - 2, y + 13),
            f"Phone : {shop_phone}",
            fontsize=fs_small,
            fontname="helv",
            color=color_dark
        )
        y += 18
    else:
        y += 6

    # 4. Soft Blue Info Bar: Invoice Number on Left, Date on Right
    bar_h = 20 if page_width < 210 else 22
    bar_rect = pymupdf.Rect(margin, y, page_width - margin, y + bar_h)
    page.draw_rect(bar_rect, color=None, fill=color_bar_bg)

    inv_num_str = f"Invoice: {invoice.invoice_number}"
    date_str = f"Date: {invoice.created_at.strftime('%d.%m.%Y')}"

    _safe_insert_text(page, pymupdf.Point(margin + 5, y + 14), inv_num_str, fontsize=fs_body, fontname="helv", color=color_dark)
    _safe_insert_textbox(
        page,
        pymupdf.Rect(page_width - 120 - margin, y + 1, page_width - margin - 5, y + (bar_h - 1)),
        date_str,
        fontsize=fs_body,
        fontname="helv",
        color=color_dark,
        align=pymupdf.TEXT_ALIGN_RIGHT
    )
    y += (bar_h + 5)

    # 5. Customer Details Box
    cust_box = pymupdf.Rect(margin, y, page_width - margin, y + cust_box_h)
    page.draw_rect(cust_box, color=color_border, width=0.8, fill=(0.98, 0.99, 1.0))

    _safe_insert_text(page, pymupdf.Point(margin + 6, y + 13), f"Customer: {invoice.customer_name}", fontsize=fs_body, fontname="helv", color=color_dark)
    cust_phone_str = f"Phone: {invoice.customer_phone}" if invoice.customer_phone else "Phone: N/A"
    _safe_insert_text(page, pymupdf.Point(margin + 6, y + 25), cust_phone_str, fontsize=fs_small, fontname="helv", color=color_muted)
    if cust_addr:
        _safe_insert_text(page, pymupdf.Point(margin + 6, y + 37), f"Address: {cust_addr[:36]}", fontsize=fs_small, fontname="helv", color=color_muted)

    status_str = f"Status: {invoice.payment_status.upper()}"
    status_col = color_success if invoice.payment_status == "Paid" else color_danger
    _safe_insert_textbox(
        page,
        pymupdf.Rect(page_width - margin - 110, y + 4, page_width - margin - 6, y + 18),
        status_str,
        fontsize=fs_small,
        fontname="helv",
        color=status_col,
        align=pymupdf.TEXT_ALIGN_RIGHT
    )
    y += (cust_box_h + 5)

    # 6. Table Header & Proportional Column Widths
    th_h = 17 if page_width < 210 else 19
    th_rect = pymupdf.Rect(margin, y, page_width - margin, y + th_h)
    page.draw_rect(th_rect, color=None, fill=(0.91, 0.94, 0.98))

    if page_width < 210:  # 58mm compact (Item, Qty, Rate, Total)
        col_item_w = avail_w * 0.46
        col_qty_w = avail_w * 0.16
        col_rate_w = avail_w * 0.18
        c_item = margin + 3
        c_qty = margin + col_item_w
        c_rate = c_qty + col_qty_w
        c_total = c_rate + col_rate_w

        _safe_insert_text(page, pymupdf.Point(c_item, y + 12), "Item", fontsize=fs_small, fontname="helv", color=color_primary)
        _safe_insert_textbox(page, pymupdf.Rect(c_qty, y + 1, c_rate, y + th_h - 1), "Qty", fontsize=fs_small, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_CENTER)
        _safe_insert_textbox(page, pymupdf.Rect(c_rate, y + 1, c_total, y + th_h - 1), "Rate", fontsize=fs_small, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(page, pymupdf.Rect(c_total, y + 1, page_width - margin - 3, y + th_h - 1), "Total", fontsize=fs_small, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
    else:  # 80mm, A5, A4 (Item Description, Unit, Qty, Rate, Total)
        col_item_w = avail_w * 0.42
        col_unit_w = avail_w * 0.12
        col_qty_w = avail_w * 0.14
        col_rate_w = avail_w * 0.15
        c_item = margin + 4
        c_unit = margin + col_item_w
        c_qty = c_unit + col_unit_w
        c_rate = c_qty + col_qty_w
        c_total = c_rate + col_rate_w

        _safe_insert_text(page, pymupdf.Point(c_item, y + 13), "#  Item Description", fontsize=fs_body, fontname="helv", color=color_primary)
        _safe_insert_textbox(page, pymupdf.Rect(c_unit, y + 1, c_qty, y + th_h - 1), "Unit", fontsize=fs_body, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_CENTER)
        _safe_insert_textbox(page, pymupdf.Rect(c_qty, y + 1, c_rate, y + th_h - 1), "Qty", fontsize=fs_body, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_CENTER)
        _safe_insert_textbox(page, pymupdf.Rect(c_rate, y + 1, c_total, y + th_h - 1), "Rate", fontsize=fs_body, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(page, pymupdf.Rect(c_total, y + 1, page_width - margin - 4, y + th_h - 1), "Total", fontsize=fs_body, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)

    y += (th_h + 1)

    # 7. Table Rows
    for idx, item in enumerate(invoice.items.all()):
        row_bg = (0.98, 0.99, 1.0) if idx % 2 == 0 else (1.0, 1.0, 1.0)
        row_rect = pymupdf.Rect(margin, y, page_width - margin, y + row_h)
        page.draw_rect(row_rect, color=None, fill=row_bg)

        item_max_len = 16 if page_width < 210 else (28 if page_width <= 340 else 45)
        item_str = f"{idx + 1}. {_clean_str(item.product_name)[:item_max_len]}"

        if page_width < 210:
            _safe_insert_text(page, pymupdf.Point(c_item, y + row_h - 5), item_str, fontsize=fs_small, fontname="helv", color=color_dark)
            _safe_insert_textbox(page, pymupdf.Rect(c_qty, y + 1, c_rate, y + row_h - 1), f"{item.quantity:.2f}".rstrip("0").rstrip("."), fontsize=fs_small, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_CENTER)
            _safe_insert_textbox(page, pymupdf.Rect(c_rate, y + 1, c_total, y + row_h - 1), f"{item.unit_price:.2f}", fontsize=fs_small, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
            _safe_insert_textbox(page, pymupdf.Rect(c_total, y + 1, page_width - margin - 3, y + row_h - 1), f"{item.total_price:.2f}", fontsize=fs_small, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
        else:
            _safe_insert_text(page, pymupdf.Point(c_item, y + row_h - 6), item_str, fontsize=fs_body, fontname="helv", color=color_dark)
            _safe_insert_textbox(page, pymupdf.Rect(c_unit, y + 1, c_qty, y + row_h - 1), str(item.unit or "Pcs"), fontsize=fs_small, fontname="helv", color=color_muted, align=pymupdf.TEXT_ALIGN_CENTER)
            _safe_insert_textbox(page, pymupdf.Rect(c_qty, y + 1, c_rate, y + row_h - 1), f"{item.quantity:.2f}".rstrip("0").rstrip("."), fontsize=fs_body, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_CENTER)
            _safe_insert_textbox(page, pymupdf.Rect(c_rate, y + 1, c_total, y + row_h - 1), f"{item.unit_price:.2f}", fontsize=fs_body, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
            _safe_insert_textbox(page, pymupdf.Rect(c_total, y + 1, page_width - margin - 4, y + row_h - 1), f"{item.total_price:.2f}", fontsize=fs_body, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)

        y += row_h

    y += 4
    page.draw_line(pymupdf.Point(margin, y), pymupdf.Point(page_width - margin, y), color=color_border, width=0.8)
    y += 8

    # 8. Totals Breakdown Section
    box_total_left = max(margin, page_width - (avail_w * 0.65 if page_width >= 340 else avail_w * 0.90))
    box_total_right = page_width - margin - 4

    # Subtotal
    _safe_insert_text(page, pymupdf.Point(box_total_left, y + 9), "Subtotal:", fontsize=fs_small, fontname="helv", color=color_muted)
    _safe_insert_textbox(page, pymupdf.Rect(box_total_left + 60, y, box_total_right, y + 13), f"Rs. {invoice.subtotal:.2f}", fontsize=fs_body, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
    y += 14

    # Tax (if any)
    if invoice.tax_amount > Decimal("0.00") and shop_gst:
        _safe_insert_text(page, pymupdf.Point(box_total_left, y + 9), "Tax / GST:", fontsize=fs_small, fontname="helv", color=color_muted)
        _safe_insert_textbox(page, pymupdf.Rect(box_total_left + 60, y, box_total_right, y + 13), f"Rs. {invoice.tax_amount:.2f}", fontsize=fs_body, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
        y += 14

    # Discount (if any)
    if invoice.discount_amount > Decimal("0.00"):
        _safe_insert_text(page, pymupdf.Point(box_total_left, y + 9), "Discount:", fontsize=fs_small, fontname="helv", color=color_success)
        _safe_insert_textbox(page, pymupdf.Rect(box_total_left + 60, y, box_total_right, y + 13), f"- Rs. {invoice.discount_amount:.2f}", fontsize=fs_body, fontname="helv", color=color_success, align=pymupdf.TEXT_ALIGN_RIGHT)
        y += 14

    # Grand Total Highlight
    gt_h = 20 if page_width < 210 else 22
    gt_rect = pymupdf.Rect(box_total_left - 6, y, page_width - margin, y + gt_h)
    page.draw_rect(gt_rect, color=None, fill=(0.92, 0.95, 1.0))
    _safe_insert_text(page, pymupdf.Point(box_total_left, y + (gt_h - 6)), "GRAND TOTAL:", fontsize=fs_body, fontname="helv", color=color_primary)
    _safe_insert_textbox(page, pymupdf.Rect(box_total_left + 60, y + 2, box_total_right, y + gt_h - 2), f"Rs. {invoice.grand_total:.2f}", fontsize=fs_total, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
    y += (gt_h + 4)

    # Customer Paid
    _safe_insert_text(page, pymupdf.Point(box_total_left, y + 9), "Paid:", fontsize=fs_small, fontname="helv", color=color_success)
    _safe_insert_textbox(page, pymupdf.Rect(box_total_left + 60, y, box_total_right, y + 13), f"Rs. {invoice.paid_amount:.2f}", fontsize=fs_body, fontname="helv", color=color_success, align=pymupdf.TEXT_ALIGN_RIGHT)
    y += 14

    # Balance Due
    bal_col = color_danger if invoice.balance_amount > Decimal("0.00") else color_muted
    _safe_insert_text(page, pymupdf.Point(box_total_left, y + 9), "Balance Due:", fontsize=fs_small, fontname="helv", color=bal_col)
    _safe_insert_textbox(page, pymupdf.Rect(box_total_left + 60, y, box_total_right, y + 13), f"Rs. {invoice.balance_amount:.2f}", fontsize=fs_body, fontname="helv", color=bal_col, align=pymupdf.TEXT_ALIGN_RIGHT)
    y += 20

    # 9. Shop's Bank Account Details Box at bottom of bill (Per Requirement)
    bank_h = 42 if page_width < 210 else 46
    bank_box = pymupdf.Rect(margin, y, page_width - margin, y + bank_h)
    page.draw_rect(bank_box, color=(0.78, 0.84, 0.92), width=0.8, fill=(0.96, 0.98, 1.0))

    _safe_insert_text(page, pymupdf.Point(margin + 6, y + 12), "BANK ACCOUNT PAYMENT DETAILS:", fontsize=fs_small, fontname="helv", color=color_primary)
    _safe_insert_text(page, pymupdf.Point(margin + 6, y + 24), f"Bank: {bank_name}  |  A/C: {account_number}", fontsize=fs_small, fontname="helv", color=color_dark)
    _safe_insert_text(page, pymupdf.Point(margin + 6, y + 36), f"IFSC: {ifsc_code}  |  Pay Mode: {invoice.payment_method}", fontsize=fs_small, fontname="helv", color=color_muted)
    y += (bank_h + 8)

    # 10. Footer Sign-off
    page.draw_line(pymupdf.Point(margin + 15, y), pymupdf.Point(page_width - margin - 15, y), color=color_border, width=0.5)
    y += 10
    _safe_insert_textbox(
        page,
        pymupdf.Rect(0, y, page_width, y + 12),
        "Thank you for your business! Visit again.",
        fontsize=fs_small,
        fontname="helv",
        color=color_muted,
        align=pymupdf.TEXT_ALIGN_CENTER
    )
    y += 11
    inv_label = "Tax Invoice" if (invoice.tax_amount > Decimal("0.00") and shop_gst) else "Invoice"
    _safe_insert_textbox(
        page,
        pymupdf.Rect(0, y, page_width, y + 11),
        f"Computer Generated {inv_label} - {shop_name}",
        fontsize=fs_small - 0.5,
        fontname="helv",
        color=color_border,
        align=pymupdf.TEXT_ALIGN_CENTER
    )

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def generate_statement_pdf(customer, start_date, end_date, transactions, summary, client_profile=None):
    """
    Generates a formal Customer Account Statement PDF with safe UTF-8 character rendering.
    """
    company = CompanySettings.get_settings()
    shop_name = company.company_name
    shop_address = company.address
    shop_phone = company.phone
    shop_email = company.email
    shop_logo_b64 = company.logo_base64
    bank_name = company.bank_name or "State Bank of India"
    account_number = company.account_number or "N/A"
    ifsc_code = company.ifsc_code or "N/A"

    if client_profile:
        if client_profile.shop_name:
            shop_name = client_profile.shop_name
        if client_profile.shop_address:
            shop_address = client_profile.shop_address
        if client_profile.phone:
            shop_phone = client_profile.phone
        if getattr(client_profile.user, "email", ""):
            shop_email = client_profile.user.email
        if client_profile.shop_logo_base64:
            shop_logo_b64 = client_profile.shop_logo_base64
        if client_profile.bank_name:
            bank_name = client_profile.bank_name
        if client_profile.account_number:
            account_number = client_profile.account_number
        if client_profile.ifsc_code:
            ifsc_code = client_profile.ifsc_code

    page_width = 595
    row_count = len(transactions)
    page_height = max(842, 520 + (row_count * 24))
    doc = pymupdf.open()
    page = doc.new_page(width=page_width, height=page_height)

    color_primary = (0.13, 0.23, 0.55)
    color_dark = (0.12, 0.16, 0.24)
    color_muted = (0.45, 0.52, 0.62)
    color_border = (0.85, 0.88, 0.92)
    color_success = (0.08, 0.64, 0.32)
    color_danger = (0.86, 0.20, 0.20)

    # 1. Logo
    logo_bytes = _get_image_bytes(shop_logo_b64)
    has_logo = False
    if logo_bytes:
        try:
            logo_rect = pymupdf.Rect(25, 16, 95, 78)
            page.insert_image(logo_rect, stream=logo_bytes, keep_proportion=True)
            has_logo = True
        except Exception:
            pass

    header_left = 105 if has_logo else 25
    y = 20

    _safe_insert_textbox(page, pymupdf.Rect(header_left, y, page_width - 25, y + 24), shop_name.upper(), fontsize=15, fontname="helv", color=color_primary)
    y += 22
    _safe_insert_textbox(page, pymupdf.Rect(header_left, y, page_width - 25, y + 16), f"{shop_address} • Ph: {shop_phone}", fontsize=9, fontname="helv", color=color_muted)
    y += 16
    _safe_insert_textbox(page, pymupdf.Rect(header_left, y, page_width - 25, y + 16), f"Email: {shop_email}", fontsize=9, fontname="helv", color=color_muted)

    y = 105

    # Title & Period
    _safe_insert_textbox(page, pymupdf.Rect(25, y, page_width - 25, y + 20), "CUSTOMER ACCOUNT STATEMENT", fontsize=13, fontname="helv", color=color_dark)
    _safe_insert_textbox(page, pymupdf.Rect(25, y + 20, page_width - 25, y + 36), f"Statement Period: {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}", fontsize=9, fontname="helv", color=color_muted)

    y += 42

    # Customer Profile Box
    cust_box = pymupdf.Rect(25, y, page_width - 25, y + 50)
    page.draw_rect(cust_box, color=color_border, width=0.8, fill=(0.98, 0.99, 1.0))
    _safe_insert_text(page, pymupdf.Point(35, y + 20), f"Customer: {_clean_str(customer.name)}", fontsize=10.5, fontname="helv", color=color_dark)
    phone_s = f"Phone: {customer.phone}" if customer.phone else "Phone: N/A"
    _safe_insert_text(page, pymupdf.Point(35, y + 36), phone_s, fontsize=8.5, fontname="helv", color=color_muted)

    sum_x = page_width - 240
    _safe_insert_textbox(page, pymupdf.Rect(sum_x, y + 8, page_width - 35, y + 22), f"Total Billed: Rs. {summary['total_billed']:.2f}", fontsize=9, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
    _safe_insert_textbox(page, pymupdf.Rect(sum_x, y + 22, page_width - 35, y + 36), f"Total Paid: Rs. {summary['total_paid']:.2f}", fontsize=9, fontname="helv", color=color_success, align=pymupdf.TEXT_ALIGN_RIGHT)
    _safe_insert_textbox(page, pymupdf.Rect(sum_x, y + 36, page_width - 35, y + 48), f"Net Due: Rs. {summary['net_pending']:.2f}", fontsize=10, fontname="helv", color=color_danger, align=pymupdf.TEXT_ALIGN_RIGHT)

    y += 62

    # Table Header
    th_rect = pymupdf.Rect(25, y, page_width - 25, y + 22)
    page.draw_rect(th_rect, color=None, fill=(0.92, 0.94, 0.98))
    _safe_insert_text(page, pymupdf.Point(32, y + 15), "Date", fontsize=8.5, fontname="helv", color=color_primary)
    _safe_insert_text(page, pymupdf.Point(105, y + 15), "Transaction / Bill #", fontsize=8.5, fontname="helv", color=color_primary)
    _safe_insert_text(page, pymupdf.Point(235, y + 15), "Details / Mode", fontsize=8.5, fontname="helv", color=color_primary)
    _safe_insert_textbox(page, pymupdf.Rect(350, y + 2, 420, y + 20), "Billed (Debit)", fontsize=8.5, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
    _safe_insert_textbox(page, pymupdf.Rect(425, y + 2, 495, y + 20), "Paid (Credit)", fontsize=8.5, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
    _safe_insert_textbox(page, pymupdf.Rect(500, y + 2, page_width - 32, y + 20), "Balance", fontsize=8.5, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)

    y += 24

    # Table Rows
    for idx, tx in enumerate(transactions):
        row_bg = (0.99, 0.99, 1.0) if idx % 2 == 0 else (1.0, 1.0, 1.0)
        row_rect = pymupdf.Rect(25, y, page_width - 25, y + 20)
        page.draw_rect(row_rect, color=None, fill=row_bg)

        _safe_insert_text(page, pymupdf.Point(32, y + 14), tx["date"].strftime("%d-%m-%Y"), fontsize=8, fontname="helv", color=color_muted)
        _safe_insert_text(page, pymupdf.Point(105, y + 14), _clean_str(str(tx["ref"]))[:20], fontsize=8, fontname="helv", color=color_dark)
        _safe_insert_text(page, pymupdf.Point(235, y + 14), _clean_str(str(tx["details"]))[:22], fontsize=8, fontname="helv", color=color_muted)

        debit_str = f"Rs. {tx['debit']:.2f}" if tx["debit"] > 0 else "-"
        credit_str = f"Rs. {tx['credit']:.2f}" if tx["credit"] > 0 else "-"
        bal_str = f"Rs. {tx['balance']:.2f}"

        _safe_insert_textbox(page, pymupdf.Rect(350, y + 2, 420, y + 18), debit_str, fontsize=8, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(page, pymupdf.Rect(425, y + 2, 495, y + 18), credit_str, fontsize=8, fontname="helv", color=color_success, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(page, pymupdf.Rect(500, y + 2, page_width - 32, y + 18), bal_str, fontsize=8, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)

        y += 20

    y += 12
    page.draw_line(pymupdf.Point(25, y), pymupdf.Point(page_width - 25, y), color=color_border, width=1.0)
    y += 16

    # Bank Account Details Box at bottom of statement
    bank_box = pymupdf.Rect(25, y, page_width - 25, y + 42)
    page.draw_rect(bank_box, color=(0.80, 0.85, 0.92), width=0.8, fill=(0.96, 0.98, 1.0))
    _safe_insert_text(page, pymupdf.Point(35, y + 16), "PAYMENT BANK DETAILS:", fontsize=9, fontname="helv", color=color_primary)
    _safe_insert_text(page, pymupdf.Point(35, y + 32), f"Bank: {bank_name}  |  A/C: {account_number}  |  IFSC: {ifsc_code}", fontsize=8.5, fontname="helv", color=color_dark)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def generate_sales_report_pdf(title, period_label, summary, invoices, company=None, client_profile=None):
    """
    Generates multi-page Daily, Weekly, and Monthly Sales & Statement Reports.
    Includes Shop Branding, KPI cards, itemized invoices, and bank payment info.
    """
    if not company:
        company = CompanySettings.get_settings()

    shop_name = company.company_name
    shop_address = company.address
    shop_phone = company.phone
    shop_email = company.email
    shop_logo_b64 = company.logo_base64
    bank_name = company.bank_name or "State Bank of India"
    account_number = company.account_number or "N/A"
    ifsc_code = company.ifsc_code or "N/A"

    if client_profile:
        if client_profile.shop_name:
            shop_name = client_profile.shop_name
        if client_profile.shop_address:
            shop_address = client_profile.shop_address
        if client_profile.phone:
            shop_phone = client_profile.phone
        if getattr(client_profile.user, "email", ""):
            shop_email = client_profile.user.email
        if client_profile.shop_logo_base64:
            shop_logo_b64 = client_profile.shop_logo_base64
        if client_profile.bank_name:
            bank_name = client_profile.bank_name
        if client_profile.account_number:
            account_number = client_profile.account_number
        if client_profile.ifsc_code:
            ifsc_code = client_profile.ifsc_code

    page_width = 595
    page_height = 842  # Standard A4 height
    doc = pymupdf.open()

    color_primary = (0.13, 0.23, 0.55)
    color_dark = (0.12, 0.16, 0.24)
    color_muted = (0.45, 0.52, 0.62)
    color_border = (0.85, 0.88, 0.92)
    color_success = (0.08, 0.64, 0.32)
    color_danger = (0.86, 0.20, 0.20)

    def _create_new_page(page_num):
        p = doc.new_page(width=page_width, height=page_height)
        y_pos = 20

        # Header
        _safe_insert_textbox(p, pymupdf.Rect(25, y_pos, page_width - 25, y_pos + 22), shop_name.upper(), fontsize=14, fontname="helv", color=color_primary)
        y_pos += 20
        _safe_insert_textbox(p, pymupdf.Rect(25, y_pos, page_width - 25, y_pos + 14), f"{shop_address} • Ph: {shop_phone}", fontsize=8.5, fontname="helv", color=color_muted)
        y_pos += 18
        p.draw_line(pymupdf.Point(25, y_pos), pymupdf.Point(page_width - 25, y_pos), color=color_border, width=0.8)
        y_pos += 12

        if page_num == 1:
            # Title & Period
            _safe_insert_textbox(p, pymupdf.Rect(25, y_pos, page_width - 25, y_pos + 20), title.upper(), fontsize=13, fontname="helv", color=color_dark)
            _safe_insert_textbox(p, pymupdf.Rect(25, y_pos + 18, page_width - 25, y_pos + 34), period_label, fontsize=9, fontname="helv", color=color_muted)
            y_pos += 42

            # 4 KPI Summary Cards
            card_w = (page_width - 50 - 30) / 4
            cards = [
                ("Total Bills", f"{summary.get('count', 0)}", color_primary),
                ("Total Billed", f"Rs. {summary.get('total_sales', 0):.2f}", color_dark),
                ("Customer Paid", f"Rs. {summary.get('total_paid', 0):.2f}", color_success),
                ("Pending Due", f"Rs. {summary.get('total_pending', 0):.2f}", color_danger),
            ]
            for i, (c_label, c_val, c_color) in enumerate(cards):
                cx = 25 + i * (card_w + 10)
                p.draw_rect(pymupdf.Rect(cx, y_pos, cx + card_w, y_pos + 38), color=color_border, fill=(0.97, 0.98, 1.0), width=0.6)
                _safe_insert_text(p, pymupdf.Point(cx + 8, y_pos + 14), c_label, fontsize=7.5, fontname="helv", color=color_muted)
                _safe_insert_text(p, pymupdf.Point(cx + 8, y_pos + 30), c_val, fontsize=9.5, fontname="helv", color=c_color)
            y_pos += 48

        # Table Header
        th_rect = pymupdf.Rect(25, y_pos, page_width - 25, y_pos + 20)
        p.draw_rect(th_rect, color=None, fill=(0.92, 0.94, 0.98))
        _safe_insert_text(p, pymupdf.Point(30, y_pos + 14), "Date & Time", fontsize=8, fontname="helv", color=color_primary)
        _safe_insert_text(p, pymupdf.Point(105, y_pos + 14), "Invoice #", fontsize=8, fontname="helv", color=color_primary)
        _safe_insert_text(p, pymupdf.Point(180, y_pos + 14), "Customer Name", fontsize=8, fontname="helv", color=color_primary)
        _safe_insert_textbox(p, pymupdf.Rect(300, y_pos + 2, 360, y_pos + 18), "Total (Rs)", fontsize=8, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(p, pymupdf.Rect(365, y_pos + 2, 425, y_pos + 18), "Paid (Rs)", fontsize=8, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(p, pymupdf.Rect(430, y_pos + 2, 490, y_pos + 18), "Due (Rs)", fontsize=8, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(p, pymupdf.Rect(495, y_pos + 2, page_width - 30, y_pos + 18), "Status", fontsize=8, fontname="helv", color=color_primary, align=pymupdf.TEXT_ALIGN_CENTER)
        y_pos += 22

        return p, y_pos

    page_num = 1
    current_page, current_y = _create_new_page(page_num)

    for idx, inv in enumerate(invoices):
        if current_y > page_height - 65:
            page_num += 1
            current_page, current_y = _create_new_page(page_num)

        row_bg = (0.99, 0.99, 1.0) if idx % 2 == 0 else (1.0, 1.0, 1.0)
        current_page.draw_rect(pymupdf.Rect(25, current_y, page_width - 25, current_y + 18), color=None, fill=row_bg)

        dt_str = inv.created_at.strftime("%d-%m-%y %H:%M")
        _safe_insert_text(current_page, pymupdf.Point(30, current_y + 13), dt_str, fontsize=7.5, fontname="helv", color=color_muted)
        _safe_insert_text(current_page, pymupdf.Point(105, current_y + 13), inv.invoice_number, fontsize=7.5, fontname="helv", color=color_dark)
        _safe_insert_text(current_page, pymupdf.Point(180, current_y + 13), _clean_str(inv.customer_name)[:20], fontsize=7.5, fontname="helv", color=color_dark)

        _safe_insert_textbox(current_page, pymupdf.Rect(300, current_y + 1, 360, current_y + 17), f"{inv.grand_total:.2f}", fontsize=7.5, fontname="helv", color=color_dark, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(current_page, pymupdf.Rect(365, current_y + 1, 425, current_y + 17), f"{inv.paid_amount:.2f}", fontsize=7.5, fontname="helv", color=color_success, align=pymupdf.TEXT_ALIGN_RIGHT)
        _safe_insert_textbox(current_page, pymupdf.Rect(430, current_y + 1, 490, current_y + 17), f"{inv.balance_amount:.2f}", fontsize=7.5, fontname="helv", color=color_danger, align=pymupdf.TEXT_ALIGN_RIGHT)

        st_col = color_success if inv.payment_status == "Paid" else color_danger
        _safe_insert_textbox(current_page, pymupdf.Rect(495, current_y + 1, page_width - 30, current_y + 17), inv.payment_status, fontsize=7.5, fontname="helv", color=st_col, align=pymupdf.TEXT_ALIGN_CENTER)

        current_y += 18

    # Final Bank Details & Sign-off on last page
    if current_y > page_height - 80:
        page_num += 1
        current_page, current_y = _create_new_page(page_num)

    current_y += 12
    current_page.draw_line(pymupdf.Point(25, current_y), pymupdf.Point(page_width - 25, current_y), color=color_border, width=0.8)
    current_y += 10

    bank_box = pymupdf.Rect(25, current_y, page_width - 25, current_y + 36)
    current_page.draw_rect(bank_box, color=(0.80, 0.85, 0.92), width=0.6, fill=(0.96, 0.98, 1.0))
    _safe_insert_text(current_page, pymupdf.Point(35, current_y + 14), "OFFICIAL SETTLEMENT BANK ACCOUNT DETAILS:", fontsize=8, fontname="helv", color=color_primary)
    _safe_insert_text(current_page, pymupdf.Point(35, current_y + 28), f"Bank: {bank_name}   |   Account No: {account_number}   |   IFSC Code: {ifsc_code}", fontsize=8, fontname="helv", color=color_dark)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes

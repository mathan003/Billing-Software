# SmartBilling: Windows POS & Django Cloud System
**Offline/Online Multi-Client Billing System with MySQL & Render Cloud Deployment**

A modern business billing and inventory management solution featuring:
1. **Django Web & Mobile Cloud Backend**: Powered by MySQL, with a responsive mobile-first web app for staff on smartphones, plus a REST API for remote clients.
2. **Windows Desktop App**: Standalone Windows POS (`SmartBillingPOS.exe`) built with CustomTkinter & SQLite. Operates 100% offline with an automatic background sync engine that pushes local bills to the cloud as soon as internet connectivity is detected.
3. **Render Deployment Ready**: Preconfigured with `render.yaml`, `Procfile`, `build.sh`, WhiteNoise static file handling, and cloud database URL support.

---

## 🌟 What's New & Upgraded

### 1. Dashboard Page
- **4 Key Business Metrics**:
  - **Today's Total Sales**: Real-time sales generated today.
  - **Total Purchases / Buy**: Total supplier purchases / purchase cost.
  - **Customer Paid**: Total payment collections received.
  - **Pending Amounts**: Total customer due / outstanding balance.
- **Add & Alter Customer**:
  - One-click modal to add new customers (Name, Phone, Address, GST).
  - Built-in tab to view all customers with total billed, paid, and alter/edit customer records anytime.
- **Quick Bill (Print & Save)**:
  - Fast product selector (bilingual Tamil & English).
  - Select Unit Type: **KG**, **Pack / Bag**, **Box**, **Pcs**, **Liter**.
  - Enter quantity and customer details.
  - Choose Cash or Online Payment.
  - **Print & Save**: Generates bill, deducts stock, and displays printable receipt immediately.

### 2. Invoices & Receipts Page
- **View Receipt**: Clean receipt viewer with complete bill breakdown.
- **Download PDF Receipt**: Direct downloadable PDF format for customer sharing, printing, or archiving.
- **Record Payments & Balance Tracking**:
  - Displays **Grand Total**, **Paid Amount**, and **Remaining Balance Due** automatically calculated.
  - One-click **Pay** button: Cashier enters amount paid, chooses **Cash** or **Online Payment (UPI/Card)**, and the system instantly updates the remaining due balance.

### 3. Products Management Page (பொருட்கள்)
- **Add Product**:
  - **Tamil Name** (Required: e.g. அரிசி, பருப்பு, காபி தூள்).
  - **English Name** (Optional: e.g. Rice, Dal, Coffee).
  - **Unit Types**: Select from **KG**, **Pack / Bag**, **Box**, **Pcs**, **Liter**.
  - Selling Price, Cost/Buy Price, Tax %, and Stock Quantity.
- **Update Anytime**:
  - Alter product names, unit types, prices, and stock levels at any time.
- **Remove Product**:
  - Safely remove/deactivate products from active inventory with confirmation.

### 4. Interface & Navigation
- **Sync Monitor Removed**: The sync monitor is completely hidden from the main user interface.

### 5. Security & Session Control
- **Login Page Only**: No public registration page (`/login/`).
- **Single Login ID with Max 5 Concurrent Devices**:
  - The business login ID can be used by up to 5 persons/devices simultaneously.
  - If a 6th device logs in, the oldest session is automatically rotated out, strictly enforcing the 5-device limit.
- **Protected Internal Pages**:
  - All internal links (`/`, `/invoices/`, `/products/`, `/customers/`, etc.) require active authenticated sessions. Unauthorized requests are automatically redirected to the login page.

---

## 🚀 Quick Start (Local Windows Machine)

### 1. Database & Superuser Credentials
The system connects to your local MySQL service (`billing_db` with `root` / `root`).
- **Login Username**: `admin`
- **Login Password**: `admin123`

### 2. Start the Django Cloud Backend
Double-click `start_django.bat` or run:
```powershell
cd backend
python manage.py runserver 0.0.0.0:8000
```
- **Login URL**: [http://127.0.0.1:8000/login/](http://127.0.0.1:8000/login/)
- **Dashboard**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Mobile Access**: Open `http://<your-pc-ip>:8000` on any mobile phone connected to the same WiFi network!

### 3. Start the Windows Desktop App
Double-click:
```
SmartBillingPOS.exe
```
(Located directly in `D:\billing software\SmartBillingPOS.exe` and inside `SmartBillingPOS_Windows.zip`)

---

## ☁️ Deploying to Render Online Platform

1. **Push Code to GitHub / GitLab**:
   ```bash
   git init
   git add .
   git commit -m "SmartBilling POS release"
   git remote add origin https://github.com/your-username/smartbilling.git
   git push -u origin main
   ```
2. **Deploy on Render**:
   - Go to [dashboard.render.com](https://dashboard.render.com) ➡️ **New +** ➡️ **Blueprint** (uses `render.yaml`).
   - Or create a **Web Service** with:
     - **Build Command**: `./build.sh`
     - **Start Command**: `gunicorn billing_backend.wsgi:application --bind 0.0.0.0:$PORT`
   - Set environment variable `DATABASE_URL` with your cloud MySQL/Postgres database.
3. **Connect Windows App**:
   - In `SmartBillingPOS.exe` ➡️ **Settings** ➡️ Enter your live Render URL (e.g. `https://smartbilling-cloud.onrender.com`).

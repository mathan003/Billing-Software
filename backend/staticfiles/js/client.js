/**
 * Client Module JavaScript - MathanHub POS
 * Handles real-time dashboard live polling, stock checks, POS billing updates
 */

(function () {
    "use strict";

    // Auto-poll live sales metrics every 8 seconds
    function pollLiveClientMetrics() {
        fetch('/api/live-status/')
            .then(res => {
                if (!res.ok) throw new Error('Live status request failed');
                return res.json();
            })
            .then(data => {
                if (data.status === 'online') {
                    // Update today's sales badge/value if present
                    const salesEl = document.getElementById('todaySalesDisplay');
                    if (salesEl && data.today_sales !== undefined) {
                        salesEl.textContent = '₹' + parseFloat(data.today_sales).toLocaleString('en-IN', { minimumFractionDigits: 2 });
                    }

                    // Update today's bills count
                    const billsCountEl = document.getElementById('todayBillsCountDisplay');
                    if (billsCountEl && data.today_bills_count !== undefined) {
                        billsCountEl.textContent = data.today_bills_count;
                    }

                    // Update total pending due
                    const pendingEl = document.getElementById('totalPendingDisplay');
                    if (pendingEl && data.total_pending !== undefined) {
                        pendingEl.textContent = '₹' + parseFloat(data.total_pending).toLocaleString('en-IN', { minimumFractionDigits: 2 });
                    }
                }
            })
            .catch(err => {
                // Silently handle offline/transient glitches
            });
    }

    // Verify stock availability before adding to cart
    window.checkAvailableStock = function (availableQty, requestedQty, productName) {
        const avail = parseFloat(availableQty) || 0;
        const req = parseFloat(requestedQty) || 0;
        if (req > avail) {
            alert(`Insufficient Stock: '${productName}' has only ${avail} available, but ${req} was requested. Cannot exceed available stock.`);
            return false;
        }
        return true;
    };

    // Client device remote logout helper
    window.logoutRemoteDevice = function (sessionId) {
        if (!confirm("Are you sure you want to log out this device remotely?")) {
            return;
        }
        fetch(`/client/devices/logout/${sessionId}/`, {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRFToken": (document.querySelector('input[name="csrfmiddlewaretoken"]') || {}).value || ""
            }
        })
        .then(res => res.json())
        .then(data => {
            alert(data.message || "Device logged out successfully!");
            window.location.reload();
        })
        .catch(err => {
            console.error(err);
            window.location.reload();
        });
    };

    // Initialize auto-poller on page load if on client dashboard
    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('todaySalesDisplay') || document.getElementById('todayBillsCountDisplay')) {
            setInterval(pollLiveClientMetrics, 8000);
        }
    });
})();

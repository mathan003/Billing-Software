/**
 * Admin Module JavaScript - MathanHub POS
 * Handles Admin UI interactions, device session controls, and live updates
 */

(function () {
    "use strict";

    // Password visibility toggle
    window.togglePassVisibility = function (inputId, btn) {
        const input = document.getElementById(inputId);
        if (!input) return;
        if (input.type === "password") {
            input.type = "text";
            btn.innerHTML = '<i class="fa-solid fa-eye-slash text-secondary"></i>';
        } else {
            input.type = "password";
            btn.innerHTML = '<i class="fa-solid fa-eye text-secondary"></i>';
        }
    };
    window.togglePass = window.togglePassVisibility;

    // Image preview helper for client logo / photo
    window.previewNewClientImage = function (input) {
        if (input.files && input.files[0]) {
            const reader = new FileReader();
            reader.onload = function (e) {
                const preview = document.getElementById('clientImgPreview');
                const fallbackEl = document.getElementById('clientImgFallback');
                if (preview) {
                    preview.src = e.target.result;
                    preview.classList.remove('d-none');
                }
                if (fallbackEl) fallbackEl.classList.add('d-none');
            };
            reader.readAsDataURL(input.files[0]);
        }
    };

    // Filter table rows dynamically
    window.filterAdminTable = function (inputId, tableId) {
        const input = document.getElementById(inputId);
        const table = document.getElementById(tableId);
        if (!input || !table) return;

        const filter = input.value.toLowerCase().trim();
        const rows = table.getElementsByTagName('tr');

        for (let i = 1; i < rows.length; i++) {
            const row = rows[i];
            const text = row.textContent.toLowerCase();
            row.style.display = text.indexOf(filter) > -1 ? '' : 'none';
        }
    };

    // Diagnostics checker for Admin
    window.checkAdminDbHealth = function () {
        fetch('/api/health/')
            .then(res => res.json())
            .then(data => {
                const indicator = document.getElementById('adminDbStatusBadge');
                if (indicator) {
                    if (data.status === 'online') {
                        indicator.className = 'badge bg-success-subtle text-success border';
                        indicator.innerHTML = '<i class="fa-solid fa-circle-check me-1"></i> Cloud DB Connected';
                    } else {
                        indicator.className = 'badge bg-danger-subtle text-danger border';
                        indicator.innerHTML = '<i class="fa-solid fa-triangle-exclamation me-1"></i> DB Degraded';
                    }
                }
            })
            .catch(() => {
                const indicator = document.getElementById('adminDbStatusBadge');
                if (indicator) {
                    indicator.className = 'badge bg-warning-subtle text-warning border';
                    indicator.innerHTML = '<i class="fa-solid fa-wifi me-1"></i> Offline / Checking';
                }
            });
    };

    // Initialize listeners when DOM is loaded
    document.addEventListener('DOMContentLoaded', function () {
        const adminSearchInputs = document.querySelectorAll('[data-admin-filter]');
        adminSearchInputs.forEach(input => {
            const targetTable = input.getAttribute('data-admin-filter');
            input.addEventListener('input', function () {
                window.filterAdminTable(input.id, targetTable);
            });
        });
    });
})();

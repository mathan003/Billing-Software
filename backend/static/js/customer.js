/**
 * Customer Module JavaScript - MathanHub POS
 * Handles Customer directory search, ledger filtering, balance calculations
 */

(function () {
    "use strict";

    // Instant customer table search
    window.searchCustomers = function (searchInputId, tableId) {
        const input = document.getElementById(searchInputId);
        const table = document.getElementById(tableId);
        if (!input || !table) return;

        const query = input.value.toLowerCase().trim();
        const rows = table.querySelectorAll('tbody tr');

        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.indexOf(query) > -1 ? '' : 'none';
        });
    };

    // Filter customers showing only those with pending due balances
    window.filterPendingCustomersOnly = function (checkboxId, tableId) {
        const cb = document.getElementById(checkboxId);
        const table = document.getElementById(tableId);
        if (!cb || !table) return;

        const showOnlyPending = cb.checked;
        const rows = table.querySelectorAll('tbody tr');

        rows.forEach(row => {
            const hasDue = row.getAttribute('data-has-due') === 'true';
            if (showOnlyPending) {
                row.style.display = hasDue ? '' : 'none';
            } else {
                row.style.display = '';
            }
        });
    };

    // Auto-attach listeners on DOM load
    document.addEventListener('DOMContentLoaded', function () {
        const custSearch = document.getElementById('customerSearchInput');
        if (custSearch) {
            custSearch.addEventListener('input', function () {
                window.searchCustomers('customerSearchInput', 'customersTable');
            });
        }
    });
})();

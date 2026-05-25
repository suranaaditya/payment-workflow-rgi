# Force-reload the Payment Indent Approval Report print format from its JSON.
# Frappe does not auto-sync `custom_format: 1` print formats on migrate
# (to avoid clobbering edits made through the print format builder), so
# changes to the bundled JSON would otherwise never reach prod benches.

import frappe


def execute():
	frappe.reload_doc("payment_indent", "print_format", "payment_indent_approval_report", force=True)

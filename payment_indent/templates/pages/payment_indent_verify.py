# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	context.title = _("Payment Indent Verification")

	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = (
			"/login?redirect-to=" + frappe.local.request.full_path
		)
		raise frappe.Redirect

	name = (frappe.form_dict.get("id") or "").strip()
	token = (frappe.form_dict.get("token") or "").strip()

	context.payment_indent = None
	context.error = None

	if not name or not token:
		context.error = _("Missing document id or verification token in URL.")
		return

	if not frappe.db.exists("Payment Indent", name):
		context.error = _("No such Payment Indent on record.")
		return

	stored = frappe.db.get_value("Payment Indent", name, "verification_token") or ""
	if not stored or token != stored:
		context.error = _(
			"This document could not be verified. The token does not match the "
			"authoritative record on RGI ERP. Do not act on the printed document."
		)
		return

	if not frappe.has_permission("Payment Indent", "read", doc=name):
		frappe.throw(
			_("You do not have permission to view this Payment Indent."),
			frappe.PermissionError,
		)

	from payment_indent.payment_indent.doctype.payment_indent.payment_indent import (
		approver_display_label,
	)

	doc = frappe.get_doc("Payment Indent", name)
	context.payment_indent = doc
	context.approver_label = approver_display_label(doc.manager_approved_by)
	context.is_manager_approved = doc.workflow_state == "Manager Approved"
	return context

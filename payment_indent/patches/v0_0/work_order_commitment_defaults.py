# Switch Work Order outstanding to "Work Order Amount less Approved Indents".
#
# 1) New Payment Indent Settings fields get their defaults. Frappe does not
#    write defaults of newly added fields into an existing Single, so without
#    this the new settings would read as blank / 0 on a migrated site. A
#    Single that was never saved is left alone: it still loads every default
#    from the doctype, and storing any row would stop that for all fields.
# 2) Open indents (docstatus 0) keep a save-time snapshot of the Work Order
#    outstanding, which was the full Work Order value under the old logic.
#    Recompute those snapshots so the line dialog shows the new figure before
#    the next save. Submitted indents are left as they were approved.
#
# Idempotent: only fills settings that were never stored, and re-running the
# recompute gives the same numbers.

import frappe
from frappe.utils import flt


SETTINGS = "Payment Indent Settings"
BASIS_APPROVED = "Work Order Amount less Approved Indents"
BASIS_BALANCE_FIELD = "Work Order Balance Field"


def execute():
	if not frappe.db.sql("select 1 from `tabSingles` where doctype = %s limit 1", (SETTINGS,)):
		return

	stored = {
		row[0]
		for row in frappe.db.sql(
			"select field from `tabSingles` where doctype = %s and field in %s",
			(SETTINGS, ("work_order_outstanding_basis", "block_work_order_overcommitment")),
		)
	}
	if "work_order_outstanding_basis" not in stored:
		frappe.db.set_single_value(SETTINGS, "work_order_outstanding_basis", default_basis())
	if "block_work_order_overcommitment" not in stored:
		frappe.db.set_single_value(SETTINGS, "block_work_order_overcommitment", 1)

	refresh_open_work_order_snapshots()
	frappe.db.commit()


def default_basis():
	# A site that only ever mapped a balance field keeps using it.
	amount_field = frappe.db.get_single_value(SETTINGS, "work_order_amount_field")
	balance_field = frappe.db.get_single_value(SETTINGS, "work_order_balance_field")
	if not amount_field and balance_field:
		return BASIS_BALANCE_FIELD
	return BASIS_APPROVED


def refresh_open_work_order_snapshots():
	from payment_indent.payment_indent.doctype.payment_indent.payment_indent import (
		get_settings,
		get_work_order_commitment,
		get_work_order_outstanding_basis,
	)

	settings = get_settings()
	doctype = settings.work_order_doctype
	if not doctype or not frappe.db.exists("DocType", doctype):
		return
	needed = (
		settings.work_order_balance_field
		if get_work_order_outstanding_basis(settings) == BASIS_BALANCE_FIELD
		else settings.work_order_amount_field
	)
	if not needed or not frappe.get_meta(doctype).has_field(needed):
		return

	refs = frappe.db.sql(
		"""
		select r.name, r.parent, r.payment_indent_item, r.reference_name
		from `tabPayment Indent Item Reference` r
		join `tabPayment Indent` p on p.name = r.parent
		join `tabPayment Indent Item` i on i.name = r.payment_indent_item and i.parent = r.parent
		where r.parenttype = 'Payment Indent'
			and r.reference_doctype = %s
			and i.reference_type = 'Work Order'
			and p.docstatus = 0
		""",
		(doctype,),
		as_dict=True,
	)

	outstanding_by_item = {}
	for ref in refs:
		if not frappe.db.exists(doctype, ref.reference_name):
			continue
		commitment = get_work_order_commitment(
			ref.reference_name, exclude_payment_indent=ref.parent, settings=settings
		)
		frappe.db.set_value(
			"Payment Indent Item Reference",
			ref.name,
			"outstanding_amount",
			commitment.remaining,
			update_modified=False,
		)
		outstanding_by_item[ref.payment_indent_item] = outstanding_by_item.get(
			ref.payment_indent_item, 0
		) + flt(commitment.remaining)

	for item_name, outstanding in outstanding_by_item.items():
		frappe.db.set_value(
			"Payment Indent Item", item_name, "outstanding_amount", outstanding, update_modified=False
		)

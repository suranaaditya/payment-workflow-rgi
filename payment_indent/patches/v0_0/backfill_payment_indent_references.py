# Backfill / re-home the `Payment Indent Item Reference` rows.
# Two passes:
#   1) Move any existing entries that point at a Payment Indent Item as parent
#      (the old grandchild layout) to be siblings of items on the parent
#      Payment Indent. Sets payment_indent_item = original item name.
#   2) Seed a single-entry reference for any Payment Indent Item that still
#      has the legacy single reference_name but no matching reference row.
#
# Both passes are idempotent — running migrate twice should leave the data
# untouched on the second run.

import frappe
from frappe.utils import flt


def execute():
	# Pass 1: re-parent old grandchild rows to the parent Payment Indent
	misparented = frappe.db.sql(
		"""
		SELECT piir.name, piir.parent AS old_parent_item, pii.parent AS indent_name
		FROM `tabPayment Indent Item Reference` piir
		JOIN `tabPayment Indent Item` pii ON pii.name = piir.parent
		WHERE piir.parenttype = 'Payment Indent Item'
		""",
		as_dict=True,
	)
	for row in misparented:
		frappe.db.set_value(
			"Payment Indent Item Reference",
			row["name"],
			{
				"parent": row["indent_name"],
				"parenttype": "Payment Indent",
				"parentfield": "references",
				"payment_indent_item": row["old_parent_item"],
			},
		)

	# Pass 2: seed entries for items that still have a legacy reference_name only
	legacy_items = frappe.db.sql(
		"""
		SELECT pii.name AS item_name, pii.parent AS indent_name, pii.reference_type,
		       pii.reference_doctype, pii.reference_name, pii.reference_date,
		       pii.reference_amount, pii.outstanding_amount
		FROM `tabPayment Indent Item` pii
		WHERE COALESCE(pii.reference_name, '') != ''
		  AND COALESCE(pii.reference_type, '') NOT IN ('', 'No Reference')
		  AND NOT EXISTS (
		      SELECT 1 FROM `tabPayment Indent Item Reference` piir
		      WHERE piir.payment_indent_item = pii.name
		  )
		""",
		as_dict=True,
	)
	for row in legacy_items:
		ref_doctype = row["reference_doctype"]
		if not ref_doctype:
			ref_doctype = {
				"Purchase Invoice": "Purchase Invoice",
				"Purchase Order": "Purchase Order",
				"Purchase Receipt": "Purchase Receipt",
			}.get(row["reference_type"])
		if not ref_doctype:
			from payment_indent.payment_indent.doctype.payment_indent.payment_indent import get_settings

			ref_doctype = get_settings().work_order_doctype
		if not ref_doctype:
			continue

		frappe.get_doc(
			{
				"doctype": "Payment Indent Item Reference",
				"parenttype": "Payment Indent",
				"parentfield": "references",
				"parent": row["indent_name"],
				"payment_indent_item": row["item_name"],
				"reference_doctype": ref_doctype,
				"reference_name": row["reference_name"],
				"reference_date": row["reference_date"],
				"reference_amount": flt(row["reference_amount"]),
				"outstanding_amount": flt(row["outstanding_amount"]),
				"idx": 1,
			}
		).db_insert()

	frappe.db.commit()

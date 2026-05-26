# Backfill the new `references` child table on Payment Indent Item from the
# legacy single `reference_name` value. Idempotent: skips rows that already
# have a `references` entry.

import frappe
from frappe.utils import flt


def execute():
	rows = frappe.db.sql(
		"""
		SELECT name, parent, reference_type, reference_doctype, reference_name,
		       reference_date, reference_amount, outstanding_amount
		FROM `tabPayment Indent Item`
		WHERE COALESCE(reference_name, '') != ''
		  AND COALESCE(reference_type, '') NOT IN ('', 'No Reference')
		""",
		as_dict=True,
	)

	if not rows:
		return

	rows_by_parent = {}
	for row in rows:
		rows_by_parent.setdefault(row["name"], row)

	# Skip any row that already has at least one references child
	existing_with_refs = set(
		frappe.db.sql_list(
			"""SELECT DISTINCT parent FROM `tabPayment Indent Item Reference`
			   WHERE parent IN %(names)s""",
			{"names": tuple(rows_by_parent.keys())},
		)
	)

	pending = [r for n, r in rows_by_parent.items() if n not in existing_with_refs]
	if not pending:
		return

	for idx, row in enumerate(pending, start=1):
		ref_doctype = row["reference_doctype"]
		if not ref_doctype:
			ref_doctype = {
				"Purchase Invoice": "Purchase Invoice",
				"Purchase Order": "Purchase Order",
				"Purchase Receipt": "Purchase Receipt",
			}.get(row["reference_type"])
		if not ref_doctype:
			# Work Order (uses dynamic doctype) — best-effort lookup
			from payment_indent.payment_indent.doctype.payment_indent.payment_indent import get_settings

			ref_doctype = get_settings().work_order_doctype
		if not ref_doctype:
			continue

		frappe.get_doc(
			{
				"doctype": "Payment Indent Item Reference",
				"parenttype": "Payment Indent Item",
				"parentfield": "references",
				"parent": row["name"],
				"reference_doctype": ref_doctype,
				"reference_name": row["reference_name"],
				"reference_date": row["reference_date"],
				"reference_amount": flt(row["reference_amount"]),
				"outstanding_amount": flt(row["outstanding_amount"]),
				"idx": 1,
			}
		).db_insert()

	frappe.db.commit()

# Re-home any Payment Indent Item Reference rows that were originally
# inserted as grandchildren (parenttype = 'Payment Indent Item') so they
# become siblings of `items` on the parent Payment Indent. Frappe does
# not support grandchild tables in its in-memory tree; sibling tables
# do work natively. payment_indent_item field preserves the back-link.

import frappe


def execute():
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
	frappe.db.commit()

# Add a "Cancelled" state (doc_status=2) to the Payment Indent Workflow
# and to the Workflow State master, so cancelled indents land on a
# state the workflow engine recognises. Without this, Frappe treats
# the form as frozen by an unknown workflow state and refuses to show
# the Amend menu after cancellation.
# Idempotent: skips additions that already exist.

import frappe


def execute():
	# Workflow State master
	if not frappe.db.exists("Workflow State", "Cancelled"):
		frappe.get_doc(
			{
				"doctype": "Workflow State",
				"workflow_state_name": "Cancelled",
				"style": "Danger",
			}
		).insert(ignore_permissions=True)

	# Payment Indent Workflow needs the state row(s)
	if not frappe.db.exists("Workflow", "Payment Indent Workflow"):
		return
	wf = frappe.get_doc("Workflow", "Payment Indent Workflow")
	if any(s.state == "Cancelled" for s in wf.states):
		return
	for role in ("Payment Indent Admin", "System Manager"):
		wf.append(
			"states",
			{
				"state": "Cancelled",
				"doc_status": "2",
				"allow_edit": role,
			},
		)
	wf.save(ignore_permissions=True)
	frappe.db.commit()

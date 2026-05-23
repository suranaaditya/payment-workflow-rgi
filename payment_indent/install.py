import frappe


PAYMENT_INDENT_ROLES = (
    "Payment Creator",
    "Payment Approver",
    "Payment Indent Admin",
)


def after_install():
    create_payment_indent_roles()


def create_payment_indent_roles():
    for role_name in PAYMENT_INDENT_ROLES:
        if frappe.db.exists("Role", role_name):
            continue

        role = frappe.new_doc("Role")
        role.role_name = role_name
        role.desk_access = 1
        role.is_custom = 0
        role.insert(ignore_permissions=True)

    frappe.db.commit()

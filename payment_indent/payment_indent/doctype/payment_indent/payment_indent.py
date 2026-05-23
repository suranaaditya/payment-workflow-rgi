# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime, today


PAYMENT_CREATOR_ROLE = "Payment Creator"
PAYMENT_APPROVER_ROLE = "Payment Approver"
PAYMENT_INDENT_ROLES = {PAYMENT_APPROVER_ROLE, "Payment Indent Admin", "System Manager"}
APPROVAL_STATES = {"Pending Manager Approval", "Manager Approved"}


class PaymentIndent(Document):
    def validate(self):
        self.set_defaults()
        self.resolve_row_parties()
        self.refresh_party_balances()
        self.calculate_totals()
        self.validate_child_rows()
        self.validate_reference_documents()
        self.validate_no_reference_requirements()
        if self.workflow_state == "Manager Approved":
            self.validate_manager_approval()

    def before_workflow_action(self):
        action = getattr(self, "workflow_action", None) or frappe.form_dict.get("workflow_action")
        if action in {"Submit for Manager Approval", "Resubmit for Manager Approval", "Approve"}:
            self.refresh_party_balances()
            self.calculate_totals()
        if action == "Approve":
            self.validate_manager_approval()
            self.set_manager_approval_details()
        elif action == "Reject" and not self.manager_remarks:
            frappe.throw(_("Manager Remarks are required before rejecting a Payment Indent."))

    def before_submit(self):
        self.refresh_party_balances()
        self.calculate_totals()
        if self.workflow_state == "Manager Approved":
            self.validate_manager_approval()
            self.set_row_statuses()
            self.set_manager_approval_details()
        elif self.workflow_state == "Manager Rejected" and not self.manager_remarks:
            frappe.throw(_("Manager Remarks are required before rejecting a Payment Indent."))

    def on_submit(self):
        if self.workflow_state != "Manager Approved":
            return
        settings = get_settings()
        if settings.auto_generate_pdf_on_manager_approval:
            self.generate_manager_approval_pdf()

    def set_defaults(self):
        if not self.requested_by:
            self.requested_by = frappe.session.user
        if not self.posting_date:
            self.posting_date = today()
        if not self.declaration_note:
            self.declaration_note = get_settings().default_declaration_note
        if not self.workflow_state:
            self.workflow_state = "Draft"

    def resolve_row_parties(self):
        for row in self.items:
            if row.reference_type == "No Reference" and row.party_search and not row.party:
                party = resolve_party(row.party_search)
                row.party_type = party.party_type
                row.party = party.party
                row.party_name = party.party_name
                row.party_search = party.display
            elif row.party_type and row.party:
                party_name = get_party_name(row.party_type, row.party) or row.party
                row.party_name = party_name
                row.party_search = row.party_search or get_party_display(row.party_type, row.party, party_name)

    def calculate_totals(self):
        self.total_requested_amount = sum(flt(row.requested_amount) for row in self.items)
        self.total_approved_amount = sum(flt(row.approved_amount) for row in self.items)

    def validate_child_rows(self):
        if not self.items:
            frappe.throw(_("Payment Indent must have at least one item."))

        for row in self.items:
            row_id = row.idx or row.name
            if not user_can_edit_approval_fields() and self.workflow_state != "Manager Approved":
                row.approved_amount = 0
                row.manager_remarks = None
            if self.workflow_state != "Manager Approved":
                row.row_status = "Pending"
            if not row.company:
                frappe.throw(_("Company is mandatory in row {0}.").format(row_id))
            if not row.reference_type:
                frappe.throw(_("Reference Type is mandatory in row {0}.").format(row_id))
            if row.reference_type != "No Reference" and not row.party:
                frappe.throw(_("Party is mandatory in row {0}. Select a reference or party first.").format(row_id))
            if row.reference_type == "No Reference" and not (row.party or row.party_search):
                frappe.throw(_("Party is mandatory for No Reference payment in row {0}.").format(row_id))
            if flt(row.requested_amount) <= 0:
                frappe.throw(_("Requested Amount must be greater than zero in row {0}.").format(row_id))
            if flt(row.approved_amount) < 0:
                frappe.throw(_("Approved Amount cannot be negative in row {0}.").format(row_id))
            if flt(row.approved_amount) > flt(row.requested_amount):
                frappe.throw(_("Approved Amount cannot exceed Requested Amount in row {0}.").format(row_id))

    def validate_reference_documents(self):
        settings = get_settings()
        privileged = has_payment_indent_role()

        for row in self.items:
            self.normalize_reference_fields(row)
            if row.reference_type == "Purchase Invoice":
                self.validate_purchase_invoice(row, settings, privileged)
            elif row.reference_type == "Purchase Order":
                self.validate_purchase_order(row)
            elif row.reference_type == "Work Order":
                self.validate_work_order(row)

    def normalize_reference_fields(self, row):
        if row.reference_type == "Purchase Invoice":
            row.reference_doctype = "Purchase Invoice"
            row.purchase_invoice = row.reference_name
            row.purchase_order = None
            row.work_order_reference = None
        elif row.reference_type == "Purchase Order":
            row.reference_doctype = "Purchase Order"
            row.purchase_order = row.reference_name
            row.purchase_invoice = None
            row.work_order_reference = None
        elif row.reference_type == "Work Order":
            row.reference_doctype = row.work_order_doctype or get_settings().work_order_doctype
            row.work_order_doctype = row.reference_doctype
            row.work_order_reference = row.reference_name
            row.purchase_invoice = None
            row.purchase_order = None
        else:
            row.reference_doctype = None
            row.reference_name = None
            row.purchase_invoice = None
            row.purchase_order = None
            row.work_order_reference = None

    def validate_purchase_invoice(self, row, settings, privileged):
        if not row.reference_name:
            frappe.throw(_("Purchase Invoice is mandatory in row {0}.").format(row.idx))
        invoice = frappe.get_doc("Purchase Invoice", row.reference_name)
        if invoice.docstatus != 1:
            frappe.throw(_("Purchase Invoice {0} must be submitted.").format(invoice.name))
        if invoice.is_return:
            frappe.throw(_("Purchase Invoice {0} is a return invoice and cannot be used.").format(invoice.name))
        if row.party and row.party_type == "Supplier" and invoice.supplier != row.party:
            frappe.throw(_("Purchase Invoice {0} does not belong to selected supplier.").format(invoice.name))
        row.party_type = "Supplier"
        row.party = invoice.supplier
        row.party_name = invoice.supplier_name
        row.party_search = get_party_display("Supplier", invoice.supplier, invoice.supplier_name)
        if row.company and invoice.company != row.company:
            frappe.throw(_("Purchase Invoice {0} belongs to company {1}, not {2}.").format(invoice.name, invoice.company, row.company))
        row.reference_date = invoice.posting_date
        row.reference_amount = invoice.grand_total
        row.outstanding_amount = invoice.outstanding_amount
        if not row.description:
            row.description = invoice.get("remarks") or invoice.get("bill_no")
        if flt(row.requested_amount) > flt(invoice.outstanding_amount) and not (settings.allow_request_above_party_balance or privileged):
            frappe.throw(_("Requested Amount in row {0} cannot exceed Purchase Invoice outstanding amount.").format(row.idx))

    def validate_purchase_order(self, row):
        if not row.reference_name:
            frappe.throw(_("Purchase Order is mandatory in row {0}.").format(row.idx))
        order = frappe.get_doc("Purchase Order", row.reference_name)
        if order.docstatus != 1:
            frappe.throw(_("Purchase Order {0} must be submitted.").format(order.name))
        if order.status in {"Closed", "Cancelled"}:
            frappe.throw(_("Purchase Order {0} is {1}.").format(order.name, order.status))
        if row.party and row.party_type == "Supplier" and order.supplier != row.party:
            frappe.throw(_("Purchase Order {0} does not belong to selected supplier.").format(order.name))
        row.party_type = "Supplier"
        row.party = order.supplier
        row.party_name = order.supplier_name
        row.party_search = get_party_display("Supplier", order.supplier, order.supplier_name)
        if row.company and order.company != row.company:
            frappe.throw(_("Purchase Order {0} belongs to company {1}, not {2}.").format(order.name, order.company, row.company))
        row.reference_date = order.transaction_date
        row.reference_amount = order.grand_total
        row.outstanding_amount = max(flt(order.grand_total) - flt(order.get("advance_paid")), 0)
        if not row.description:
            row.description = order.get("status")

    def validate_work_order(self, row):
        if not row.reference_name:
            frappe.throw(_("Work Order Reference is mandatory in row {0}.").format(row.idx))
        details = get_work_order_details(row.reference_name)
        if details.get("company") and row.company and details.company != row.company:
            frappe.throw(_("Work Order {0} belongs to company {1}, not {2}.").format(row.reference_name, details.company, row.company))
        row.work_order_doctype = details.work_order_doctype
        if details.get("party"):
            row.party_type = details.get("party_type") or row.party_type
            row.party = details.get("party")
            row.party_name = details.get("party_name") or details.get("party")
            row.party_search = get_party_display(row.party_type, row.party, row.party_name) if row.party_type else row.party_name
        row.reference_date = details.get("reference_date")
        row.reference_amount = details.get("reference_amount")
        row.outstanding_amount = details.get("outstanding_amount")
        row.description = row.description or details.get("description")

    def validate_no_reference_requirements(self):
        settings = get_settings()
        if not settings.require_attachment_for_no_reference or self.workflow_state not in APPROVAL_STATES:
            return

        requires_attachment = any(
            row.reference_type == "No Reference"
            and flt(row.requested_amount) >= flt(settings.attachment_mandatory_above_amount)
            for row in self.items
        )
        if not requires_attachment:
            return
        if not self.name or not frappe.db.exists("File", {"attached_to_doctype": self.doctype, "attached_to_name": self.name, "is_folder": 0}):
            frappe.throw(_("Attachment is mandatory for No Reference payment above {0}.").format(settings.attachment_mandatory_above_amount))

    def refresh_party_balances(self):
        for row in self.items:
            if not row.company:
                continue
            balance = get_party_balance_for_values(row.company, row.party_type, row.party, self.posting_date, self.name)
            row.party_balance = balance["balance"]
            row.balance_type = balance["balance_type"]
            row.balance_as_on_date = balance["as_on_date"]
            row.balance_fetched_on = balance["fetched_on"]
            row.pending_payment_requests = get_pending_payment_requests(
                row.company, row.party_type, row.party, self.name, row.name
            )
            row.net_available_payable = (
                flt(row.party_balance) if row.balance_type == "Payable" else 0
            ) - flt(row.pending_payment_requests)

    def validate_manager_approval(self):
        settings = get_settings()
        if self.requested_by == frappe.session.user and not settings.allow_self_approval:
            frappe.throw(_("Self approval is disabled in Payment Indent Settings."))
        self.validate_approval_amounts()

    def validate_approval_amounts(self):
        has_approved_row = False
        for row in self.items:
            approved = flt(row.approved_amount)
            requested = flt(row.requested_amount)
            if approved < 0:
                frappe.throw(_("Approved Amount cannot be negative in row {0}.").format(row.idx))
            if approved > requested:
                frappe.throw(_("Approved Amount cannot exceed Requested Amount in row {0}.").format(row.idx))
            if approved > 0:
                has_approved_row = True
            if row.row_status in {"Rejected", "Hold", "Partially Approved"} and not (row.manager_remarks or self.manager_remarks):
                frappe.throw(_("Manager Remarks are mandatory for {0} row {1}.").format(row.row_status, row.idx))
            if approved > 0 and approved < requested and not (row.manager_remarks or self.manager_remarks):
                frappe.throw(_("Manager Remarks are mandatory for partially approved row {0}.").format(row.idx))
        if not has_approved_row:
            frappe.throw(_("At least one row must have Approved Amount greater than zero."))

    def set_row_statuses(self):
        for row in self.items:
            approved = flt(row.approved_amount)
            requested = flt(row.requested_amount)
            if approved == requested and requested > 0:
                row.row_status = "Approved"
            elif approved > 0 and approved < requested:
                row.row_status = "Partially Approved"
            elif approved == 0 and row.row_status not in {"Rejected", "Hold"}:
                row.row_status = "Rejected"

    def set_manager_approval_details(self):
        self.manager_approved_by = frappe.session.user
        self.manager_approved_on = now_datetime()

    def generate_manager_approval_pdf(self):
        file_url = generate_pdf(self.name, ignore_permissions=True)
        self.db_set("pdf_attachment", file_url, update_modified=False)
        self.db_set("pdf_generated", 1, update_modified=False)


def get_settings():
    return frappe.get_single("Payment Indent Settings")


def has_payment_indent_role():
    return bool(PAYMENT_INDENT_ROLES.intersection(set(frappe.get_roles())))


def user_can_edit_approval_fields():
    return bool(PAYMENT_INDENT_ROLES.intersection(set(frappe.get_roles())))


def require_payment_indent_access(permission="read"):
    if frappe.has_permission("Payment Indent", permission) or frappe.has_permission("Payment Indent", "create"):
        return
    frappe.throw(_("Not permitted for Payment Indent."), frappe.PermissionError)


DEFAULT_PARTY_SEARCH_TYPES = {
    "Supplier": ("supplier_name",),
    "Customer": ("customer_name",),
    "Employee": ("employee_name", "employee_number"),
}


def get_searchable_party_types():
    party_types = {}
    if frappe.db.table_exists("Party Type"):
        for row in frappe.get_all("Party Type", pluck="name"):
            if frappe.db.table_exists(row):
                party_types[row] = get_party_search_fields(row)

    for party_type, fields in DEFAULT_PARTY_SEARCH_TYPES.items():
        if frappe.db.table_exists(party_type):
            party_types.setdefault(party_type, fields)

    return party_types


def get_party_search_fields(party_type):
    default = DEFAULT_PARTY_SEARCH_TYPES.get(party_type)
    if default:
        return default

    meta = frappe.get_meta(party_type)
    candidates = []
    if meta.title_field:
        candidates.append(meta.title_field)
    for field in meta.fields:
        if field.fieldtype in {"Data", "Small Text"} and (
            field.fieldname in {"party_name", "customer_name", "supplier_name", "employee_name", "full_name"}
            or field.fieldname.endswith("_name")
        ):
            candidates.append(field.fieldname)

    fields = []
    for fieldname in candidates:
        if fieldname and fieldname not in fields and meta.has_field(fieldname):
            fields.append(fieldname)

    return tuple(fields[:2]) or ("name",)


def get_party_name(party_type, party):
    fieldname = {
        "Supplier": "supplier_name",
        "Customer": "customer_name",
        "Employee": "employee_name",
    }.get(party_type)
    if not fieldname or not party:
        return party
    return frappe.db.get_value(party_type, party, fieldname)


def get_party_display(party_type, party, party_name=None):
    label = party_name or party
    if party and party_name and party != party_name:
        label = f"{party_name} - {party}"
    return f"{label} ({party_type})" if party_type else label


def find_party_matches(txt, limit=10):
    txt = normalize_party_search_text(txt)

    matches = []
    like_txt = f"%{txt}%"
    for party_type, fields in get_searchable_party_types().items():
        if not frappe.db.table_exists(party_type):
            continue
        search_fields = ["name", *fields]
        conditions = " or ".join([f"`{field}` like %s" for field in search_fields]) if txt else "1 = 1"
        values = [like_txt] * len(search_fields) if txt else []
        rows = frappe.db.sql(
            f"""
            select name, {fields[0] if fields[0] != "name" else "name"} as party_name
            from `tab{party_type}`
            where {conditions}
            order by modified desc
            limit %s
            """,
            [*values, limit],
            as_dict=True,
        )
        for row in rows:
            matches.append(
                frappe._dict(
                    {
                        "party_type": party_type,
                        "party": row.name,
                        "party_name": row.party_name or row.name,
                        "display": get_party_display(party_type, row.name, row.party_name),
                    }
                )
            )
    return matches[:limit]


def normalize_party_search_text(txt):
    txt = (txt or "").strip()
    if not txt:
        return ""
    match = re.match(r"^(.*?)\s+\(([^()]+)\)\s*$", txt)
    if match:
        txt = match.group(1).strip()
    if " - " in txt:
        parts = [part.strip() for part in txt.split(" - ") if part.strip()]
        if parts:
            txt = parts[-1]
    return txt


def resolve_party(txt):
    original_txt = (txt or "").strip()
    normalized_txt = normalize_party_search_text(original_txt)
    display_match = re.match(r"^(.*?)\s+\(([^()]+)\)\s*$", original_txt)
    display_party_type = display_match.group(2).strip() if display_match else None
    display_label = display_match.group(1).strip() if display_match else original_txt

    matches = find_party_matches(original_txt, limit=8)
    if not matches:
        return frappe._dict(
            {
                "party_type": "Other",
                "party": normalized_txt or original_txt,
                "party_name": normalized_txt or original_txt,
                "display": get_party_display("Other", normalized_txt or original_txt, normalized_txt or original_txt),
            }
        )
    exact = [
        row
        for row in matches
        if (not display_party_type or row.party_type == display_party_type)
        and (
            row.party.lower() == normalized_txt.lower()
            or row.party_name.lower() == normalized_txt.lower()
            or row.display.lower() == original_txt.lower()
            or row.party_name.lower() == display_label.lower()
        )
    ]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    frappe.throw(
        _("Multiple parties matched '{0}'. Please type a more specific name. Matches: {1}").format(
            txt, ", ".join(row.display for row in matches)
        )
    )


@frappe.whitelist()
def search_parties(txt):
    require_payment_indent_access()
    return find_party_matches(txt)


@frappe.whitelist()
def get_party_autocomplete_options(doctype=None, txt="", searchfield=None, start=0, page_len=20, filters=None, **kwargs):
    require_payment_indent_access()
    options = []
    for row in find_party_matches(txt, limit=int(page_len or 20)):
        options.append(
            {
                "label": row.display,
                "value": row.display,
                "description": f"{row.party_type}: {row.party}",
            }
        )

    if (txt or "").strip():
        options.append(
            {
                "label": _("Use as Other Party: {0}").format((txt or "").strip()),
                "value": get_party_display("Other", (txt or "").strip(), (txt or "").strip()),
                "description": _("External / ad-hoc party"),
            }
        )

    return options


def get_party_balance_for_values(company, party_type, party, posting_date=None, payment_indent_name=None):
    posting_date = getdate(posting_date or today())
    fetched_on = now_datetime()

    if party_type == "Other" or not party:
        return {"balance": 0, "balance_type": "Unknown", "as_on_date": posting_date, "fetched_on": fetched_on}

    result = frappe.db.sql(
        """
        select coalesce(sum(debit), 0) as debit, coalesce(sum(credit), 0) as credit
        from `tabGL Entry`
        where company = %s
            and party_type = %s
            and party = %s
            and posting_date <= %s
            and is_cancelled = 0
        """,
        (company, party_type, party, posting_date),
        as_dict=True,
    )[0]
    debit = flt(result.debit)
    credit = flt(result.credit)

    balance_type = "Unknown"
    amount = 0
    if party_type in {"Supplier", "Employee"}:
        net = credit - debit
        if net > 0:
            balance_type = "Payable"
            amount = net
        elif net < 0:
            balance_type = "Advance"
            amount = abs(net)
        else:
            balance_type = "Nil"
    elif party_type == "Customer":
        net = debit - credit
        if net > 0:
            balance_type = "Receivable"
            amount = net
        elif net < 0:
            balance_type = "Advance"
            amount = abs(net)
        else:
            balance_type = "Nil"

    return {"balance": amount, "balance_type": balance_type, "as_on_date": posting_date, "fetched_on": fetched_on}


def get_pending_payment_requests(company, party_type, party, current_name=None, current_row_name=None):
    if not company or not party_type or not party or party_type == "Other":
        return 0

    if not frappe.db.table_exists("Payment Indent") or not frappe.db.table_exists("Payment Indent Item"):
        return 0

    value = frappe.db.sql(
        """
        select coalesce(sum(
            case
                when parent.workflow_state = 'Manager Approved' then coalesce(item.approved_amount, 0)
                else coalesce(item.requested_amount, 0)
            end
        ), 0)
        from `tabPayment Indent Item` item
        inner join `tabPayment Indent` parent on parent.name = item.parent
        where item.company = %s
            and item.party_type = %s
            and item.party = %s
            and parent.name != %s
            and parent.workflow_state in ('Pending Manager Approval', 'Manager Approved')
            and parent.docstatus < 2
        """,
        (company, party_type, party, current_name or ""),
    )[0][0]
    return flt(value)


@frappe.whitelist()
def get_party_balance(company, party_type, party, posting_date=None):
    require_payment_indent_access()
    return get_party_balance_for_values(company, party_type, party, posting_date)


@frappe.whitelist()
def refresh_party_balance_for_row(company, party_type, party, posting_date=None):
    require_payment_indent_access()
    return get_party_balance_for_values(company, party_type, party, posting_date)


@frappe.whitelist()
def refresh_all_party_balances(payment_indent_name):
    doc = frappe.get_doc("Payment Indent", payment_indent_name)
    doc.check_permission("write")
    doc.refresh_party_balances()
    doc.calculate_totals()

    if doc.docstatus == 1:
        for row in doc.items:
            row.db_update()
        doc.db_set("total_requested_amount", doc.total_requested_amount, update_modified=False)
        doc.db_set("total_approved_amount", doc.total_approved_amount, update_modified=False)
    else:
        doc.save()
    return {"total_requested_amount": doc.total_requested_amount, "total_approved_amount": doc.total_approved_amount}


@frappe.whitelist()
def save_approval_review(payment_indent_name, decisions, manager_remarks=None):
    if not user_can_edit_approval_fields():
        frappe.throw(_("Only Payment Approvers can review Payment Indents."), frappe.PermissionError)

    doc = frappe.get_doc("Payment Indent", payment_indent_name)
    doc.check_permission("write")

    if doc.workflow_state != "Pending Manager Approval":
        frappe.throw(_("Approval review is available only in Pending Manager Approval state."))

    if isinstance(decisions, str):
        decisions = frappe.parse_json(decisions)
    decisions = decisions or []
    rows_by_name = {row.name: row for row in doc.items}
    allowed_statuses = {"Pending", "Approved", "Partially Approved", "Rejected", "Hold"}

    for decision in decisions:
        row_name = decision.get("name")
        if row_name not in rows_by_name:
            frappe.throw(_("Invalid payment line selected for approval review."))

        row = rows_by_name[row_name]
        status = decision.get("row_status") or "Pending"
        approved_amount = flt(decision.get("approved_amount"))
        remarks = (decision.get("manager_remarks") or "").strip()
        requested_amount = flt(row.requested_amount)

        if status not in allowed_statuses:
            frappe.throw(_("Invalid approval status {0} in row {1}.").format(status, row.idx))
        if approved_amount < 0:
            frappe.throw(_("Approved Amount cannot be negative in row {0}.").format(row.idx))
        if approved_amount > requested_amount:
            frappe.throw(_("Approved Amount cannot exceed Requested Amount in row {0}.").format(row.idx))

        if status == "Approved":
            approved_amount = requested_amount
        elif status in {"Rejected", "Hold"}:
            approved_amount = 0
        elif status == "Partially Approved":
            if approved_amount <= 0 or approved_amount >= requested_amount:
                frappe.throw(_("Partial approval in row {0} needs an amount between zero and requested amount.").format(row.idx))

        if status in {"Rejected", "Hold", "Partially Approved"} and not remarks and not manager_remarks:
            frappe.throw(_("Remarks are mandatory for {0} row {1}.").format(status, row.idx))

        row.approved_amount = approved_amount
        row.row_status = status
        row.manager_remarks = remarks
        row.db_update()

    doc.total_approved_amount = sum(flt(row.approved_amount) for row in doc.items)
    doc.db_set("total_approved_amount", doc.total_approved_amount, update_modified=False)
    if manager_remarks is not None:
        doc.db_set("manager_remarks", manager_remarks, update_modified=False)

    return {
        "total_approved_amount": doc.total_approved_amount,
        "reviewed_rows": len(decisions),
    }


@frappe.whitelist()
def get_reference_details(reference_type, reference_name, party_type=None, party=None, company=None):
    require_payment_indent_access()
    if reference_type == "Purchase Invoice":
        invoice = frappe.get_doc("Purchase Invoice", reference_name)
        if invoice.docstatus != 1:
            frappe.throw(_("Purchase Invoice {0} must be submitted.").format(reference_name))
        if party and invoice.supplier != party:
            frappe.throw(_("Purchase Invoice {0} does not belong to selected party.").format(reference_name))
        if company and invoice.company != company:
            frappe.throw(_("Purchase Invoice {0} does not belong to selected company.").format(reference_name))
        return {
            "party_type": "Supplier",
            "party": invoice.supplier,
            "party_name": invoice.supplier_name,
            "party_search": get_party_display("Supplier", invoice.supplier, invoice.supplier_name),
            "company": invoice.company,
            "reference_date": invoice.posting_date,
            "reference_amount": invoice.grand_total,
            "outstanding_amount": invoice.outstanding_amount,
            "due_date": invoice.get("due_date"),
            "description": invoice.get("remarks") or invoice.get("bill_no"),
        }

    if reference_type == "Purchase Order":
        order = frappe.get_doc("Purchase Order", reference_name)
        if order.docstatus != 1:
            frappe.throw(_("Purchase Order {0} must be submitted.").format(reference_name))
        if order.status in {"Closed", "Cancelled"}:
            frappe.throw(_("Purchase Order {0} is {1}.").format(order.name, order.status))
        if party and order.supplier != party:
            frappe.throw(_("Purchase Order {0} does not belong to selected party.").format(reference_name))
        if company and order.company != company:
            frappe.throw(_("Purchase Order {0} does not belong to selected company.").format(reference_name))
        return {
            "party_type": "Supplier",
            "party": order.supplier,
            "party_name": order.supplier_name,
            "party_search": get_party_display("Supplier", order.supplier, order.supplier_name),
            "company": order.company,
            "reference_date": order.transaction_date,
            "reference_amount": order.grand_total,
            "outstanding_amount": max(flt(order.grand_total) - flt(order.get("advance_paid")), 0),
            "description": order.get("status"),
        }

    if reference_type == "Work Order":
        return get_work_order_details(reference_name, party=party, company=company)

    if reference_type == "No Reference":
        return {"description": "No Reference"}

    frappe.throw(_("Unsupported Reference Type: {0}").format(reference_type))


def get_work_order_details(reference_name, party=None, company=None):
    settings = get_settings()
    if not settings.work_order_doctype:
        frappe.throw(_("Work Order integration is not configured in Payment Indent Settings."))

    meta = frappe.get_meta(settings.work_order_doctype)
    field_map = {
        "party": settings.work_order_party_field,
        "company": settings.work_order_company_field,
        "reference_amount": settings.work_order_amount_field,
        "outstanding_amount": settings.work_order_balance_field,
        "reference_date": settings.work_order_date_field,
        "status": settings.work_order_status_field,
        "description": settings.work_order_description_field,
    }
    for label, fieldname in field_map.items():
        if fieldname and not meta.has_field(fieldname):
            frappe.throw(_("Configured Work Order {0} field '{1}' does not exist on {2}.").format(label, fieldname, settings.work_order_doctype))

    work_order = frappe.get_doc(settings.work_order_doctype, reference_name)
    if settings.work_order_status_field and settings.work_order_required_status:
        status = work_order.get(settings.work_order_status_field)
        if status != settings.work_order_required_status:
            frappe.throw(_("Work Order {0} must have status {1}.").format(reference_name, settings.work_order_required_status))

    configured_party = work_order.get(settings.work_order_party_field) if settings.work_order_party_field else None
    if party and configured_party and party != configured_party:
        frappe.throw(_("Work Order {0} does not belong to selected party.").format(reference_name))
    configured_company = work_order.get(settings.work_order_company_field) if settings.work_order_company_field else None
    if company and configured_company and company != configured_company:
        frappe.throw(_("Work Order {0} does not belong to selected company.").format(reference_name))

    resolved = None
    if configured_party:
        matches = find_party_matches(configured_party, limit=2)
        if len(matches) == 1:
            resolved = matches[0]

    return frappe._dict(
        {
            "work_order_doctype": settings.work_order_doctype,
            "party_type": resolved.party_type if resolved else None,
            "party": resolved.party if resolved else configured_party,
            "party_name": resolved.party_name if resolved else configured_party,
            "party_search": resolved.display if resolved else configured_party,
            "company": configured_company,
            "reference_amount": flt(work_order.get(settings.work_order_amount_field)) if settings.work_order_amount_field else 0,
            "outstanding_amount": flt(work_order.get(settings.work_order_balance_field)) if settings.work_order_balance_field else 0,
            "reference_date": work_order.get(settings.work_order_date_field) if settings.work_order_date_field else None,
            "status": work_order.get(settings.work_order_status_field) if settings.work_order_status_field else None,
            "description": work_order.get(settings.work_order_description_field) if settings.work_order_description_field else None,
        }
    )


@frappe.whitelist()
def generate_pdf(payment_indent_name, ignore_permissions=False):
    doc = frappe.get_doc("Payment Indent", payment_indent_name)
    if not ignore_permissions:
        doc.check_permission("print")
    if doc.workflow_state != "Manager Approved":
        frappe.throw(_("PDF can be generated only after Manager Approved."))

    pdf_content = frappe.get_print(
        "Payment Indent",
        doc.name,
        print_format="Payment Indent Approval Report",
        as_pdf=True,
        pdf_options={"orientation": "Landscape", "page-size": "A4"},
    )
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"{doc.name}-approval-report.pdf",
            "attached_to_doctype": doc.doctype,
            "attached_to_name": doc.name,
            "is_private": 1,
            "content": pdf_content,
        }
    )
    file_doc.insert(ignore_permissions=True)
    doc.db_set("pdf_attachment", file_doc.file_url, update_modified=False)
    doc.db_set("pdf_generated", 1, update_modified=False)
    return file_doc.file_url


def prevent_payment_indent_pdf_delete(doc, method=None):
    if doc.attached_to_doctype != "Payment Indent" or not doc.attached_to_name:
        return

    payment_indent = frappe.db.get_value(
        "Payment Indent",
        doc.attached_to_name,
        ["pdf_generated", "pdf_attachment"],
        as_dict=True,
    )
    if not payment_indent or not payment_indent.pdf_generated:
        return

    expected_file_name = f"{doc.attached_to_name}-approval-report.pdf"
    is_current_pdf = doc.file_url and doc.file_url == payment_indent.pdf_attachment
    is_generated_pdf = doc.file_name == expected_file_name or (
        doc.file_url and expected_file_name in doc.file_url
    )

    if is_current_pdf or is_generated_pdf:
        frappe.throw(
            _("Payment Indent approval PDFs cannot be deleted after generation."),
            frappe.PermissionError,
        )

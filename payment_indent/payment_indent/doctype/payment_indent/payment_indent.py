# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, now_datetime, today


PAYMENT_CREATOR_ROLE = "Payment Creator"
PAYMENT_APPROVER_ROLE = "Payment Approver"
PAYMENT_INDENT_ROLES = {PAYMENT_APPROVER_ROLE, "Payment Indent Admin", "System Manager"}
APPROVAL_STATES = {"Pending Manager Approval", "Manager Approved"}

# Approvers whose name is replaced by a role title on the approval PDF and the verify page.
# The DB record always keeps the real user (manager_approved_by) for audit.
EXECUTIVE_DIRECTOR_USER = "shraddha.surana@raisoni.net"
EXECUTIVE_DIRECTOR_TITLE = "Executive Director"


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
            self._verify_approval_otp()
            self.validate_manager_approval()
            self.set_manager_approval_details()
        elif action == "Reject" and not self.manager_remarks:
            frappe.throw(_("Manager Remarks are required before rejecting a Payment Indent."))

    def _verify_approval_otp(self):
        settings = get_settings()
        if not cint(getattr(settings, "require_approval_otp", 0)):
            return
        from payment_indent.payment_indent.doctype.payment_approval_authenticator.payment_approval_authenticator import (
            verify_approval_token,
        )

        token = frappe.form_dict.get("approval_otp") or frappe.form_dict.get("otp")
        verify_approval_token(frappe.session.user, token)

    def before_submit(self):
        self.refresh_party_balances()
        self.calculate_totals()
        if self.workflow_state == "Manager Approved":
            self._verify_approval_otp()
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

        # References live as a sibling child table on the parent Payment Indent;
        # each entry carries a `payment_indent_item` field pointing to its row.
        if not getattr(self, "references", None):
            self.references = []

        # Repair orphans: Frappe renames new item rows on insert, so any
        # reference whose payment_indent_item points at a stale temp name
        # needs to be rebound via payment_indent_item_idx (which is stable
        # within a save cycle).
        items_by_name = {it.name: it for it in self.items}
        items_by_idx = {int(it.idx or 0): it for it in self.items}
        for ref in self.references:
            current = ref.get("payment_indent_item") if isinstance(ref, dict) else ref.payment_indent_item
            if current and current in items_by_name:
                continue
            idx = ref.get("payment_indent_item_idx") if isinstance(ref, dict) else ref.payment_indent_item_idx
            target = items_by_idx.get(int(idx or 0))
            if target:
                if isinstance(ref, dict):
                    ref["payment_indent_item"] = target.name
                else:
                    ref.payment_indent_item = target.name

        refs_by_item = {}
        for ref in self.references:
            ref_data = ref if isinstance(ref, dict) else ref.as_dict()
            item_key = ref_data.get("payment_indent_item")
            if not item_key:
                continue
            refs_by_item.setdefault(item_key, []).append(ref)

        for row in self.items:
            self.normalize_reference_fields(row)
            if row.reference_type == "No Reference":
                # Make sure no stray references remain for this row
                refs_by_item.pop(row.name, None)
                continue
            row_refs = refs_by_item.get(row.name) or []
            self._validate_and_aggregate_references(row, row_refs, settings, privileged)
            # Sync any field updates back into self.references via the same objects
            # (we mutated them in place during aggregation).

    def normalize_reference_fields(self, row):
        if row.reference_type == "Purchase Invoice":
            row.reference_doctype = "Purchase Invoice"
            row.purchase_invoice = row.reference_name
            row.purchase_order = None
            row.purchase_receipt = None
            row.work_order_reference = None
        elif row.reference_type == "Purchase Order":
            row.reference_doctype = "Purchase Order"
            row.purchase_order = row.reference_name
            row.purchase_invoice = None
            row.purchase_receipt = None
            row.work_order_reference = None
        elif row.reference_type == "Purchase Receipt":
            row.reference_doctype = "Purchase Receipt"
            row.purchase_receipt = row.reference_name
            row.purchase_invoice = None
            row.purchase_order = None
            row.work_order_reference = None
        elif row.reference_type == "Work Order":
            row.reference_doctype = row.work_order_doctype or get_settings().work_order_doctype
            row.work_order_doctype = row.reference_doctype
            row.work_order_reference = row.reference_name
            row.purchase_invoice = None
            row.purchase_order = None
            row.purchase_receipt = None
        else:
            row.reference_doctype = None
            row.reference_name = None
            row.purchase_invoice = None
            row.purchase_order = None
            row.purchase_receipt = None
            row.work_order_reference = None

    def _seed_legacy_reference(self, row):
        """When a row has a legacy single reference_name but no entry in
        self.references, materialise one entry so the aggregator + display
        keep working. Uses self.append on the parent (allowed, since
        `references` is a direct child of Payment Indent)."""
        if not row.reference_name or not row.reference_doctype:
            return None
        existing = [
            ref for ref in self.references
            if (ref.get("payment_indent_item") if isinstance(ref, dict) else ref.payment_indent_item) == row.name
        ]
        if existing:
            return None
        new_entry = self.append(
            "references",
            {
                "payment_indent_item": row.name,
                "payment_indent_item_idx": row.idx,
                "reference_doctype": row.reference_doctype,
                "reference_name": row.reference_name,
            },
        )
        return new_entry

    def _validate_and_aggregate_references(self, row, row_refs, settings, privileged):
        if not row_refs:
            seeded = self._seed_legacy_reference(row)
            if seeded is not None:
                row_refs = [seeded]
        if not row_refs:
            frappe.throw(_("At least one reference is required in row {0}.").format(row.idx))

        # Normalise dict entries so attribute reads/writes work uniformly.
        for i, ref in enumerate(row_refs):
            if isinstance(ref, dict):
                row_refs[i] = frappe._dict(ref)

        seen = set()
        agg_reference_amount = 0
        agg_outstanding = 0
        latest_date = None
        first_details = None

        for ref in row_refs:
            details = _fetch_reference_details(
                row.reference_type,
                ref.reference_name,
                expected_company=row.company,
                row_idx=row.idx,
            )
            key = (details["reference_doctype"], details["reference_name"])
            if key in seen:
                frappe.throw(
                    _("Reference {0} is listed twice in row {1}.").format(
                        details["reference_name"], row.idx
                    )
                )
            seen.add(key)

            if first_details is None:
                first_details = details
            else:
                if (
                    details.get("party")
                    and first_details.get("party")
                    and details["party"] != first_details["party"]
                ):
                    frappe.throw(
                        _(
                            "All references in row {0} must belong to the same party. "
                            "Found {1} and {2}."
                        ).format(row.idx, first_details["party"], details["party"])
                    )

            ref.reference_doctype = details["reference_doctype"]
            ref.reference_date = details.get("reference_date")
            ref.reference_amount = flt(details.get("reference_amount"))
            ref.outstanding_amount = flt(details.get("outstanding_amount"))

            agg_reference_amount += flt(details.get("reference_amount"))
            agg_outstanding += flt(details.get("outstanding_amount"))
            ref_date = details.get("reference_date")
            if ref_date and (latest_date is None or ref_date > latest_date):
                latest_date = ref_date

        # Adopt the (now agreed) party from the first reference; for Work Order
        # some doctypes may not return a party — fall back to whatever the row had.
        if first_details and first_details.get("party"):
            row.party_type = first_details.get("party_type") or row.party_type
            row.party = first_details["party"]
            row.party_name = first_details.get("party_name") or first_details["party"]
            row.party_search = get_party_display(row.party_type, row.party, row.party_name)

        # Primary reference (kept on the row for back-compat: grid, old links, etc.)
        first = row_refs[0]
        row.reference_name = first.reference_name
        row.reference_date = latest_date
        row.reference_amount = agg_reference_amount
        row.outstanding_amount = agg_outstanding

        # Sync the per-type back-compat fields with the first ref
        if row.reference_type == "Purchase Invoice":
            row.purchase_invoice = first.reference_name
        elif row.reference_type == "Purchase Order":
            row.purchase_order = first.reference_name
        elif row.reference_type == "Purchase Receipt":
            row.purchase_receipt = first.reference_name
        elif row.reference_type == "Work Order":
            row.work_order_reference = first.reference_name

        if not row.description and first_details and first_details.get("description"):
            row.description = first_details["description"]

        if (
            flt(row.requested_amount) > agg_outstanding
            and not (settings.allow_request_above_party_balance or privileged)
        ):
            frappe.throw(
                _("Requested Amount in row {0} cannot exceed total outstanding {1}.").format(
                    row.idx, agg_outstanding
                )
            )

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
        if not self.verification_token:
            self.verification_token = frappe.generate_hash(length=20)

    def generate_manager_approval_pdf(self):
        file_url = generate_pdf(self.name, ignore_permissions=True)
        self.db_set("pdf_attachment", file_url, update_modified=False)
        self.db_set("pdf_generated", 1, update_modified=False)


def get_settings():
    return frappe.get_single("Payment Indent Settings")


def approver_display_label(user):
    """Jinja-callable. Returns "Executive Director" for the configured ED user,
    otherwise the user's full_name (or the user id as last resort)."""
    if not user:
        return ""
    if user == EXECUTIVE_DIRECTOR_USER:
        return EXECUTIVE_DIRECTOR_TITLE
    return frappe.db.get_value("User", user, "full_name") or user


def get_verification_base_url():
    settings = get_settings()
    base = (getattr(settings, "verification_base_url", None) or "").strip()
    return base or frappe.utils.get_url()


def get_verification_url(name, token):
    from urllib.parse import quote

    base = get_verification_base_url().rstrip("/")
    return f"{base}/payment-indent-verify?id={quote(name)}&token={quote(token)}"


def get_verification_qr(name):
    """Jinja-callable. Returns a base64 data URI for the verification QR image,
    or empty string if the doc has no token / qrcode lib is unavailable."""
    token = frappe.db.get_value("Payment Indent", name, "verification_token")
    if not token:
        return ""
    url = get_verification_url(name, token)
    try:
        import base64
        import io

        import qrcode

        buffer = io.BytesIO()
        qrcode.make(url).save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        frappe.log_error(title="payment_indent QR render failed")
        return ""


def rasterize_pdf(pdf_bytes, dpi=200):
    """Rasterize a PDF (bytes) into an image-only PDF using PyMuPDF.
    Returns rasterized bytes. Raises ImportError if pymupdf isn't installed."""
    import io

    import fitz  # PyMuPDF

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    try:
        for page in src:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            page_pdf = fitz.open()
            new_page = page_pdf.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=pix.tobytes("png"))
            out.insert_pdf(page_pdf)
            page_pdf.close()

        buffer = io.BytesIO()
        out.save(buffer, deflate=True, garbage=3)
        return buffer.getvalue()
    finally:
        src.close()
        out.close()


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

    if reference_type == "Purchase Receipt":
        receipt = frappe.get_doc("Purchase Receipt", reference_name)
        if receipt.docstatus != 1:
            frappe.throw(_("Purchase Receipt {0} must be submitted.").format(reference_name))
        if receipt.get("is_return"):
            frappe.throw(_("Purchase Receipt {0} is a return receipt and cannot be used.").format(reference_name))
        if party and receipt.supplier != party:
            frappe.throw(_("Purchase Receipt {0} does not belong to selected party.").format(reference_name))
        if company and receipt.company != company:
            frappe.throw(_("Purchase Receipt {0} does not belong to selected company.").format(reference_name))
        # ERPNext doesn't carry an outstanding_amount on receipts; treat the
        # unbilled portion (grand_total - billed_amount) as still payable.
        unbilled = max(flt(receipt.grand_total) - flt(receipt.get("billed_amount")), 0)
        return {
            "party_type": "Supplier",
            "party": receipt.supplier,
            "party_name": receipt.supplier_name,
            "party_search": get_party_display("Supplier", receipt.supplier, receipt.supplier_name),
            "company": receipt.company,
            "reference_date": receipt.posting_date,
            "reference_amount": receipt.grand_total,
            "outstanding_amount": unbilled or flt(receipt.grand_total),
            "description": receipt.get("remarks") or receipt.get("supplier_delivery_note"),
        }

    if reference_type == "Work Order":
        return get_work_order_details(reference_name, party=party, company=company)

    if reference_type == "No Reference":
        return {"description": "No Reference"}

    frappe.throw(_("Unsupported Reference Type: {0}").format(reference_type))


def _fetch_reference_details(reference_type, reference_name, expected_company=None, row_idx=None):
    """Thin wrapper used by validate_reference_documents: calls get_reference_details
    and appends the canonical reference_doctype / reference_name back into the dict
    so the caller can persist them to the child entry without re-deriving."""
    if not reference_name:
        frappe.throw(_("Reference is mandatory in row {0}.").format(row_idx or ""))
    details = get_reference_details(
        reference_type=reference_type,
        reference_name=reference_name,
        company=expected_company,
    ) or {}
    if reference_type == "Purchase Invoice":
        ref_doctype = "Purchase Invoice"
    elif reference_type == "Purchase Order":
        ref_doctype = "Purchase Order"
    elif reference_type == "Purchase Receipt":
        ref_doctype = "Purchase Receipt"
    elif reference_type == "Work Order":
        ref_doctype = get_settings().work_order_doctype
    else:
        ref_doctype = ""
    details["reference_doctype"] = ref_doctype
    details["reference_name"] = reference_name
    return details


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

    settings = get_settings()
    if cint(getattr(settings, "rasterize_approval_pdf", 0)):
        try:
            pdf_content = rasterize_pdf(pdf_content, dpi=200)
        except ImportError:
            frappe.log_error(
                title="payment_indent rasterize skipped",
                message="PyMuPDF (pymupdf) not installed. Install it or turn off rasterize_approval_pdf.",
            )
        except Exception:
            frappe.log_error(title="payment_indent rasterize failed")

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

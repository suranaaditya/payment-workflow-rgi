// Copyright (c) 2026, Dux Digitech and contributors
// For license information, please see license.txt

const PAYMENT_INDENT_METHOD =
    "payment_indent.payment_indent.doctype.payment_indent.payment_indent";

const AUTHENTICATOR_METHOD =
    "payment_indent.payment_indent.doctype.payment_approval_authenticator.payment_approval_authenticator";

const REFERENCE_TYPE_OPTIONS = [
    "",
    "Purchase Invoice",
    "Purchase Order",
    "Purchase Receipt",
    "Work Order",
    "No Reference",
].join("\n");

const PAYMENT_TERMS_OPTIONS = [
    "",
    "100% advance",
    "50% advance",
    "30% advance",
    "20% advance",
    "10% advance",
    "Material Received",
    "Against Proforma Invoice",
    "Others",
].join("\n");

function recalculate_totals(frm) {
    const rows = frm.doc.items || [];
    const total_requested = rows.reduce((sum, row) => sum + flt(row.requested_amount), 0);
    const total_approved = rows.reduce((sum, row) => sum + flt(row.approved_amount), 0);
    frm.set_value("total_requested_amount", total_requested);
    frm.set_value("total_approved_amount", total_approved);
}

function is_blank_payment_row(row) {
    return (
        !row.party_search &&
        !row.party &&
        !row.reference_name &&
        !row.purchase_invoice &&
        !row.purchase_order &&
        !row.work_order_reference &&
        !row.payment_terms &&
        !row.payment_terms_other &&
        !row.payment_remark &&
        !flt(row.requested_amount) &&
        !flt(row.approved_amount)
    );
}

function clear_default_company_from_blank_rows(frm) {
    if (!frm.is_new()) return;

    let changed = false;
    (frm.doc.items || []).forEach((row) => {
        if (row.company && is_blank_payment_row(row)) {
            frappe.model.set_value(row.doctype, row.name, "company", "");
            changed = true;
        }
    });

    if (changed && frm.fields_dict.items) {
        frm.fields_dict.items.grid.refresh();
    }
}

function schedule_clear_default_company(frm) {
    clear_default_company_from_blank_rows(frm);
    setTimeout(() => clear_default_company_from_blank_rows(frm), 150);
}

function is_payment_line_editable(frm) {
    return ["Draft", "Returned for Correction", "Pending Manager Approval", ""].includes(frm.doc.workflow_state || "");
}

function can_edit_approval_fields(frm) {
    const roles = frappe.user_roles || [];
    const is_approver = roles.includes("Payment Approver") || roles.includes("Payment Indent Admin") || roles.includes("System Manager");
    return is_approver && frm.doc.workflow_state === "Pending Manager Approval";
}

function hide_native_workflow_approve(frm) {
    const labels = new Set(["Approve", __("Approve")]);
    const hide_in = ($scope) => {
        if (!$scope || !$scope.length) return;
        $scope.find("a, button").each(function () {
            const text = ($(this).text() || "").trim();
            if (labels.has(text)) {
                $(this).closest("li, .menu-item").hide();
            }
        });
    };
    const hide = () => {
        const $wrapper = frm.page && frm.page.wrapper ? $(frm.page.wrapper) : null;
        hide_in($wrapper && $wrapper.find(".actions-btn-group"));
        hide_in($wrapper && $wrapper.find(".menu-btn-group"));
        hide_in(frm.page && frm.page.menu);
    };
    hide();
    setTimeout(hide, 200);
    setTimeout(hide, 600);
    setTimeout(hide, 1500);
}

function get_approval_enrollment_status() {
    return frappe.call({
        method: `${AUTHENTICATOR_METHOD}.get_enrollment_status`,
    }).then((r) => r.message || {});
}

function open_approval_enrollment_dialog() {
    return new Promise((resolve) => {
        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            resolve(value);
        };
        const dialog = new frappe.ui.Dialog({
            title: __("Set up Approval Authenticator"),
            fields: [
                {
                    fieldname: "intro",
                    fieldtype: "HTML",
                    options: `<div class="text-muted small">${__(
                        "Scan the QR code with Google Authenticator (or any TOTP app), then enter the 6-digit code shown in the app to confirm."
                    )}</div>`,
                },
                { fieldname: "qr_html", fieldtype: "HTML" },
                {
                    fieldname: "token",
                    label: __("6-digit Code"),
                    fieldtype: "Data",
                    reqd: 1,
                    description: __("Enter the code currently shown in your authenticator app."),
                },
            ],
            primary_action_label: __("Confirm and Enable"),
            primary_action(values) {
                const token = (values.token || "").trim();
                if (!/^\d{6}$/.test(token)) {
                    frappe.msgprint(__("Enter the 6-digit numeric code."));
                    return;
                }
                frappe.call({
                    method: `${AUTHENTICATOR_METHOD}.confirm_enrollment`,
                    args: { token },
                    freeze: true,
                    freeze_message: __("Verifying authenticator"),
                }).then((r) => {
                    if (r.message && r.message.enrolled) {
                        frappe.show_alert({
                            message: __("Approval Authenticator enabled."),
                            indicator: "green",
                        });
                        finish(true);
                        dialog.hide();
                    }
                });
            },
        });
        dialog.onhide = () => finish(false);

        frappe.call({
            method: `${AUTHENTICATOR_METHOD}.start_enrollment`,
            freeze: true,
            freeze_message: __("Preparing authenticator"),
        }).then((r) => {
            const data = r.message || {};
            const qr_wrapper = dialog.fields_dict.qr_html.$wrapper;
            const qr_img = data.qr_image
                ? `<img src="${data.qr_image}" alt="QR code" style="max-width: 220px; display: block; margin: 8px auto;" />`
                : `<div class="text-muted small">${__("QR image unavailable. Use the manual key below.")}</div>`;
            qr_wrapper.html(`
                <div style="text-align: center;">${qr_img}</div>
                <div class="text-muted small" style="text-align: center; margin-bottom: 8px;">
                    ${__("Issuer")}: <strong>${frappe.utils.escape_html(data.issuer || "")}</strong> &middot;
                    ${__("Account")}: <strong>${frappe.utils.escape_html(data.account || "")}</strong>
                </div>
                <div class="text-muted small" style="text-align: center;">
                    ${__("Manual key")}: <code>${frappe.utils.escape_html(data.manual_key || "")}</code>
                </div>
            `);
            dialog.show();
        });
    });
}

function add_approval_security_buttons(frm) {
    const roles = frappe.user_roles || [];
    const can_manage =
        roles.includes("Payment Approver") ||
        roles.includes("Payment Indent Admin") ||
        roles.includes("System Manager");
    if (!can_manage) return;

    get_approval_enrollment_status().then((status) => {
        if (!status.enrolled) {
            frm.add_custom_button(
                __("Set up Approval Authenticator"),
                () => open_approval_enrollment_dialog(),
                __("Approval Security")
            );
            return;
        }
        frm.add_custom_button(
            __("Reset Authenticator"),
            () => {
                frappe.confirm(
                    __("This removes your current authenticator. You'll need to scan a new QR to approve again. Continue?"),
                    () => {
                        frappe.call({
                            method: `${AUTHENTICATOR_METHOD}.reset_my_authenticator`,
                            freeze: true,
                            freeze_message: __("Resetting authenticator"),
                        }).then(() => {
                            frappe.show_alert({
                                message: __("Authenticator reset. Scan the new QR to re-enroll."),
                                indicator: "orange",
                            });
                            open_approval_enrollment_dialog();
                        });
                    }
                );
            },
            __("Approval Security")
        );
    });
}

function ensure_approval_enrollment() {
    return get_approval_enrollment_status().then((status) => {
        if (status.enrolled) return status;
        frappe.msgprint({
            title: __("Authenticator Required"),
            message: __("Set up your Approval Authenticator before approving."),
            indicator: "orange",
        });
        return open_approval_enrollment_dialog().then((enrolled) => enrolled ? get_approval_enrollment_status() : null);
    });
}

function prompt_for_approval_otp(context) {
    context = context || {};
    return new Promise((resolve) => {
        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            resolve(value);
        };
        const dialog = new frappe.ui.Dialog({
            title: __("Approval Code Required"),
            fields: [
                { fieldname: "header_html", fieldtype: "HTML" },
                {
                    fieldname: "approval_otp",
                    label: __("6-digit Code"),
                    fieldtype: "Data",
                    reqd: 1,
                },
            ],
            primary_action_label: __("Approve"),
            primary_action(values) {
                submit_token(values.approval_otp);
            },
        });
        dialog.onhide = () => finish(null);

        const submit_token = (raw) => {
            const token = (raw || "").trim();
            if (!/^\d{6}$/.test(token)) {
                dialog.$wrapper.find(".otp-input-error").show();
                return;
            }
            finish(token);
            dialog.hide();
        };

        dialog.show();
        dialog.$wrapper.find(".modal-dialog").css("max-width", "440px");

        const account = frappe.utils.escape_html(context.account_label || "");
        const issuer = frappe.utils.escape_html(context.issuer || "RGI Payment Indent");
        dialog.fields_dict.header_html.$wrapper.html(`
            <style>
                .otp-prompt-header { text-align: center; padding: 4px 0 14px; }
                .otp-prompt-icon { display: inline-flex; align-items: center; justify-content: center;
                    width: 44px; height: 44px; border-radius: 999px;
                    background: var(--gray-100); color: var(--text-color);
                    margin-bottom: 10px; }
                .otp-prompt-icon svg { width: 22px; height: 22px; }
                .otp-prompt-subtitle { color: var(--text-muted); font-size: 12px; margin-top: 2px; }
                .otp-prompt-subtitle strong { color: var(--text-color); }
                .otp-input-wrapper input { text-align: center; font-size: 22px;
                    letter-spacing: 10px; font-family: var(--font-stack-monospace, monospace);
                    height: 52px; padding-left: 10px; }
                .otp-input-error { color: var(--red-500); font-size: 12px; margin-top: 6px; display: none; }
            </style>
            <div class="otp-prompt-header">
                <div class="otp-prompt-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                        stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="11" width="18" height="11" rx="2" />
                        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                    </svg>
                </div>
                <div>${__("Enter the 6-digit code from your authenticator app to approve this Payment Indent.")}</div>
                ${account ? `<div class="otp-prompt-subtitle">${__("Approving as")} <strong>${account}</strong> &middot; ${issuer}</div>` : ""}
            </div>
        `);

        const $input = dialog.fields_dict.approval_otp.$input;
        const $error = dialog.$wrapper.find(".otp-input-error");
        if ($input && $input.length) {
            $input
                .attr("inputmode", "numeric")
                .attr("autocomplete", "one-time-code")
                .attr("maxlength", "6")
                .attr("pattern", "[0-9]{6}");
            $input.closest(".frappe-control").addClass("otp-input-wrapper");
            $input.after('<div class="otp-input-error">' + __("Enter the 6-digit numeric code.") + "</div>");
            $input.on("input", function () {
                const digits = (this.value || "").replace(/\D/g, "").slice(0, 6);
                if (digits !== this.value) this.value = digits;
                $error.hide();
                if (digits.length === 6) submit_token(digits);
            });
            setTimeout(() => $input.focus(), 60);
        }
    });
}

function set_approval_field_access(frm) {
    const editable = can_edit_approval_fields(frm);
    frm.set_df_property("manager", "hidden", 1);
    frm.fields_dict.items.grid.update_docfield_property("approved_amount", "read_only", editable ? 0 : 1);
    frm.fields_dict.items.grid.update_docfield_property("manager_remarks", "read_only", editable ? 0 : 1);
    frm.fields_dict.items.grid.update_docfield_property("row_status", "read_only", 1);
    frm.fields_dict.items.grid.refresh();
}

function configure_payment_line_grid(frm) {
    const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
    if (!grid) return;

    grid.wrapper.find(".grid-add-row, .grid-add-multiple-rows").hide();
}

function clear_row_balance(cdt, cdn) {
    frappe.model.set_value(cdt, cdn, "party_balance", 0);
    frappe.model.set_value(cdt, cdn, "balance_type", "");
    frappe.model.set_value(cdt, cdn, "balance_as_on_date", "");
    frappe.model.set_value(cdt, cdn, "balance_fetched_on", "");
    frappe.model.set_value(cdt, cdn, "pending_payment_requests", 0);
    frappe.model.set_value(cdt, cdn, "net_available_payable", 0);
}

function refresh_row_balance(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (!row.company || !row.party_type || !row.party) {
        clear_row_balance(cdt, cdn);
        return;
    }

    frappe.call({
        method: `${PAYMENT_INDENT_METHOD}.refresh_party_balance_for_row`,
        args: {
            company: row.company,
            party_type: row.party_type,
            party: row.party,
            posting_date: frm.doc.posting_date,
        },
        callback(r) {
            if (!r.message) return;
            frappe.model.set_value(cdt, cdn, "party_balance", r.message.balance);
            frappe.model.set_value(cdt, cdn, "balance_type", r.message.balance_type);
            frappe.model.set_value(cdt, cdn, "balance_as_on_date", r.message.as_on_date);
            frappe.model.set_value(cdt, cdn, "balance_fetched_on", r.message.fetched_on);
        },
    });
}

function refresh_all_row_balances(frm) {
    if (!frm.doc.name || frm.is_new()) {
        (frm.doc.items || []).forEach((row) => refresh_row_balance(frm, row.doctype, row.name));
        return;
    }

    frappe.call({
        method: `${PAYMENT_INDENT_METHOD}.refresh_all_party_balances`,
        args: { payment_indent_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Refreshing party balances"),
        callback() {
            frm.reload_doc();
        },
    });
}

function apply_reference_details(frm, cdt, cdn, details) {
    if (!details) return;
    const setters = {
        reference_date: details.reference_date,
        reference_amount: details.reference_amount,
        outstanding_amount: details.outstanding_amount,
        description: details.description,
        party_type: details.party_type,
        party: details.party,
        party_name: details.party_name,
        party_search: details.party_search || details.party_name,
    };

    Object.keys(setters).forEach((fieldname) => {
        if (setters[fieldname] !== undefined && setters[fieldname] !== null) {
            frappe.model.set_value(cdt, cdn, fieldname, setters[fieldname]);
        }
    });

    refresh_row_balance(frm, cdt, cdn);
}

function set_row_party(frm, row, party) {
    if (!row || !party) return;
    frappe.model.set_value(row.doctype, row.name, "party_type", party.party_type);
    frappe.model.set_value(row.doctype, row.name, "party", party.party);
    frappe.model.set_value(row.doctype, row.name, "party_name", party.party_name || party.party);
    frappe.model.set_value(row.doctype, row.name, "party_search", party.display);
    refresh_row_balance(frm, row.doctype, row.name);
}

function bind_party_picker_to_grid(frm) {
    const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
    if (!grid || grid._payment_indent_party_picker_bound) return;
    grid._payment_indent_party_picker_bound = true;

    grid.wrapper.on("focusin click", '[data-fieldname="party_search"] input', function () {
        const $row = $(this).closest(".grid-row");
        const row_name = $row.attr("data-name");
        if (row_name) {
            open_party_picker(frm, row_name, this.value);
        }
    });
}

function fetch_reference_details(frm, cdt, cdn, reference_type, reference_name) {
    if (!reference_type || !reference_name) return;
    const row = locals[cdt][cdn];

    frappe.call({
        method: `${PAYMENT_INDENT_METHOD}.get_reference_details`,
        args: {
            reference_type,
            reference_name,
            party_type: row.party_type,
            party: row.party,
            company: row.company,
        },
        callback(r) {
            apply_reference_details(frm, cdt, cdn, r.message);
        },
    });
}

function set_reference_doctype(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.reference_type === "Purchase Invoice") {
        frappe.model.set_value(cdt, cdn, "reference_doctype", "Purchase Invoice");
    } else if (row.reference_type === "Purchase Order") {
        frappe.model.set_value(cdt, cdn, "reference_doctype", "Purchase Order");
    } else if (row.reference_type === "Work Order") {
        frappe.db.get_single_value("Payment Indent Settings", "work_order_doctype").then((doctype) => {
            if (doctype) {
                frappe.model.set_value(cdt, cdn, "work_order_doctype", doctype);
                frappe.model.set_value(cdt, cdn, "reference_doctype", doctype);
            } else {
                frappe.model.set_value(cdt, cdn, "reference_doctype", "");
                frappe.msgprint(__("Work Order integration is not configured in Payment Indent Settings."));
            }
        });
    } else {
        frappe.model.set_value(cdt, cdn, "reference_doctype", "");
    }
}

function html_escape(value) {
    return $("<div>").text(value || "").html();
}

function payment_line_reference(row) {
    return row.reference_name || row.purchase_invoice || row.purchase_order || row.work_order_reference || "";
}

function payment_line_reference_doctype(row) {
    if (row.reference_doctype) return row.reference_doctype;
    if (row.reference_type === "Purchase Invoice") return "Purchase Invoice";
    if (row.reference_type === "Purchase Order") return "Purchase Order";
    if (row.reference_type === "Work Order") return row.work_order_doctype;
    return "";
}

function payment_line_reference_html(row) {
    const reference = payment_line_reference(row);
    if (!reference) {
        return `<div class="text-muted small">${__("No Reference")}</div>`;
    }

    const reference_doctype = payment_line_reference_doctype(row);
    if (!reference_doctype) {
        return `<div class="text-muted small">${html_escape(reference)}</div>`;
    }

    return `
        <a href="/app/${encodeURIComponent(frappe.router.slug(reference_doctype))}/${encodeURIComponent(reference)}"
            class="payment-reference-link"
            target="_blank"
            rel="noopener noreferrer"
            title="${__("Open reference document")}">
            ${html_escape(reference)}
        </a>
    `;
}

function open_approval_workbench(frm) {
    if (!can_edit_approval_fields(frm)) {
        frappe.msgprint(__("Only Payment Approvers can review this Payment Indent."));
        return;
    }

    const dialog = new frappe.ui.Dialog({
        title: __("Review & Approve Payments"),
        fields: [
            {
                fieldname: "approval_review",
                fieldtype: "HTML",
            },
            {
                fieldname: "manager_remarks",
                label: __("Overall Manager Remarks"),
                fieldtype: "Small Text",
                default: frm.doc.manager_remarks || "",
            },
        ],
        primary_action_label: __("Approve Payment Indent"),
        primary_action() {
            save_review(true);
        },
        secondary_action_label: __("Save Review"),
        secondary_action() {
            save_review(false);
        },
    });

    const rows = frm.doc.items || [];
    const wrapper = () => dialog.fields_dict.approval_review.$wrapper;

    const money = (value) => format_currency(flt(value), frm.doc.currency || undefined);

    const decision_for_row = (row) => {
        const approved = flt(row.approved_amount);
        const requested = flt(row.requested_amount);
        if (row.row_status && row.row_status !== "Pending") return row.row_status;
        if (approved === requested && requested > 0) return "Approved";
        if (approved > 0 && approved < requested) return "Partially Approved";
        return "Pending";
    };

    const render = () => {
        const table_rows = rows
            .map((row, index) => {
                const decision = decision_for_row(row);
                return `
                    <tr data-row-name="${html_escape(row.name)}">
                        <td class="small text-muted">${index + 1}</td>
                        <td>
                            <div><strong>${html_escape(row.party_search || row.party_name || row.party)}</strong></div>
                            <div class="text-muted small">${html_escape(row.company)}</div>
                        </td>
                        <td>
                            <div>${html_escape(row.reference_type)}</div>
                            <div class="small">${payment_line_reference_html(row)}</div>
                            <div class="text-muted small">${html_escape(row.payment_terms === "Others" ? row.payment_terms_other : row.payment_terms)}</div>
                        </td>
                        <td class="text-right">
                            <div>${money(row.requested_amount)}</div>
                            <div class="text-muted small">Outstanding ${money(row.outstanding_amount)}</div>
                        </td>
                        <td class="text-right">
                            <div>${money(row.party_balance)}</div>
                            <div class="text-muted small">${html_escape(row.balance_type)}</div>
                        </td>
                        <td>
                            <input class="form-control input-sm approved-amount text-right" type="number" step="0.01" min="0" value="${flt(row.approved_amount) || ""}">
                            <div class="btn-group btn-group-xs mt-2 quick-actions" role="group">
                                <button type="button" class="btn btn-default" data-action="full">${__("Full")}</button>
                                <button type="button" class="btn btn-default" data-action="partial">${__("Partial")}</button>
                                <button type="button" class="btn btn-default" data-action="reject">${__("Reject")}</button>
                                <button type="button" class="btn btn-default" data-action="hold">${__("Hold")}</button>
                            </div>
                        </td>
                        <td>
                            <select class="form-control input-sm row-status">
                                ${["Pending", "Approved", "Partially Approved", "Rejected", "Hold"]
                                    .map((status) => `<option value="${status}" ${status === decision ? "selected" : ""}>${__(status)}</option>`)
                                    .join("")}
                            </select>
                        </td>
                        <td>
                            <textarea class="form-control input-sm manager-remarks" rows="2">${html_escape(row.manager_remarks)}</textarea>
                        </td>
                    </tr>
                `;
            })
            .join("");

        wrapper().html(`
            <style>
                .payment-approval-workbench { max-height: 58vh; overflow: auto; border: 1px solid var(--border-color); border-radius: 6px; }
                .payment-approval-workbench table { margin: 0; min-width: 1120px; }
                .payment-approval-workbench th { position: sticky; top: 0; background: var(--fg-color); z-index: 1; }
                .payment-approval-summary { display: flex; gap: 16px; flex-wrap: wrap; margin: 0 0 10px; }
                .payment-approval-summary .metric { padding: 8px 10px; background: var(--control-bg); border-radius: 6px; min-width: 150px; }
                .payment-approval-summary .metric strong { display: block; font-size: 15px; }
                .payment-approval-workbench tr.needs-remarks td { background: #fff6e5; }
                .payment-approval-workbench tr.invalid-decision td { background: #fff0f0; }
                .payment-reference-link { font-weight: 600; text-decoration: underline; }
            </style>
            <div class="payment-approval-summary"></div>
            <div class="payment-approval-workbench">
                <table class="table table-bordered table-sm">
                    <thead>
                        <tr>
                            <th style="width: 42px;">#</th>
                            <th style="width: 220px;">${__("Party / Company")}</th>
                            <th style="width: 220px;">${__("Reference")}</th>
                            <th style="width: 150px;" class="text-right">${__("Requested")}</th>
                            <th style="width: 140px;" class="text-right">${__("Balance")}</th>
                            <th style="width: 210px;">${__("Approved Amount")}</th>
                            <th style="width: 150px;">${__("Decision")}</th>
                            <th style="width: 220px;">${__("Remarks")}</th>
                        </tr>
                    </thead>
                    <tbody>${table_rows}</tbody>
                </table>
            </div>
            <div class="mt-2">
                <button type="button" class="btn btn-xs btn-default approve-all">${__("Approve All Full")}</button>
                <button type="button" class="btn btn-xs btn-default clear-review">${__("Clear Review")}</button>
            </div>
        `);

        bind_review_events();
        recalculate_review_summary();
    };

    const get_review_rows = () =>
        wrapper()
            .find("tbody tr")
            .map(function () {
                const row_name = $(this).attr("data-row-name");
                return {
                    name: row_name,
                    approved_amount: flt($(this).find(".approved-amount").val()),
                    row_status: $(this).find(".row-status").val(),
                    manager_remarks: $(this).find(".manager-remarks").val(),
                };
            })
            .get();

    const recalculate_review_summary = () => {
        let requested = 0;
        let approved = 0;
        const counts = { Approved: 0, "Partially Approved": 0, Rejected: 0, Hold: 0, Pending: 0 };

        wrapper().find("tbody tr").each(function () {
            const row = rows.find((item) => item.name === $(this).attr("data-row-name"));
            const status = $(this).find(".row-status").val();
            const approved_amount = flt($(this).find(".approved-amount").val());
            const remarks = ($(this).find(".manager-remarks").val() || "").trim();
            const requested_amount = flt(row && row.requested_amount);

            requested += requested_amount;
            approved += approved_amount;
            counts[status] = (counts[status] || 0) + 1;

            $(this).toggleClass(
                "needs-remarks",
                ["Partially Approved", "Rejected", "Hold"].includes(status) && !remarks
            );
            $(this).toggleClass(
                "invalid-decision",
                approved_amount < 0 ||
                    approved_amount > requested_amount ||
                    (status === "Partially Approved" && (approved_amount <= 0 || approved_amount >= requested_amount))
            );
        });

        wrapper().find(".payment-approval-summary").html(`
            <div class="metric"><span>${__("Requested")}</span><strong>${money(requested)}</strong></div>
            <div class="metric"><span>${__("Approved")}</span><strong>${money(approved)}</strong></div>
            <div class="metric"><span>${__("Difference")}</span><strong>${money(requested - approved)}</strong></div>
            <div class="metric"><span>${__("Rows")}</span><strong>${counts.Approved} full / ${counts["Partially Approved"]} partial / ${counts.Rejected} rejected / ${counts.Hold} hold</strong></div>
        `);
    };

    const set_row_decision = (tr, status, amount) => {
        tr.find(".row-status").val(status);
        tr.find(".approved-amount").val(amount || "");
        recalculate_review_summary();
    };

    const bind_review_events = () => {
        wrapper().find(".approved-amount, .row-status, .manager-remarks").on("input change", function () {
            const tr = $(this).closest("tr");
            const row = rows.find((item) => item.name === tr.attr("data-row-name"));
            const requested = flt(row && row.requested_amount);
            const approved = flt(tr.find(".approved-amount").val());

            if ($(this).hasClass("approved-amount")) {
                if (approved === requested && requested > 0) tr.find(".row-status").val("Approved");
                else if (approved > 0 && approved < requested) tr.find(".row-status").val("Partially Approved");
                else if (!approved && tr.find(".row-status").val() === "Approved") tr.find(".row-status").val("Pending");
            }
            recalculate_review_summary();
        });

        wrapper().find(".quick-actions button").on("click", function () {
            const tr = $(this).closest("tr");
            const row = rows.find((item) => item.name === tr.attr("data-row-name"));
            const action = $(this).attr("data-action");
            if (action === "full") set_row_decision(tr, "Approved", flt(row.requested_amount));
            if (action === "partial") {
                tr.find(".row-status").val("Partially Approved");
                tr.find(".approved-amount").focus();
                recalculate_review_summary();
            }
            if (action === "reject") set_row_decision(tr, "Rejected", 0);
            if (action === "hold") set_row_decision(tr, "Hold", 0);
        });

        wrapper().find(".approve-all").on("click", () => {
            wrapper().find("tbody tr").each(function () {
                const row = rows.find((item) => item.name === $(this).attr("data-row-name"));
                set_row_decision($(this), "Approved", flt(row.requested_amount));
            });
        });

        wrapper().find(".clear-review").on("click", () => {
            wrapper().find("tbody tr").each(function () {
                set_row_decision($(this), "Pending", 0);
                $(this).find(".manager-remarks").val("");
            });
            recalculate_review_summary();
        });
    };

    const save_review = (approve_after_save) => {
        const decisions = get_review_rows();
        const run_save = (approval_otp) => {
            frappe.call({
                method: `${PAYMENT_INDENT_METHOD}.save_approval_review`,
                args: {
                    payment_indent_name: frm.doc.name,
                    decisions,
                    manager_remarks: dialog.get_value("manager_remarks"),
                },
                freeze: true,
                freeze_message: approve_after_save ? __("Saving review and approving") : __("Saving approval review"),
                callback() {
                    if (!approve_after_save) {
                        frappe.show_alert({ message: __("Approval review saved"), indicator: "green" });
                        frm.reload_doc();
                        dialog.hide();
                        return;
                    }

                    frappe.call({
                        method: "frappe.model.workflow.apply_workflow",
                        args: {
                            doc: frm.doc,
                            action: "Approve",
                            approval_otp,
                        },
                        freeze: true,
                        freeze_message: __("Approving Payment Indent"),
                        callback() {
                            dialog.hide();
                            frm.reload_doc();
                        },
                    });
                },
            });
        };

        if (!approve_after_save) {
            run_save(null);
            return;
        }

        ensure_approval_enrollment().then((status) => {
            if (!status) return;
            prompt_for_approval_otp(status).then((token) => {
                if (!token) return;
                run_save(token);
            });
        });
    };

    dialog.show();
    dialog.$wrapper.find(".modal-dialog").css("max-width", "1180px");
    render();
}

function get_or_create_payment_line_row(frm, row_name) {
    if (row_name) {
        return (frm.doc.items || []).find((row) => row.name === row_name);
    }

    const blank_row = (frm.doc.items || []).find((row) => !row.company && !row.reference_type && is_blank_payment_row(row));
    return blank_row || frm.add_child("items");
}

function get_reference_doctype_for_type(reference_type, callback) {
    if (reference_type === "Purchase Invoice") {
        callback("Purchase Invoice");
    } else if (reference_type === "Purchase Order") {
        callback("Purchase Order");
    } else if (reference_type === "Purchase Receipt") {
        callback("Purchase Receipt");
    } else if (reference_type === "Work Order") {
        frappe.db.get_single_value("Payment Indent Settings", "work_order_doctype").then((doctype) => {
            if (!doctype) {
                frappe.msgprint(__("Work Order integration is not configured in Payment Indent Settings."));
            }
            callback(doctype || "");
        });
    } else {
        callback("");
    }
}

function reference_type_supports_multi(reference_type) {
    return ["Purchase Invoice", "Purchase Order", "Purchase Receipt", "Work Order"].includes(reference_type);
}

function set_dialog_values(dialog, values) {
    Object.keys(values || {}).forEach((fieldname) => {
        if (dialog.fields_dict[fieldname]) {
            dialog.set_value(fieldname, values[fieldname] || "");
        }
    });
}

function open_payment_line_dialog(frm, row_name) {
    if (!is_payment_line_editable(frm)) {
        frappe.msgprint(__("Payment lines can be edited only before manager approval is completed."));
        return;
    }

    let current_row_name = row_name || null;
    const existing_row = current_row_name ? (frm.doc.items || []).find((row) => row.name === current_row_name) : null;

    const dialog = new frappe.ui.Dialog({
        title: existing_row ? __("Edit Payment Line {0}", [existing_row.idx]) : __("Add Payment Line"),
        fields: [
            {
                fieldname: "party_section",
                fieldtype: "Section Break",
                label: __("Party"),
            },
            {
                fieldname: "party_search",
                label: __("Party"),
                fieldtype: "Data",
                reqd: 1,
                description: __("Type and choose from the results below."),
            },
            {
                fieldname: "party_results",
                fieldtype: "HTML",
            },
            { fieldname: "party_type", fieldtype: "Data", hidden: 1 },
            { fieldname: "party", fieldtype: "Data", hidden: 1 },
            { fieldname: "party_name", fieldtype: "Data", hidden: 1 },
            {
                fieldname: "reference_section",
                fieldtype: "Section Break",
                label: __("Reference"),
            },
            {
                fieldname: "company",
                label: __("Company"),
                fieldtype: "Link",
                options: "Company",
                reqd: 1,
            },
            { fieldtype: "Column Break" },
            {
                fieldname: "reference_type",
                label: __("Reference Type"),
                fieldtype: "Select",
                options: REFERENCE_TYPE_OPTIONS,
                reqd: 1,
            },
            { fieldname: "reference_doctype", fieldtype: "Link", options: "DocType", hidden: 1 },
            {
                fieldname: "reference_name",
                label: __("Add Reference"),
                fieldtype: "Dynamic Link",
                options: "reference_doctype",
                description: __("Pick a reference and it will be added below. You can add multiple."),
                hidden: 1,
            },
            { fieldname: "references_html", fieldtype: "HTML" },
            {
                fieldname: "terms_section",
                fieldtype: "Section Break",
                label: __("Amount and Terms"),
            },
            {
                fieldname: "payment_terms",
                label: __("Payment Terms"),
                fieldtype: "Select",
                options: PAYMENT_TERMS_OPTIONS,
            },
            { fieldtype: "Column Break" },
            {
                fieldname: "payment_terms_other",
                label: __("Other Payment Terms"),
                fieldtype: "Data",
                hidden: 1,
            },
            {
                fieldname: "requested_amount",
                label: __("Requested Amount"),
                fieldtype: "Currency",
                reqd: 1,
            },
            {
                fieldname: "snapshot_section",
                fieldtype: "Section Break",
                label: __("Reference and Balance Snapshot"),
            },
            { fieldname: "reference_date", label: __("Reference Date"), fieldtype: "Date", read_only: 1 },
            { fieldtype: "Column Break" },
            { fieldname: "reference_amount", label: __("Reference Amount"), fieldtype: "Currency", read_only: 1 },
            { fieldname: "outstanding_amount", label: __("Outstanding / Balance"), fieldtype: "Currency", read_only: 1 },
            { fieldtype: "Column Break" },
            { fieldname: "party_balance", label: __("Party Balance"), fieldtype: "Currency", read_only: 1 },
            { fieldname: "balance_type", label: __("Balance Type"), fieldtype: "Data", read_only: 1 },
            {
                fieldname: "notes_section",
                fieldtype: "Section Break",
                label: __("Notes"),
            },
            {
                fieldname: "payment_remark",
                label: __("Payment Remark"),
                fieldtype: "Small Text",
            },
        ],
        primary_action_label: __("Save & Next"),
        primary_action() {
            save_payment_line(true);
        },
        secondary_action_label: __("Save"),
        secondary_action() {
            save_payment_line(false);
        },
    });

    // --- multi-reference state for this dialog instance ---
    let selectedReferences = [];

    const format_currency_safe = (value) => format_currency(flt(value), frm.doc.currency || undefined);

    const render_references_list = () => {
        const $wrapper = dialog.fields_dict.references_html.$wrapper;
        if (!reference_type_supports_multi(dialog.get_value("reference_type"))) {
            $wrapper.empty();
            return;
        }
        if (!selectedReferences.length) {
            $wrapper.html(`<div class="text-muted small" style="margin: 4px 0 6px;">${__("No references added yet. Pick one above to start.")}</div>`);
            return;
        }
        const rows_html = selectedReferences
            .map((ref, idx) => `
                <tr data-ref-idx="${idx}">
                    <td class="small text-muted">${idx + 1}</td>
                    <td><strong>${frappe.utils.escape_html(ref.reference_name)}</strong></td>
                    <td class="small text-muted">${frappe.utils.escape_html(ref.reference_date ? frappe.format(ref.reference_date, {fieldtype: "Date"}) : "")}</td>
                    <td class="text-right">${format_currency_safe(ref.reference_amount)}</td>
                    <td class="text-right">${format_currency_safe(ref.outstanding_amount)}</td>
                    <td class="text-right"><button type="button" class="btn btn-xs btn-default remove-ref" title="${__("Remove")}">&times;</button></td>
                </tr>
            `).join("");
        const sum_ref = selectedReferences.reduce((s, r) => s + flt(r.reference_amount), 0);
        const sum_out = selectedReferences.reduce((s, r) => s + flt(r.outstanding_amount), 0);
        $wrapper.html(`
            <style>
                .pi-refs-table { width: 100%; border-collapse: collapse; margin: 6px 0 8px; font-size: 12px; }
                .pi-refs-table th, .pi-refs-table td { border: 1px solid var(--border-color); padding: 4px 6px; }
                .pi-refs-table th { background: var(--control-bg); font-weight: 600; }
                .pi-refs-table .text-right { text-align: right; }
                .pi-refs-totals { color: var(--text-muted); font-size: 11px; margin-bottom: 4px; }
                .pi-refs-totals strong { color: var(--text-color); }
            </style>
            <table class="pi-refs-table">
                <thead>
                    <tr><th style="width:30px;">#</th><th>${__("Reference")}</th><th style="width:90px;">${__("Date")}</th><th style="width:110px;" class="text-right">${__("Amount")}</th><th style="width:110px;" class="text-right">${__("Outstanding")}</th><th style="width:36px;"></th></tr>
                </thead>
                <tbody>${rows_html}</tbody>
            </table>
            <div class="pi-refs-totals">
                ${__("Total")}: <strong>${format_currency_safe(sum_ref)}</strong> &middot;
                ${__("Outstanding")}: <strong>${format_currency_safe(sum_out)}</strong>
            </div>
        `);
        $wrapper.find(".remove-ref").on("click", function () {
            const idx = parseInt($(this).closest("tr").attr("data-ref-idx"), 10);
            selectedReferences.splice(idx, 1);
            sync_aggregates_to_dialog();
            render_references_list();
        });
    };

    const sync_aggregates_to_dialog = () => {
        const sum_amount = selectedReferences.reduce((s, r) => s + flt(r.reference_amount), 0);
        const sum_outstanding = selectedReferences.reduce((s, r) => s + flt(r.outstanding_amount), 0);
        const latest_date = selectedReferences
            .map((r) => r.reference_date)
            .filter(Boolean)
            .sort()
            .pop() || "";
        set_dialog_values(dialog, {
            reference_date: latest_date,
            reference_amount: sum_amount,
            outstanding_amount: sum_outstanding,
        });
        if (selectedReferences.length) {
            const first = selectedReferences[0];
            // sync the picker's stored reference_name to the first ref so other
            // legacy code paths (read on row) keep working.
            if (dialog.fields_dict.reference_name) {
                dialog.doc = dialog.doc || {};
                dialog.doc.reference_name = first.reference_name;
            }
        }
    };

    const add_selected_reference_to_list = (ref_name) => {
        const values = get_payment_line_dialog_values(true);
        const reference_type = values.reference_type;
        if (!reference_type_supports_multi(reference_type)) return;
        if (!ref_name) return;
        if (selectedReferences.some((r) => r.reference_name === ref_name)) {
            frappe.show_alert({ message: __("{0} is already in the list.", [ref_name]), indicator: "orange" });
            return;
        }
        frappe.call({
            method: `${PAYMENT_INDENT_METHOD}.get_reference_details`,
            args: {
                reference_type,
                reference_name: ref_name,
                party_type: selectedReferences[0] ? values.party_type : null,
                party: selectedReferences[0] ? selectedReferences[0].party : null,
                company: values.company,
            },
            callback(r) {
                const details = r.message || {};
                if (selectedReferences.length && details.party && selectedReferences[0].party && details.party !== selectedReferences[0].party) {
                    frappe.msgprint(__("All references in a row must belong to the same party. {0} belongs to {1}.", [ref_name, details.party]));
                    return;
                }
                selectedReferences.push({
                    reference_doctype: values.reference_doctype,
                    reference_name: ref_name,
                    reference_date: details.reference_date || null,
                    reference_amount: flt(details.reference_amount),
                    outstanding_amount: flt(details.outstanding_amount),
                    party_type: details.party_type,
                    party: details.party,
                    party_name: details.party_name,
                });
                if (selectedReferences.length === 1 && details.party) {
                    // First ref locks the party
                    set_dialog_values(dialog, {
                        party_type: details.party_type,
                        party: details.party,
                        party_name: details.party_name,
                        party_search: details.party_search || details.party_name,
                    });
                    refresh_dialog_balance();
                }
                sync_aggregates_to_dialog();
                render_references_list();
                // Clear the picker for the next entry
                set_dialog_values(dialog, { reference_name: "" });
            },
        });
    };

    const seed_references_from_row = (row) => {
        if (!row) {
            selectedReferences = [];
            return;
        }
        const all_refs = frm.doc.references || [];
        // Match by name primarily (covers saved docs) and fall back to idx
        // for newly-added items whose temp name was rewritten by the server.
        const row_refs = all_refs.filter(
            (r) =>
                r.payment_indent_item === row.name ||
                (r.payment_indent_item_idx && r.payment_indent_item_idx === row.idx)
        );
        selectedReferences = row_refs.map((r) => ({
            reference_doctype: r.reference_doctype,
            reference_name: r.reference_name,
            reference_date: r.reference_date,
            reference_amount: flt(r.reference_amount),
            outstanding_amount: flt(r.outstanding_amount),
            party_type: row.party_type,
            party: row.party,
            party_name: row.party_name,
        }));
        // Back-compat: if no `references` rows but legacy reference_name set, seed one entry.
        if (!selectedReferences.length && row.reference_name && reference_type_supports_multi(row.reference_type)) {
            selectedReferences.push({
                reference_doctype: row.reference_doctype,
                reference_name: row.reference_name,
                reference_date: row.reference_date,
                reference_amount: flt(row.reference_amount),
                outstanding_amount: flt(row.outstanding_amount),
                party_type: row.party_type,
                party: row.party,
                party_name: row.party_name,
            });
        }
    };

    const write_party = (party) => {
        set_dialog_values(dialog, {
            party_search: party.display,
            party_type: party.party_type,
            party: party.party,
            party_name: party.party_name || party.party,
        });
        dialog.fields_dict.party_results.$wrapper
            .empty()
            .append($("<div>").addClass("text-muted small").text(__("Selected: {0}", [party.display])));
        refresh_dialog_balance();
    };

    const render_party_results = (matches, search_text) => {
        const wrapper = dialog.fields_dict.party_results.$wrapper;
        wrapper.empty();

        const text = (search_text || "").trim();
        if (!text) {
            wrapper.html(`<div class="text-muted small">${__("Start typing to search parties.")}</div>`);
            return;
        }

        const list = $('<div style="display: grid; gap: 6px; margin: 8px 0 2px;"></div>');
        (matches || []).forEach((party) => {
            const button = $(`
                <button type="button" class="btn btn-default btn-sm text-left" style="white-space: normal; text-align: left;">
                    <strong></strong>
                    <div class="text-muted small"></div>
                </button>
            `);
            button.find("strong").text(party.display);
            button.find(".small").text(`${party.party_type}: ${party.party}`);
            button.on("click", () => write_party(party));
            list.append(button);
        });

        const other = $(`
            <button type="button" class="btn btn-secondary btn-sm text-left" style="white-space: normal; text-align: left;">
                <strong></strong>
                <div class="text-muted small">${__("Use for ad-hoc or external parties.")}</div>
            </button>
        `);
        other.find("strong").text(__("Use as Other Party: {0}", [text]));
        other.on("click", () => write_party({
            party_type: "Other",
            party: text,
            party_name: text,
            display: `${text} (Other)`,
        }));
        list.append(other);
        wrapper.append(list);
    };

    const get_party_search_input = () => dialog.fields_dict.party_search.$input;

    const get_dialog_doc = () => {
        dialog.doc = dialog.doc || {};
        return dialog.doc;
    };

    const preserve_party_search_text = (text) => {
        get_dialog_doc().party_search = text;
        dialog.fields_dict.party_search.value = text;
        get_party_search_input().val(text);
    };

    const clear_selected_party_identity = () => {
        const doc = get_dialog_doc();
        doc.party_type = "";
        doc.party = "";
        doc.party_name = "";
        ["party_type", "party", "party_name"].forEach((fieldname) => {
            if (dialog.fields_dict[fieldname]) {
                dialog.fields_dict[fieldname].value = "";
            }
        });
    };

    const toggle_dialog_field = (fieldname, show) => {
        const field = dialog.fields_dict[fieldname];
        if (!field) return;
        field.df.hidden = show ? 0 : 1;
        field.refresh();
        field.$wrapper.toggle(!!show);
    };

    const update_reference_field_visibility = () => {
        const reference_type = dialog.get_value("reference_type");
        const show_reference = reference_type_supports_multi(reference_type);
        const reference_field = dialog.fields_dict.reference_name;

        if (reference_field) {
            reference_field.df.label = show_reference ? __("Add Reference") : __("Reference");
            reference_field.df.reqd = 0;
            reference_field.refresh();
        }
        toggle_dialog_field("reference_name", show_reference);
        toggle_dialog_field("references_html", show_reference);
        render_references_list();
    };

    const update_payment_terms_visibility = () => {
        const show_other = dialog.get_value("payment_terms") === "Others";
        const other_field = dialog.fields_dict.payment_terms_other;
        if (other_field) {
            other_field.df.reqd = show_other ? 1 : 0;
        }
        toggle_dialog_field("payment_terms_other", show_other);
    };

    const search_parties = frappe.utils.debounce(() => {
        const text = get_party_search_input().val() || "";
        preserve_party_search_text(text);
        clear_selected_party_identity();

        frappe.call({
            method: `${PAYMENT_INDENT_METHOD}.search_parties`,
            args: { txt: text },
            callback(r) {
                const current_text = get_party_search_input().val() || "";
                if (current_text !== text) {
                    return;
                }
                render_party_results(r.message || [], text);
                preserve_party_search_text(text);
            },
        });
    }, 250);

    const clear_dialog_reference_snapshot = () => {
        set_dialog_values(dialog, {
            reference_name: "",
            reference_date: "",
            reference_amount: "",
            outstanding_amount: "",
        });
    };

    const refresh_dialog_balance = () => {
        const values = get_payment_line_dialog_values(true);
        if (!values.company || !values.party_type || !values.party) {
            set_dialog_values(dialog, {
                party_balance: 0,
                balance_type: "",
            });
            return;
        }

        frappe.call({
            method: `${PAYMENT_INDENT_METHOD}.refresh_party_balance_for_row`,
            args: {
                company: values.company,
                party_type: values.party_type,
                party: values.party,
                posting_date: frm.doc.posting_date,
            },
            callback(r) {
                if (!r.message) return;
                set_dialog_values(dialog, {
                    party_balance: r.message.balance,
                    balance_type: r.message.balance_type,
                });
            },
        });
    };

    const get_dialog_field_value = (fieldname) => {
        const field = dialog.fields_dict[fieldname];
        if (!field) return dialog.get_value(fieldname);
        const control_value = field.get_value ? field.get_value() : undefined;
        if (control_value !== undefined && control_value !== null && control_value !== "") {
            return control_value;
        }
        const input_value = field.$input && field.$input.length ? field.$input.val() : undefined;
        if (input_value !== undefined && input_value !== null && input_value !== "") {
            return input_value;
        }
        if (field.value !== undefined && field.value !== null && field.value !== "") {
            return field.value;
        }
        return dialog.get_value(fieldname) || "";
    };

    const get_payment_line_dialog_values = (ignore_errors) => {
        const values = dialog.get_values(ignore_errors) || {};
        [
            "party_search",
            "party_type",
            "party",
            "party_name",
            "company",
            "reference_type",
            "reference_doctype",
            "reference_name",
            "payment_terms",
            "payment_terms_other",
            "requested_amount",
            "payment_remark",
            "reference_date",
            "reference_amount",
            "outstanding_amount",
            "party_balance",
            "balance_type",
        ].forEach((fieldname) => {
            values[fieldname] = get_dialog_field_value(fieldname);
        });
        if (values.reference_type === "Purchase Invoice") {
            values.reference_doctype = "Purchase Invoice";
        } else if (values.reference_type === "Purchase Order") {
            values.reference_doctype = "Purchase Order";
        } else if (values.reference_type === "No Reference") {
            values.reference_doctype = "";
            values.reference_name = "";
        }
        return values;
    };

    const fetch_dialog_reference = () => {
        const values = get_payment_line_dialog_values(true);
        if (!values.reference_type || !values.reference_name) return;

        frappe.call({
            method: `${PAYMENT_INDENT_METHOD}.get_reference_details`,
            args: {
                reference_type: values.reference_type,
                reference_name: values.reference_name,
                party_type: values.party_type,
                party: values.party,
                company: values.company,
            },
            callback(r) {
                const details = r.message || {};
                set_dialog_values(dialog, {
                    reference_date: details.reference_date,
                    reference_amount: details.reference_amount,
                    outstanding_amount: details.outstanding_amount,
                    party_type: details.party_type,
                    party: details.party,
                    party_name: details.party_name,
                    party_search: details.party_search || details.party_name,
                });
                refresh_dialog_balance();
            },
        });
    };

    const write_values_to_row = (row, values) => {
        const set_row_value = (fieldname, value) => {
            const normalized_value = value === undefined || value === null ? "" : value;
            row[fieldname] = normalized_value;
            frappe.model.set_value(row.doctype, row.name, fieldname, normalized_value);
        };

        const fieldnames = [
            "party_search",
            "party_type",
            "party",
            "party_name",
            "company",
            "reference_type",
            "reference_date",
            "reference_amount",
            "outstanding_amount",
            "party_balance",
            "balance_type",
            "payment_terms",
            "payment_terms_other",
            "requested_amount",
            "payment_remark",
        ];
        fieldnames.forEach((fieldname) => {
            set_row_value(fieldname, values[fieldname]);
        });

        set_row_value("reference_doctype", values.reference_doctype);
        // Primary reference (back-compat / grid display) = first selected ref
        const first_ref = selectedReferences[0];
        const primary_ref_name = first_ref ? first_ref.reference_name : "";
        set_row_value("reference_name", primary_ref_name);

        if (values.reference_type === "Purchase Invoice") {
            set_row_value("purchase_invoice", primary_ref_name);
            set_row_value("purchase_order", "");
            set_row_value("purchase_receipt", "");
            set_row_value("work_order_reference", "");
        } else if (values.reference_type === "Purchase Order") {
            set_row_value("purchase_order", primary_ref_name);
            set_row_value("purchase_invoice", "");
            set_row_value("purchase_receipt", "");
            set_row_value("work_order_reference", "");
        } else if (values.reference_type === "Purchase Receipt") {
            set_row_value("purchase_receipt", primary_ref_name);
            set_row_value("purchase_invoice", "");
            set_row_value("purchase_order", "");
            set_row_value("work_order_reference", "");
        } else if (values.reference_type === "Work Order") {
            set_row_value("work_order_doctype", values.reference_doctype);
            set_row_value("work_order_reference", primary_ref_name);
            set_row_value("purchase_invoice", "");
            set_row_value("purchase_order", "");
            set_row_value("purchase_receipt", "");
        } else {
            set_row_value("purchase_invoice", "");
            set_row_value("purchase_order", "");
            set_row_value("purchase_receipt", "");
            set_row_value("work_order_reference", "");
        }

        // References live as a sibling child table on the parent doc, keyed
        // back to this row via payment_indent_item (name) AND
        // payment_indent_item_idx (position). New items get renamed by Frappe
        // on insert; the server validation uses idx to repoint orphaned refs.
        frm.doc.references = (frm.doc.references || []).filter(
            (r) =>
                r.payment_indent_item !== row.name &&
                r.payment_indent_item_idx !== row.idx
        );
        if (reference_type_supports_multi(values.reference_type)) {
            selectedReferences.forEach((ref) => {
                const child = frappe.model.add_child(frm.doc, "Payment Indent Item Reference", "references");
                child.payment_indent_item = row.name;
                child.payment_indent_item_idx = row.idx;
                child.reference_doctype = ref.reference_doctype;
                child.reference_name = ref.reference_name;
                child.reference_date = ref.reference_date;
                child.reference_amount = flt(ref.reference_amount);
                child.outstanding_amount = flt(ref.outstanding_amount);
            });
        }
        frm.refresh_field("items");
        frm.refresh_field("references");
    };

    const clear_dialog_for_next = () => {
        current_row_name = null;
        dialog.set_title(__("Add Payment Line"));
        set_dialog_values(dialog, {
            party_search: "",
            party_type: "",
            party: "",
            party_name: "",
            company: "",
            reference_type: "",
            reference_doctype: "",
            reference_name: "",
            payment_terms: "",
            payment_terms_other: "",
            requested_amount: "",
            payment_remark: "",
            reference_date: "",
            reference_amount: "",
            outstanding_amount: "",
            party_balance: 0,
            balance_type: "",
        });
        render_party_results([], "");
        selectedReferences = [];
        update_reference_field_visibility();
        update_payment_terms_visibility();
        render_references_list();
        dialog.fields_dict.party_search.$input.focus();
    };

    const save_payment_line = (next) => {
        const values = get_payment_line_dialog_values();
        if (!values) return;

        if (!values.party_type || !values.party) {
            const text = (values.party_search || "").trim();
            if (!text) {
                frappe.msgprint(__("Select a party before saving the payment line."));
                return;
            }
            values.party_type = "Other";
            values.party = text;
            values.party_name = text;
            values.party_search = `${text} (Other)`;
        }

        if (reference_type_supports_multi(values.reference_type) && !selectedReferences.length) {
            frappe.msgprint(__("Add at least one reference for the chosen Reference Type."));
            return;
        }

        if (values.payment_terms === "Others" && !values.payment_terms_other) {
            frappe.msgprint(__("Enter Other Payment Terms."));
            return;
        }

        if (flt(values.requested_amount) <= 0) {
            frappe.msgprint(__("Requested Amount must be greater than zero."));
            return;
        }

        const row = get_or_create_payment_line_row(frm, current_row_name);
        if (!row) return;

        write_values_to_row(row, values);
        refresh_row_balance(frm, row.doctype, row.name);
        recalculate_totals(frm);
        frm.refresh_field("items");
        configure_payment_line_grid(frm);

        if (next) {
            clear_dialog_for_next();
        } else {
            dialog.hide();
        }
    };

    dialog.show();
    dialog.$wrapper.find(".modal-dialog").css("max-width", "980px");

    if (existing_row) {
        set_dialog_values(dialog, {
            party_search: existing_row.party_search,
            party_type: existing_row.party_type,
            party: existing_row.party,
            party_name: existing_row.party_name,
            company: existing_row.company,
            reference_type: existing_row.reference_type,
            reference_doctype: existing_row.reference_doctype,
            reference_name: "",  // picker stays empty; the list shows what's already picked
            payment_terms: existing_row.payment_terms,
            payment_terms_other: existing_row.payment_terms_other,
            requested_amount: existing_row.requested_amount,
            payment_remark: existing_row.payment_remark,
            reference_date: existing_row.reference_date,
            reference_amount: existing_row.reference_amount,
            outstanding_amount: existing_row.outstanding_amount,
            party_balance: existing_row.party_balance,
            balance_type: existing_row.balance_type,
        });
        seed_references_from_row(existing_row);
        render_references_list();
    } else {
        render_party_results([], "");
    }

    dialog.fields_dict.party_search.$input.on("input", search_parties);
    dialog.fields_dict.company.df.onchange = refresh_dialog_balance;
    const bind_dialog_field_change = (fieldname, handler) => {
        const field = dialog.fields_dict[fieldname];
        if (!field) return;
        field.df.onchange = handler;
        const input = field.$input || field.$wrapper.find("input, select, textarea");
        if (input && input.length) {
            input.off(`change.payment_indent_${fieldname}`).on(`change.payment_indent_${fieldname}`, handler);
        }
    };

    const on_reference_type_change = () => {
        const reference_type = dialog.get_value("reference_type");
        // Switching type invalidates any previously picked refs (they belong to a different doctype)
        selectedReferences = [];
        sync_aggregates_to_dialog();
        clear_dialog_reference_snapshot();
        update_reference_field_visibility();
        get_reference_doctype_for_type(reference_type, (doctype) => {
            dialog.set_value("reference_doctype", doctype);
            update_reference_field_visibility();
        });
    };
    bind_dialog_field_change("reference_type", on_reference_type_change);

    const on_payment_terms_change = () => {
        update_payment_terms_visibility();
        if (dialog.get_value("payment_terms") !== "Others") {
            dialog.set_value("payment_terms_other", "");
        }
    };
    bind_dialog_field_change("payment_terms", on_payment_terms_change);
    bind_dialog_field_change("reference_name", () => {
        const picked = dialog.fields_dict.reference_name && dialog.fields_dict.reference_name.get_value();
        if (picked) add_selected_reference_to_list(picked);
    });
    dialog.fields_dict.reference_name.get_query = () => {
        const values = get_payment_line_dialog_values(true);
        const filters = {};
        if (["Purchase Invoice", "Purchase Order", "Purchase Receipt"].includes(values.reference_doctype)) {
            filters.docstatus = 1;
            // Once a first ref locks the party, restrict the picker to that supplier
            const locked_party = selectedReferences[0] && selectedReferences[0].party;
            const supplier = locked_party || (values.party_type === "Supplier" ? values.party : null);
            if (supplier) filters.supplier = supplier;
            if (values.company) filters.company = values.company;
        }
        return { filters };
    };
    update_reference_field_visibility();
    update_payment_terms_visibility();
}

frappe.ui.form.on("Payment Indent", {
    setup(frm) {
        const party_field = frm.fields_dict.items.grid.get_field("party_search");
        if (party_field) {
            party_field.get_query = () => ({
                query: `${PAYMENT_INDENT_METHOD}.get_party_autocomplete_options`,
                translate_values: false,
            });
        }

        frm.set_query("reference_name", "items", (doc, cdt, cdn) => {
            const row = locals[cdt][cdn];
            const filters = {};
            if (row.reference_doctype === "Purchase Invoice") {
                filters.docstatus = 1;
                if (row.party_type === "Supplier" && row.party) filters.supplier = row.party;
                if (row.company) filters.company = row.company;
            } else if (row.reference_doctype === "Purchase Order") {
                filters.docstatus = 1;
                if (row.party_type === "Supplier" && row.party) filters.supplier = row.party;
                if (row.company) filters.company = row.company;
            }
            return { filters };
        });
    },

    onload_post_render(frm) {
        schedule_clear_default_company(frm);
    },

    refresh(frm) {
        bind_party_picker_to_grid(frm);
        set_approval_field_access(frm);
        schedule_clear_default_company(frm);
        configure_payment_line_grid(frm);
        hide_native_workflow_approve(frm);

        if (can_edit_approval_fields(frm)) {
            frm.add_custom_button(__("Review & Approve Payments"), () => open_approval_workbench(frm));
        }

        add_approval_security_buttons(frm);

        if (["Draft", "Returned for Correction", "Pending Manager Approval"].includes(frm.doc.workflow_state)) {
            frm.add_custom_button(__("Add Payment Line"), () => open_payment_line_dialog(frm));
            frm.add_custom_button(__("Edit Selected Line"), () => {
                const selected = frm.fields_dict.items.grid.get_selected_children();
                if (!selected.length) {
                    frappe.msgprint(__("Select one payment line to edit."));
                    return;
                }
                if (selected.length > 1) {
                    frappe.msgprint(__("Select only one payment line to edit."));
                    return;
                }
                open_payment_line_dialog(frm, selected[0].name);
            });
            frm.add_custom_button(__("Refresh Party Balances"), () => refresh_all_row_balances(frm));
            frm.add_custom_button(__("Select Party"), () => open_party_picker(frm));
        }

        if (frm.doc.workflow_state === "Manager Approved") {
            frm.add_custom_button(__(frm.doc.pdf_generated ? "Regenerate PDF" : "Generate PDF"), () => {
                frappe.call({
                    method: `${PAYMENT_INDENT_METHOD}.generate_pdf`,
                    args: { payment_indent_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Generating PDF"),
                    callback() {
                        frm.reload_doc();
                    },
                });
            });
        }
    },

    posting_date(frm) {
        refresh_all_row_balances(frm);
    },

    items_add(frm, cdt, cdn) {
        const row = locals[cdt] && locals[cdt][cdn];
        if (row && row.company && is_blank_payment_row(row)) {
            frappe.model.set_value(cdt, cdn, "company", "");
        }
        recalculate_totals(frm);
        schedule_clear_default_company(frm);
    },

    items_remove(frm) {
        recalculate_totals(frm);
    },
});

function open_party_picker(frm, selected_row_name, initial_text) {
    const rows = frm.doc.items || [];
    if (!rows.length) {
        frappe.msgprint(__("Add a row before selecting a party."));
        return;
    }

    const selected_row = rows.find((row) => row.name === selected_row_name);
    const selected_row_index = selected_row ? rows.indexOf(selected_row) + 1 : null;
    const row_options = rows.map((row, index) => ({
        label: `${index + 1}. ${row.party_search || row.reference_type || __("New Row")}`,
        value: row.name,
    }));
    const row_field = selected_row
        ? {
              fieldname: "row_name",
              fieldtype: "Data",
              default: selected_row.name,
              hidden: 1,
          }
        : {
              fieldname: "row_name",
              label: __("Row"),
              fieldtype: "Select",
              options: row_options,
              default: row_options[0].value,
              reqd: 1,
          };

    const dialog = new frappe.ui.Dialog({
        title: selected_row_index ? __("Select Party for Row {0}", [selected_row_index]) : __("Select Party"),
        fields: [
            row_field,
            {
                fieldname: "party_search",
                label: __("Supplier / Customer / Employee / Other Party"),
                fieldtype: "Data",
                default: initial_text || "",
                reqd: 1,
                description: __("Type a name, then choose from the results below."),
            },
            {
                fieldname: "party_results",
                fieldtype: "HTML",
            },
        ],
        primary_action_label: __("Use Party"),
        primary_action(values) {
            const row = rows.find((item) => item.name === values.row_name);
            if (!row) return;
            use_other_party(values.party_search);
            dialog.hide();
        },
    });

    const render_results = (matches, search_text) => {
        const wrapper = dialog.fields_dict.party_results.$wrapper;
        wrapper.empty();

        if (!search_text) {
            wrapper.html(`<div class="text-muted small">${__("Start typing to search parties.")}</div>`);
            return;
        }

        if (!matches.length) {
            wrapper.append(`<div class="text-muted small">${__("No existing party matched.")}</div>`);
        }

        const list = $('<div class="payment-indent-party-results" style="display: grid; gap: 6px; margin-top: 8px;"></div>');
        matches.forEach((party) => {
            const button = $(`
                <button type="button" class="btn btn-default btn-sm text-left" style="white-space: normal; text-align: left;">
                    <strong></strong>
                    <div class="text-muted small"></div>
                </button>
            `);
            button.find("strong").text(party.display);
            button.find(".small").text(`${party.party_type}: ${party.party}`);
            button.on("click", () => {
                const row = rows.find((item) => item.name === dialog.get_value("row_name"));
                set_row_party(frm, row, party);
                dialog.hide();
            });
            list.append(button);
        });

        const other = $(`
            <button type="button" class="btn btn-secondary btn-sm text-left" style="white-space: normal; text-align: left;">
                <strong></strong>
                <div class="text-muted small">${__("Use when this party is not a Supplier, Customer, Employee, or configured Party Type.")}</div>
            </button>
        `);
        other.find("strong").text(__("Use as Other Party: {0}", [search_text]));
        other.on("click", () => {
            use_other_party(search_text);
            dialog.hide();
        });
        list.append(other);
        wrapper.append(list);
    };

    const use_other_party = (search_text) => {
        const text = (search_text || "").trim();
        if (!text) return;
        const row = rows.find((item) => item.name === dialog.get_value("row_name"));
        set_row_party(frm, row, {
            party_type: "Other",
            party: text.replace(/\s+\(Other\)\s*$/, ""),
            party_name: text.replace(/\s+\(Other\)\s*$/, ""),
            display: text.endsWith("(Other)") ? text : `${text} (Other)`,
        });
    };

    const search = frappe.utils.debounce(() => {
        const text = dialog.get_value("party_search") || "";
        frappe.call({
            method: `${PAYMENT_INDENT_METHOD}.search_parties`,
            args: { txt: text },
            callback(r) {
                render_results(r.message || [], text.trim());
            },
        });
    }, 250);

    dialog.show();
    dialog.fields_dict.party_search.$input.on("input", search);
    dialog.fields_dict.party_search.$input.on("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            search();
        }
    });
    search();
}

frappe.ui.form.on("Payment Indent Item", {
    company(frm, cdt, cdn) {
        refresh_row_balance(frm, cdt, cdn);
    },

    reference_type(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const fields_to_clear = [
            "reference_name",
            "purchase_invoice",
            "purchase_order",
            "work_order_reference",
            "reference_date",
            "reference_amount",
            "outstanding_amount",
            "description",
        ];
        fields_to_clear.forEach((fieldname) => frappe.model.set_value(cdt, cdn, fieldname, ""));
        set_reference_doctype(frm, cdt, cdn);
    },

    party_search(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.party_search) {
            frappe.model.set_value(cdt, cdn, "party_type", "");
            frappe.model.set_value(cdt, cdn, "party", "");
            frappe.model.set_value(cdt, cdn, "party_name", "");
            return;
        }

        frappe.call({
            method: `${PAYMENT_INDENT_METHOD}.search_parties`,
            args: { txt: row.party_search },
            callback(r) {
                const matches = r.message || [];
                if (!matches.length) {
                    frappe.msgprint(__("No Supplier, Customer, or Employee found for {0}", [row.party_search]));
                    return;
                }

                const exact = matches.filter((party) =>
                    [party.party, party.party_name, party.display]
                        .filter(Boolean)
                        .some((value) => value.toLowerCase() === row.party_search.toLowerCase())
                );
                const selected = exact.length === 1 || matches.length === 1 ? (exact[0] || matches[0]) : null;

                if (!selected) {
                    open_party_picker(frm, cdn, row.party_search);
                    return;
                }

                set_row_party(frm, row, selected);
            },
        });
    },

    reference_name(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        fetch_reference_details(frm, cdt, cdn, row.reference_type, row.reference_name);
    },

    purchase_invoice(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.purchase_invoice && !row.reference_name) {
            frappe.model.set_value(cdt, cdn, "reference_name", row.purchase_invoice);
        }
    },

    purchase_order(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.purchase_order && !row.reference_name) {
            frappe.model.set_value(cdt, cdn, "reference_name", row.purchase_order);
        }
    },

    work_order_reference(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.work_order_reference && !row.reference_name) {
            frappe.model.set_value(cdt, cdn, "reference_name", row.work_order_reference);
        }
    },

    requested_amount(frm) {
        recalculate_totals(frm);
    },

    approved_amount(frm) {
        recalculate_totals(frm);
    },
});

app_name = "payment_indent"
app_title = "Payment Indent"
app_publisher = "Dux Digitech"
app_description = "Payment indent and manager approval system for ERPNext"
app_email = "aditya.surana@thesvsgroup.org"
app_license = "mit"

doc_events = {
    "File": {
        "on_trash": "payment_indent.payment_indent.doctype.payment_indent.payment_indent.prevent_payment_indent_pdf_delete",
    },
}

fixtures = [
    {
        "dt": "Role",
        "filters": [
            [
                "role_name",
                "in",
                [
                    "Payment Creator",
                    "Payment Approver",
                    "Payment Indent Admin",
                ],
            ]
        ],
    },
    {
        "dt": "Workflow",
        "filters": [["name", "=", "Payment Indent Workflow"]],
    },
    {
        "dt": "Workflow State",
        "filters": [
            [
                "name",
                "in",
                [
                    "Pending Manager Approval",
                    "Manager Approved",
                    "Manager Rejected",
                    "Returned for Correction",
                ],
            ]
        ],
    },
    {
        "dt": "Workflow Action Master",
        "filters": [
            [
                "workflow_action_name",
                "in",
                [
                    "Submit for Manager Approval",
                    "Resubmit for Manager Approval",
                    "Approve",
                    "Reject",
                    "Return for Correction",
                ],
            ]
        ],
    },
    {
        "dt": "Print Format",
        "filters": [["name", "=", "Payment Indent Approval Report"]],
    },
]

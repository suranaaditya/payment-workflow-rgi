# Payment Indent

Portable Frappe / ERPNext v16 app for payment indent creation, manager approval, and approval PDF generation.

## Features

- Row-wise payment requests for multiple parties and companies in one Payment Indent
- Party picker across Supplier, Customer, Employee, and ad-hoc Other parties
- Purchase Invoice, Purchase Order, configurable Work Order, and No Reference lines
- Company-wise party balance snapshot on each line
- Payment terms and payment remarks on each line
- Creator and approver roles with workflow-based approval
- Manager approval review dialog for swift line-wise approval
- Landscape Payment Indent Approval Report PDF
- Generated approval PDFs protected from deletion

## Roles

- Payment Creator
- Payment Approver
- Payment Indent Admin

## Installation

From a Frappe bench:

```bash
bench get-app https://github.com/suranaaditya/payment-workflow-rgi.git --branch version-16
bench --site your-site.local install-app payment_indent
bench --site your-site.local migrate
bench --site your-site.local clear-cache
```

## Configuration

Open **Payment Indent Settings** after installation.

Work Order integration is optional and configurable. Set the Work Order DocType and field mappings there if the deployment uses a custom work order document.

## Scope

This app currently stops at manager approval and PDF generation. It does not create Payment Entry, Payment Order, Journal Entry, GL Entry, or any accounting voucher.

## License

MIT

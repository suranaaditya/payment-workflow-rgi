# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

import pyotp

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime
from frappe.utils.password import get_decrypted_password


APPROVER_ROLES = {"Payment Approver", "Payment Indent Admin", "System Manager"}
ADMIN_ROLES = {"Payment Indent Admin", "System Manager"}

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 5 * 60
DEFAULT_ISSUER = "RGI Payment Indent"


class PaymentApprovalAuthenticator(Document):
	def validate(self):
		if not self.user:
			frappe.throw(_("User is required."))


def _require_approver():
	if not APPROVER_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(
			_("Only Payment Approvers can manage approval authenticators."),
			frappe.PermissionError,
		)


def _require_admin():
	if not ADMIN_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(
			_("Only Payment Indent Admin or System Manager can perform this action."),
			frappe.PermissionError,
		)


def _get_record(user, for_update=False):
	name = frappe.db.exists("Payment Approval Authenticator", {"user": user})
	if not name:
		return None
	return frappe.get_doc("Payment Approval Authenticator", name)


def _get_secret(doc):
	if not doc:
		return None
	return get_decrypted_password(
		"Payment Approval Authenticator",
		doc.name,
		"otp_secret",
		raise_exception=False,
	)


def _issuer_name():
	settings = frappe.get_single("Payment Indent Settings")
	return (getattr(settings, "otp_issuer_name", None) or DEFAULT_ISSUER).strip() or DEFAULT_ISSUER


def _failed_attempts_key(user):
	return f"payment_indent:otp_fail:{user}"


def _lockout_key(user):
	return f"payment_indent:otp_lock:{user}"


def _is_locked_out(user):
	return bool(frappe.cache().get_value(_lockout_key(user)))


def _record_failure(user):
	cache = frappe.cache()
	key = _failed_attempts_key(user)
	attempts = cint(cache.get_value(key)) + 1
	cache.set_value(key, attempts, expires_in_sec=LOCKOUT_SECONDS)
	if attempts >= MAX_FAILED_ATTEMPTS:
		cache.set_value(_lockout_key(user), 1, expires_in_sec=LOCKOUT_SECONDS)
	return attempts


def _clear_failures(user):
	cache = frappe.cache()
	cache.delete_value(_failed_attempts_key(user))
	cache.delete_value(_lockout_key(user))


@frappe.whitelist()
def get_enrollment_status():
	_require_approver()
	user = frappe.session.user
	doc = _get_record(user)
	has_secret = bool(_get_secret(doc)) if doc else False
	enrolled = bool(doc and cint(doc.enabled) and has_secret)
	return {
		"enrolled": enrolled,
		"pending_confirmation": bool(doc and not enrolled),
		"enrolled_on": doc.enrolled_on if doc else None,
		"account_label": _account_label(user),
	}


@frappe.whitelist()
def reset_my_authenticator():
	_require_approver()
	user = frappe.session.user
	name = frappe.db.exists("Payment Approval Authenticator", {"user": user})
	if name:
		frappe.delete_doc("Payment Approval Authenticator", name, ignore_permissions=True)
	_clear_failures(user)
	return {"reset": True}


@frappe.whitelist()
def start_enrollment():
	_require_approver()
	user = frappe.session.user
	account_label = _account_label(user)

	doc = _get_record(user)
	secret = pyotp.random_base32()

	if doc:
		doc.enabled = 0
		doc.last_used_token = None
		doc.last_used_on = None
		doc.enrolled_on = None
		doc.otp_secret = secret
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Payment Approval Authenticator",
				"user": user,
				"otp_secret": secret,
				"enabled": 0,
			}
		)
		doc.insert(ignore_permissions=True)

	issuer = _issuer_name()
	uri = pyotp.TOTP(secret).provisioning_uri(name=account_label, issuer_name=issuer)
	return {
		"otpauth_uri": uri,
		"issuer": issuer,
		"account": account_label,
		"manual_key": secret,
		"qr_image": _render_qr_data_uri(uri),
	}


def _account_label(user):
	row = frappe.db.get_value(
		"User", user, ["first_name", "full_name", "username", "email"], as_dict=True
	) or {}
	for field in ("first_name", "full_name", "username", "email"):
		value = (row.get(field) or "").strip()
		if value:
			return value
	return user


def _render_qr_data_uri(uri):
	try:
		import base64
		import io

		import qrcode

		buffer = io.BytesIO()
		qrcode.make(uri).save(buffer, format="PNG")
		encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
		return f"data:image/png;base64,{encoded}"
	except Exception:
		return None


@frappe.whitelist()
def confirm_enrollment(token):
	_require_approver()
	user = frappe.session.user
	doc = _get_record(user)
	if not doc:
		frappe.throw(_("Start enrollment before confirming."))

	secret = _get_secret(doc)
	if not secret:
		frappe.throw(_("Authenticator secret is missing. Restart enrollment."))

	token = (token or "").strip()
	if not token.isdigit() or len(token) != 6:
		frappe.throw(_("Enter the 6-digit code from your authenticator app."))

	if not pyotp.TOTP(secret).verify(token, valid_window=1):
		frappe.throw(_("Code did not match. Try again."))

	doc.enabled = 1
	doc.enrolled_on = now_datetime()
	doc.last_used_token = token
	doc.last_used_on = now_datetime()
	doc.save(ignore_permissions=True)
	_clear_failures(user)
	return {"enrolled": True}


@frappe.whitelist()
def reset_authenticator(user):
	_require_admin()
	if not user:
		frappe.throw(_("User is required."))
	name = frappe.db.exists("Payment Approval Authenticator", {"user": user})
	if not name:
		return {"reset": False}
	frappe.delete_doc("Payment Approval Authenticator", name, ignore_permissions=True)
	_clear_failures(user)
	return {"reset": True}


def verify_approval_token(user, token):
	"""
	Verify a TOTP token for the given user. Raises frappe.throw on any failure.
	Caller is responsible for checking the master enable setting before calling.
	"""
	if _is_locked_out(user):
		frappe.throw(
			_("Too many invalid approval codes. Try again in a few minutes."),
			frappe.ValidationError,
		)

	doc = _get_record(user)
	secret = _get_secret(doc) if doc else None
	if not doc or not cint(doc.enabled) or not secret:
		frappe.throw(_("Set up your Approval Authenticator before approving."))

	token = (token or "").strip()
	if not token:
		frappe.throw(_("Approval OTP is required."))
	if not token.isdigit() or len(token) != 6:
		_record_failure(user)
		frappe.throw(_("Enter the 6-digit code from your authenticator app."))

	if doc.last_used_token and token == doc.last_used_token:
		_record_failure(user)
		frappe.throw(_("This code was already used. Wait for the next code."))

	if not pyotp.TOTP(secret).verify(token, valid_window=1):
		attempts = _record_failure(user)
		remaining = max(MAX_FAILED_ATTEMPTS - attempts, 0)
		if remaining:
			frappe.throw(
				_("Invalid approval code. {0} attempt(s) remaining before lockout.").format(remaining)
			)
		frappe.throw(_("Invalid approval code. Account locked for 5 minutes."))

	frappe.db.set_value(
		"Payment Approval Authenticator",
		doc.name,
		{"last_used_token": token, "last_used_on": now_datetime()},
		update_modified=False,
	)
	_clear_failures(user)

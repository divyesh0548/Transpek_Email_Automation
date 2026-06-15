import hashlib
import secrets
import threading
import time
import os
from Utility_Functions.config.utility_functions import send_email

OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_IN_SECONDS", "60"))

# Channels must stay stable — used in pending-OTP cache keys.
CHANNEL_IM_HOD = "im_hod"
CHANNEL_IM_ACCOUNTANT = "im_accountant"
CHANNEL_PURCHASE_ORDER = "purchase_order"
CHANNEL_SALES_ORDER = "sales_order"
CHANNEL_JOB_CARD = "job_card"


def generate_otp() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def make_otp_hash(identity: str, request_id: str, otp: str, secret_key: str) -> str:
    """
    Hash OTP with identity + request_id + app secret.
    identity is approver email (single) or a normalized scope string (e.g. sorted accountant emails).
    """
    raw = f"{identity.strip().lower()}|{request_id}|{otp}|{secret_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def accountant_otp_scope(acc1, acc2):
    parts = {e.strip().lower() for e in (acc1 or "", acc2 or "") if e and str(e).strip()}
    return "|".join(sorted(parts))


_pending_lock = threading.Lock()
_pending = {}


def _pending_map_key(channel: str, action: str, request_id: str, cache_identity) -> tuple:
    action_l = (action or "").strip().lower()
    rid = str(request_id)
    if cache_identity is None:
        return (channel, action_l, rid)
    return (channel, action_l, cache_identity.strip().lower(), rid)


def _prune_unlocked(now: float) -> None:
    dead = [k for k, v in _pending.items() if now > v["expires_at"]]
    for k in dead:
        del _pending[k]


def get_or_issue_pending_otp(
    channel: str,
    action: str,
    request_id: str,
    *,
    cache_identity=None,
    ttl_seconds=None,
):
    """
    Returns (otp, should_send_email, ttl_seconds_remaining).
    cache_identity: approver email for most flows; None => one pending OTP per (channel, action, request_id).
    """
    if ttl_seconds is None:
        ttl_seconds = OTP_TTL_SECONDS
    key = _pending_map_key(channel, action, request_id, cache_identity)
    now = time.time()
    with _pending_lock:
        _prune_unlocked(now)
        entry = _pending.get(key)
        if entry and now <= entry["expires_at"]:
            ttl_left = max(1, int(entry["expires_at"] - now))
            return entry["otp"], False, ttl_left
        otp = generate_otp()
        _pending[key] = {"otp": otp, "expires_at": now + ttl_seconds}
        return otp, True, ttl_seconds


def clear_pending_otp(channel: str, action: str, request_id: str, *, cache_identity=None):
    key = _pending_map_key(channel, action, request_id, cache_identity)
    with _pending_lock:
        _pending.pop(key, None)


def send_otp_email(
    to_email: str,
    order_type: str,
    action: str,
    otp: str,
    request_id: str,
    ttl_seconds: int = 180,
) -> dict:
    try:
        if action == "approve":
            action_text = "Approval"
        elif action == "reject":
            action_text = "Rejection"
        else:
            action_text = str(action).title()
        subject = f"OTP for {request_id} {action_text}"
        plain_body = f"""
Dear User,

Your OTP for {order_type} {action_text} is: {otp}

This OTP is valid for {ttl_seconds} seconds.

Order No: {request_id}

If you did not request this, please ignore this email.
"""
        html_body = f"""
<html>
    <body style="margin: 0; padding: 0; background-color: #f6f7fb; font-family: Arial, Helvetica, sans-serif; color: #1f2937;">
        <div style="max-width: 620px; margin: 0 auto; padding: 32px 20px;">
            <div style="background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 32px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);">
                <p style="margin: 0 0 16px; font-size: 16px; line-height: 1.6;">Dear User,</p>
                <p style="margin: 0 0 12px; font-size: 16px; line-height: 1.6;">Your OTP for <strong>{order_type} {action_text}</strong> is:</p>
                <div style="margin: 20px 0; padding: 18px 24px; background: #f8fafc; border: 2px dashed #cbd5e1; border-radius: 10px; text-align: center;">
                    <span style="font-size: 40px; font-weight: 800; letter-spacing: 0.18em; color: #111827; display: inline-block;">{otp}</span>
                </div>
                <p style="margin: 0 0 12px; font-size: 15px; line-height: 1.6;"><strong>Validity:</strong> {ttl_seconds} seconds</p>
                <p style="margin: 0 0 12px; font-size: 15px; line-height: 1.6;"><strong>Order No:</strong> {request_id}</p>
                <p style="margin: 20px 0 0; font-size: 14px; line-height: 1.6; color: #6b7280;">If you did not request this, please ignore this email.</p>
            </div>
        </div>
    </body>
</html>
"""
        result = send_email(to_email, subject, plain_body, html_body=html_body)
        if result.get("success"):
            print(f"OTP {otp} sent to {to_email} for request {request_id}")
        return result
    except Exception as e:
        return {"success": False, "message": str(e)}


def send_otp_emails_to_many(
    to_emails,
    order_type: str,
    action: str,
    otp: str,
    request_id: str,
    ttl_seconds: int = 180,
) -> dict:
    addresses = [e.strip() for e in to_emails if e and str(e).strip()]
    if not addresses:
        return {"success": False, "message": "No OTP recipient email configured."}
    for addr in addresses:
        res = send_otp_email(addr, order_type, action, otp, request_id, ttl_seconds)
        if not res.get("success"):
            return res
    return {"success": True}

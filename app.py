from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, render_template_string, session
from Utility_Functions.config.constants import PURCHASE_EMAIL_TABLE, PURCHASE_HEADER_MAIN, REMINDER_DURATION_TABLE, APPROVAL_ENTRY_TABLE, PURCHASE_REQ_TABLE, SALES_ORDER_EMAIL_TABLE, SALES_ORDER_MAIN, RESTRICTION_RECORDS_TABLE, JOB_CARD_MAIN_TABLE
from Utility_Functions.config.db_utils import background_email_checker, backgroud_email_status_fix
from Utility_Functions.config.database import setup_database, get_odbc_connection_string, get_engine
from Utility_Functions.IM_Purchase.im_purchase import get_full_timestamp, get_purchase_request_by_id, is_request_already_processed, update_purchase_request_status_with_text, send_accountant_response_email_to_creator
from Utility_Functions.Purchase_Order.purchase import  get_purchase_request_data_by_id, send_purchase_response_email_to_creator
from Utility_Functions.Sales_Order.sales_order import  get_sales_order_data_by_id, send_sales_order_response_email_to_customer
from Utility_Functions.JOB_Task.job_task import send_job_card_response_email_to_customer, get_job_card_data_by_id
from sqlalchemy import text
import os
from flask_wtf.csrf import CSRFProtect
from datetime import datetime, timedelta
import re  # Import re for regular expressions
import json
import time
import uuid
import pyodbc
import hmac
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from Utility_Functions.config.secure import encrypt, decrypt
from Utility_Functions.config.release_API import (
    trigger_oder_release,
    release_api_succeeded,
    release_api_should_retry,
    release_api_failure_message,
)
from load_secrets import load_secrets
from flask_session import Session
from Utility_Functions.config.approval_otp import (
    OTP_TTL_SECONDS,
    CHANNEL_IM_HOD,
    CHANNEL_IM_ACCOUNTANT,
    CHANNEL_PURCHASE_ORDER,
    CHANNEL_SALES_ORDER,
    CHANNEL_JOB_CARD,
    make_otp_hash,
    get_or_issue_pending_otp,
    clear_pending_otp,
    send_otp_email,
    send_otp_emails_to_many,
    accountant_otp_scope,
    )

#Load secrets
load_secrets()

PURCHASE_ORDER_API_URL = os.getenv("PO_API")

SALES_ORDER_API_URL = os.getenv("SO_API")

print(f"Using Purchase Order API URL: {PURCHASE_ORDER_API_URL}")
print(f"Using Sales Order API URL: {SALES_ORDER_API_URL}")


app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)
Session(app)

# Initialize CSRF protection
csrf = CSRFProtect(app)


def is_email_otp_required():
    """If False, skip OTP emails, HTML fields, and server-side OTP checks."""
    return os.getenv("REQUIRE_EMAIL_OTP", "true").strip().lower() in ("1", "true", "yes", "on")


@app.context_processor
def inject_email_otp_flag():
    return {"otp_required": is_email_otp_required()}


scheduler = BackgroundScheduler()


# Session helper functions for OTP management (hash uses shared approval_otp.make_otp_hash)
OTP_MAX_ATTEMPTS_BY_USER = int(os.getenv("OTP_MAX_ATTEMPTS", "3"))


def save_otp_in_session(identity: str, request_id: str, otp: str, ttl_seconds=None):
    if ttl_seconds is None:
        ttl_seconds = OTP_TTL_SECONDS
    secret = app.config["SECRET_KEY"]
    session["approval_otp"] = {
        "email": identity.strip().lower(),
        "request_id": str(request_id),
        "otp_hash": make_otp_hash(identity, request_id, otp, secret),
        "expires_at": time.time() + ttl_seconds,
        "attempts": 0,
        "verified": False,
    }


def verify_otp_from_session(identity: str, request_id: str, entered_otp: str):
    """
    Returns (ok, message, reason).
    ``reason`` is ``'otp_expired'`` when the session OTP TTL has passed (client can show a warning and close).
    ``reason`` is ``'otp_max_attempts'`` when the user has used all allowed wrong guesses (same UX: message + close).
    """
    data = session.get("approval_otp")

    if not data:
        return False, "OTP session not found. Please reopen the approval link.", None

    if data.get("verified"):
        return False, "OTP already used.", None

    if data.get("email") != identity.strip().lower():
        return False, "OTP does not belong to this email.", None

    if data.get("request_id") != str(request_id):
        return False, "OTP does not belong to this request.", None

    if time.time() > data.get("expires_at", 0):
        session.pop("approval_otp", None)
        return (
            False,
            "This OTP has expired. Open the approval link from your email to get a new code. This window will close automatically.",
            "otp_expired",
        )

    attempts = data.get("attempts", 0)
    if attempts >= OTP_MAX_ATTEMPTS_BY_USER:
        session.pop("approval_otp", None)
        return False, "Max attempts for OTP is reached", "otp_max_attempts"

    expected_hash = data.get("otp_hash")
    secret = app.config["SECRET_KEY"]
    actual_hash = make_otp_hash(identity, request_id, entered_otp, secret)

    if not hmac.compare_digest(expected_hash, actual_hash):
        data["attempts"] = attempts + 1
        session["approval_otp"] = data
        if data["attempts"] >= OTP_MAX_ATTEMPTS_BY_USER:
            session.pop("approval_otp", None)
            return False, "Max attempts for OTP is reached", "otp_max_attempts"
        return False, "Invalid OTP.", None

    data["verified"] = True
    session["approval_otp"] = data
    return True, "OTP verified.", None

def clear_otp_session():
    session.pop("approval_otp", None)


def _im_hod_approver_email(requisition: dict) -> str:
    return (requisition.get("Approver Mail ID") or requisition.get("approver_mailid") or "").strip()


def _render_otp_send_failed():
    return render_template_string("""
    <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
    <h2>❌ OTP Email Failed</h2>
    <p>Could not send OTP email. Please try again later.</p>
    <script>setTimeout(function(){window.close();}, 5000);</script>
    </body></html>
    """)


def start_scheduler():
    if not scheduler.get_jobs():  # Check if the scheduler has jobs
        scheduler.add_job(
            func=background_email_checker,
            trigger="interval",
            seconds=15,
            id='check_emails_job',
            name='Check pending emails every 30 seconds',
            replace_existing=True,
            max_instances=1
        )
        scheduler.add_job(
            func=backgroud_email_status_fix,
            trigger="interval",
            seconds=5,
            id='backgroud_email_status_fix_job',
            name='Background email status fix every 5 seconds',
            replace_existing=True
        )
        scheduler.start()
        print("Scheduler started successfully.")



def _run_delayed_release_api_call(
    request_id,
    attempt,
    order_label,
    api_url,
    payload_key,
    retry_job,
    max_attempts=500,
    retry_minutes=2,
):
    release_result = trigger_oder_release(
        payload={payload_key: str(request_id)},
        api_url=api_url,
    )

    print(
        f"Attempt {attempt} for {order_label} {request_id}: "
        f"API call result: {release_result}"
    )

    if release_api_succeeded(release_result):
        print(f"API call successful for {order_label} {request_id}. Stopping retries.")
        return True

    print(
        f"API call failed for {order_label} {request_id}: "
        f"{release_api_failure_message(release_result)}"
    )

    if not release_api_should_retry(release_result):
        print(f"Non-retryable error for {order_label} {request_id}. Stopping retries.")
        return False

    if attempt < max_attempts:
        print(f"Retrying API call for {order_label} {request_id} in {retry_minutes} minutes...")
        scheduler.add_job(
            retry_job,
            "date",
            run_date=datetime.now() + timedelta(minutes=retry_minutes),
            args=[request_id, attempt + 1],
        )
        return False

    print(f"API call failed for {order_label} {request_id} after {attempt} attempts. Giving up.")
    return False


def delayed_po_api_call(request_id, attempt=1):
    return _run_delayed_release_api_call(
        request_id=request_id,
        attempt=attempt,
        order_label="PO",
        api_url=PURCHASE_ORDER_API_URL,
        payload_key="pO_No",
        retry_job=delayed_po_api_call,
    )


def delayed_so_api_call(request_id, attempt=1):
    return _run_delayed_release_api_call(
        request_id=request_id,
        attempt=attempt,
        order_label="SO",
        api_url=SALES_ORDER_API_URL,
        payload_key="sO_No",
        retry_job=delayed_so_api_call,
    )



def _telemetry_label(mapping, key, default='Unknown'):
    """JSON may contain explicit null; dict.get still returns None for present nulls."""
    if not isinstance(mapping, dict):
        return default
    val = mapping.get(key)
    if val is None or val == '':
        return default
    return val


def _telemetry_scalar(val, default='Unknown'):
    if val is None or val == '':
        return default
    return val

def format_device_details_string(form_details, client_details):
    browser_info = client_details.get('browserName') or {}
    os_info = client_details.get('operatingSystem') or {}
    device_data = form_details.get('device', {})
    network_data = form_details.get('network', {})
    
    ip_address = form_details.get('ipAddress', 'Not available')
    browser = f"{_telemetry_label(browser_info, 'name')} v{_telemetry_label(browser_info, 'version')}"
    os = f"{_telemetry_label(os_info, 'name')} {_telemetry_label(os_info, 'version')} ({_telemetry_label(os_info, 'architecture')})"
    screen = f"{device_data.get('screenWidth', 'Unknown')}x{device_data.get('screenHeight', 'Unknown')}"
    connection = network_data.get('effectiveType', 'Unknown')
    
    # Format as a readable string
    device_string = (
        f"IP: {ip_address} | "
        f"Browser: {browser} | "
        f"OS: {os} | "
        f"Screen: {screen} | "
        f"Connection: {connection}"
    )
    
    return device_string



def safe_int(value, default=0):
    """Safely convert value to integer, return default if conversion fails"""
    try:
        if isinstance(value, str):
            # Remove any non-numeric characters except minus sign
            cleaned = re.sub(r'[^\d-]', '', value)
            if cleaned:
                return int(cleaned)
            return default
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        if setup_database():
            return jsonify({"status": "healthy", "database": "connected"})
        else:
            return jsonify({"status": "unhealthy", "database": "disconnected"}), 500
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500



# For IM Purchase Requisition
@app.route('/email-approve/<path:request_id>')
def email_approve(request_id):
    """Show email approval page"""
    try:
        originl_req_id = decrypt(str(request_id))
        requisition = get_purchase_request_by_id(originl_req_id)
        
        if not requisition:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Purchase Requisition Not Found</h2>
            <p>The requested purchase requisition could not be found.</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = requisition.get("Emaill Status")
        Status = requisition.get("Status")

        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This purchase requisition is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
    
        # Check if Status is 2 with defined Email_status values - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status.lower() == "sent to hod for approval":
                if is_email_otp_required():
                    hod_email = _im_hod_approver_email(requisition)
                    if not hod_email:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this requisition.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_IM_HOD, "approve", originl_req_id, cache_identity=hod_email
                    )
                    save_otp_in_session(hod_email, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_email(
                            hod_email, "IM Purchase Requisition", "approve", otp, originl_req_id, ttl_rem
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "im_requisition_email_approve.html",
                    req_no=originl_req_id,
                    employee_name=requisition.get("Employee Name", ""),
                    department=requisition.get("Indenting Department", ""),
                    description=requisition.get("Posting Description", ""),
                    **(
                        {
                            "otp_sent_to": hod_email,
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )

            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase requisition has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 3000);</script>
        </body></html>
        """)

@app.route('/email-reject/<path:request_id>')
def email_reject(request_id):
    """Show email rejection page"""
    try:
        originl_req_id = decrypt(str(request_id))
        requisition = get_purchase_request_by_id(originl_req_id)
        
        if not requisition:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Purchase Requisition Not Found</h2>
            <p>The requested purchase requisition could not be found.</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)

        Email_status = requisition.get("Emaill Status")
        Status = requisition.get("Status")

        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This purchase requisition is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
        
        # Check if Status is 2 with defined Email_status values - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status.lower() == "sent to hod for approval":
                if is_email_otp_required():
                    hod_email = _im_hod_approver_email(requisition)
                    if not hod_email:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this requisition.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_IM_HOD, "reject", originl_req_id, cache_identity=hod_email
                    )
                    save_otp_in_session(hod_email, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_email(
                            hod_email, "IM Purchase Requisition", "reject", otp, originl_req_id, ttl_rem
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "im_requisition_email_reject.html",
                    req_no=originl_req_id,
                    employee_name=requisition.get("Employee Name", ""),
                    department=requisition.get("Indenting Department", ""),
                    description=requisition.get("Posting Description", ""),
                    **(
                        {
                            "otp_sent_to": hod_email,
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase requisition has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 3000);</script>
        </body></html>
        """)

@app.route('/email-process-approval/<path:request_id>', methods=['POST'])
def email_process_approval(request_id):
    """Process email approval/rejection"""
    try:
        action = request.form.get('action')
        reason = request.form.get('reason', '')
        form_details_str = request.form.get('formDetails', '{}')
        form_details = json.loads(form_details_str)

        client_details_str = request.form.get('clientmachinedetails', '{}')
        client_details = json.loads(client_details_str)

        device_string = format_device_details_string(form_details, client_details)
        
        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]
        
        
        if not action:
            raise ValueError('Action is required')
        
        if action == 'reject' and not reason.strip():
            raise ValueError('Rejection reason is required')
        
        # Get client IP from request headers
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        if client_ip and client_ip != '127.0.0.1':
            # If multiple IPs in X-Forwarded-For, take the first one
            client_ip = client_ip.split(',')[0].strip()
        
        # Only collect approver's client-side machine details
        approver_machine_details = {
            "ts": datetime.now().strftime("%Y%m%d%H%M"),
            "ip": client_ip if client_ip and client_ip != '127.0.0.1' else "0.0.0.0"
        }
        
        requisition = get_purchase_request_by_id(request_id)
        if not requisition:
            print(f"Requisition not found for ID: {request_id}")
        
        # Check if request is already processed
        if is_request_already_processed(request_id):
            current_status = requisition.get('status', 'Unknown')
            return render_template_string(f"""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2 style="color: #ffc107;">⚠️ Request Already Processed</h2>
            <p>This purchase requisition has already been processed.</p>
            <p><strong>Current Status:</strong> {current_status}</p>
            <p>No further action was taken.</p>
            <script>setTimeout(function(){{window.close();}}, 5000);</script>
            </body></html>
            """)
        
        hod_email = None
        if is_email_otp_required():
            entered_otp = request.form.get("otp", "").strip()
            if not entered_otp:
                return jsonify({"success": False, "message": "OTP is required.", "status": "Error"}), 400
            hod_email = _im_hod_approver_email(requisition)
            if not hod_email:
                return jsonify({"success": False, "message": "Approver email not found for this request.", "status": "Error"}), 400
            ok_otp, otp_msg, otp_reason = verify_otp_from_session(hod_email, request_id, entered_otp)
            if not ok_otp:
                err = {"success": False, "message": otp_msg, "status": "Error"}
                if otp_reason:
                    err["reason"] = otp_reason
                return jsonify(err), 400

        approver_name = requisition.get('Send for Approval', '')
        current_time = datetime.now()

        approval_data = None
        if action == 'approve':
            approval_data = {
                "approver_name": approver_name,
                "approved_date": current_time.date(),
                "approved_time": current_time.time(),
            }
        
        if action == 'approve':
            current_time = datetime.now()
            success = update_purchase_request_status_with_text(
                request_id,
                int_status=1,  # Approved
                text_status="Approved",
                reason=reason if reason.strip() else None,
                user_data=device_string,
                timestemp=current_time,
                approval_data=approval_data
            )
        elif action == 'reject':
            success = update_purchase_request_status_with_text(
                request_id,
                int_status=0,  # Rejected  
                text_status=f"Rejected",
                reason=reason,
                user_data=device_string,
                timestemp=current_time,
            )
        else:
            raise ValueError('Invalid action')
        
        if not success:
            raise ValueError('Failed to update purchase requisition status')
        
        if is_email_otp_required() and hod_email:
            clear_pending_otp(CHANNEL_IM_HOD, action, request_id, cache_identity=hod_email)
        response_data = {"success": True, "req_id": request_id, "reason": reason, "client_details": client_details, "approver_machine_details": approver_machine_details}

        return jsonify(response_data)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2 style="color: #dc3545;">❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <button onclick="history.back();" style="padding: 10px 20px; margin-top: 20px;">Go Back</button>
        </body></html>
        """)







#IM Purchase Accountant Approval and Rejection Part
@app.route('/email-accountant-approve/<path:request_id>')
def email_accountant_approve(request_id):
    """Show email approval page for accountants"""
    try:
        originl_req_id = decrypt(str(request_id))
        requisition = get_purchase_request_by_id(originl_req_id)
        
        if not requisition:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Purchase Requisition Not Found</h2>
            <p>The requested purchase requisition could not be found.</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = requisition.get("Emaill Status")
        Status = requisition.get("Status")

        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This purchase requisition is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
    
        # Check if Status is 2 with pending by accountants status - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status and Email_status.lower() == "pending by accountants":
                if is_email_otp_required():
                    acc1 = requisition.get("Account dept Approver 1") or ""
                    acc2 = requisition.get("Account dept Approver 2") or ""
                    scope = accountant_otp_scope(acc1, acc2)
                    recipients = [e for e in (acc1, acc2) if e and str(e).strip()]
                    if not scope or not recipients:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No accountant approver email is configured for this requisition.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_IM_ACCOUNTANT, "approve", originl_req_id, cache_identity=None
                    )
                    save_otp_in_session(scope, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_emails_to_many(
                            recipients,
                            "IM Purchase (Accountant)",
                            "approve",
                            otp,
                            originl_req_id,
                            ttl_rem,
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "email_accountant_approve.html",
                    req_no=originl_req_id,
                    employee_name=requisition.get("Employee Name", ""),
                    department=requisition.get("Indenting Department", ""),
                    description=requisition.get("Posting Description", ""),
                    **(
                        {
                            "otp_sent_to": ", ".join(recipients),
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )

            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase requisition has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 3000);</script>
        </body></html>
        """)

@app.route('/email-accountant-reject/<path:request_id>')
def email_accountant_reject(request_id):
    """Show email rejection page for accountants"""
    try:
        originl_req_id = decrypt(str(request_id))
        requisition = get_purchase_request_by_id(originl_req_id)
        
        if not requisition:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Purchase Requisition Not Found</h2>
            <p>The requested purchase requisition could not be found.</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)

        Email_status = requisition.get("Emaill Status")
        Status = requisition.get("Status")

        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This purchase requisition is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
        
        # Check if Status is 2 with pending by accountants status - Show rejection template
        elif Status == 2 or Status == "2":
            if Email_status and Email_status.lower() == "pending by accountants":
                if is_email_otp_required():
                    acc1 = requisition.get("Account dept Approver 1") or ""
                    acc2 = requisition.get("Account dept Approver 2") or ""
                    scope = accountant_otp_scope(acc1, acc2)
                    recipients = [e for e in (acc1, acc2) if e and str(e).strip()]
                    if not scope or not recipients:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No accountant approver email is configured for this requisition.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_IM_ACCOUNTANT, "reject", originl_req_id, cache_identity=None
                    )
                    save_otp_in_session(scope, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_emails_to_many(
                            recipients,
                            "IM Purchase (Accountant)",
                            "reject",
                            otp,
                            originl_req_id,
                            ttl_rem,
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "email_accountant_reject.html",
                    req_no=originl_req_id,
                    employee_name=requisition.get("Employee Name", ""),
                    department=requisition.get("Indenting Department", ""),
                    description=requisition.get("Posting Description", ""),
                    **(
                        {
                            "otp_sent_to": ", ".join(recipients),
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase requisition has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 3000);</script>
        </body></html>
        """)

@app.route('/email-process-accountant-approval/<path:request_id>', methods=['POST'])
def email_process_accountant_approval(request_id):
    """Process accountant email approval/rejection"""
    try:
        action = request.form.get('action')
        reason = request.form.get('reason', '')
        form_details_str = request.form.get('formDetails', '{}')
        form_details = json.loads(form_details_str)

        client_details_str = request.form.get('clientmachinedetails', '{}')
        client_details = json.loads(client_details_str)

        device_string = format_device_details_string(form_details, client_details)
        
        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]
        
        
        if not action:
            raise ValueError('Action is required')
        
        if action == 'reject' and not reason.strip():
            raise ValueError('Rejection reason is required')
        
        # Get client IP from request headers
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        if client_ip and client_ip != '127.0.0.1':
            # If multiple IPs in X-Forwarded-For, take the first one
            client_ip = client_ip.split(',')[0].strip()
        
        # Only collect accountant's client-side machine details
        accountant_machine_details = {
            "ts": datetime.now().strftime("%Y%m%d%H%M"),
            "ip": client_ip if client_ip and client_ip != '127.0.0.1' else "0.0.0.0"
        }
        
        requisition = get_purchase_request_by_id(request_id)
        if not requisition:
            print(f"Requisition not found for ID: {request_id}")

        if is_email_otp_required():
            entered_otp = request.form.get("otp", "").strip()
            if not entered_otp:
                return jsonify({"success": False, "message": "OTP is required.", "error": "OTP is required."}), 400
            acc1 = requisition.get("Account dept Approver 1") or ""
            acc2 = requisition.get("Account dept Approver 2") or ""
            scope = accountant_otp_scope(acc1, acc2)
            if not scope:
                return jsonify({"success": False, "message": "Accountant approver emails not configured.", "error": "Accountant approver emails not configured."}), 400
            ok_otp, otp_msg, otp_reason = verify_otp_from_session(scope, request_id, entered_otp)
            if not ok_otp:
                err = {"success": False, "message": otp_msg, "error": otp_msg}
                if otp_reason:
                    err["reason"] = otp_reason
                return jsonify(err), 400

        # Check if request is already processed
        email_status = requisition.get('Emaill Status', '')
        if email_status and email_status.lower() in ['approved by acc', 'rejected by acc']:
            current_status = email_status
            return render_template_string(f"""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2 style="color: #ffc107;">⚠️ Request Already Processed</h2>
            <p>This purchase requisition has already been processed by accountants.</p>
            <p><strong>Current Status:</strong> {current_status}</p>
            <p>No further action was taken.</p>
            <script>setTimeout(function(){{window.close();}}, 5000);</script>
            </body></html>
            """)
        
        # Update status for accountant approval/rejection

        
        engine = get_engine()
        with engine.connect() as connection:
            if action == 'approve':
                update_query = text(f"""
                    UPDATE {PURCHASE_REQ_TABLE}
                    SET [Emaill Status] = 'approved by acc',
                        [Reason] = :reason,
                        [Approved By Account Dept_] = 1,
                        [User Data] = :user_data,
                        [Timestamps] = :timestemp
                    WHERE [No_] = :request_id
                """)
                params = {
                    "request_id": str(request_id),
                    "reason": reason if reason.strip() else "None",
                    "user_data": device_string,
                    "timestemp": current_time
                }
            elif action == 'reject':
                update_query = text(f"""
                    UPDATE {PURCHASE_REQ_TABLE}
                    SET [Emaill Status] = 'pending',
                        [Status] = 0,
                        [Reason] = :reason,
                        [Approved By Account Dept_] = 0,
                        [User Data] = :user_data,
                        [Timestamps] = :timestemp
                    WHERE [No_] = :request_id
                """)
                params = {
                    "request_id": str(request_id),
                    "reason": reason,
                    "user_data": device_string,
                    "timestemp": current_time
                }
            else:
                raise ValueError('Invalid action')
            
            result = connection.execute(update_query, params)
            connection.commit()
            
            if result.rowcount == 0:
                error_msg = f'Failed to update purchase requisition status. No rows updated for request_id: {request_id}, action: {action}'
                print(f"[ERROR] {error_msg}")
                print(f"[DEBUG] Current email status: {email_status}")
                raise ValueError(error_msg)
        
        # Send response email to creator after accountant approval/rejection
        try:
            # Get updated requisition data
            updated_requisition = get_purchase_request_by_id(request_id)
            if updated_requisition:
                response_status = "Approved by Accountant" if action == 'approve' else "Rejected by Accountant"
                print(f"[DEBUG] Sending accountant response email to creator for request_id: {request_id}, status: {response_status}")
                email_sent = send_accountant_response_email_to_creator(updated_requisition, response_status, reason)
                print(f"[DEBUG] Accountant response email sent: {email_sent}")
            else:
                print(f"[ERROR] Could not fetch updated requisition for request_id: {request_id}")
        except Exception as e:
            print(f"ERROR: Failed to send accountant response email to creator: {e}")
            import traceback
            traceback.print_exc()
            # Don't fail the whole request if email sending fails
        
        if is_email_otp_required():
            clear_pending_otp(CHANNEL_IM_ACCOUNTANT, action, request_id, cache_identity=None)
        response_data = {"success": True, "req_id": request_id, "reason": reason, "client_details": client_details, "accountant_machine_details": accountant_machine_details}

        return jsonify(response_data)
        
    except Exception as e:
        print(f"[ERROR] Accountant approval/rejection error: {str(e)}")
        import traceback
        traceback.print_exc()
        # Return JSON error response for better handling in the template
        return jsonify({"success": False, "error": str(e)}), 400







#Route for Purchase Data
@app.route('/purchase-email-approve/<path:request_id>')
def purchase_email_approve(request_id):
    try:
        originl_req_id = decrypt(str(request_id))
        purchase_data = get_purchase_request_data_by_id(request_id=originl_req_id)
        
        if not purchase_data:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Purchase Request Not Found</h2>
            <p>The requested purchase data could not be found....</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = purchase_data["email_data"].get("Emaill Status")
        Status = purchase_data["header_data"].get("Status")
        
        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This purchase order is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
        
        # Check if Status is 2 with defined Email_status values - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status.lower() == "pending" or Email_status.lower() == "sent for approval" or Email_status.lower() == "reminder sent":
                if is_email_otp_required():
                    email_row = purchase_data.get("email_data") or {}
                    po_approver = (email_row.get("Approver Mail ID") or "").strip()
                    if not po_approver:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this purchase order.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_PURCHASE_ORDER, "approve", originl_req_id, cache_identity=po_approver
                    )
                    save_otp_in_session(po_approver, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_email(
                            po_approver, "Purchase Order", "approve", otp, originl_req_id, ttl_rem
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "purchase_email_approve.html",
                    req_no=originl_req_id,
                    buy_from_vendor_name=purchase_data["header_data"].get("Buy-from Vendor Name"),
                    ship_to_name=purchase_data["header_data"].get("Ship-to Name"),
                    order_date=purchase_data["header_data"].get("Order Date"),
                    **(
                        {
                            "otp_sent_to": po_approver,
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
            
        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase order has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
        
        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase order has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 10000);</script>
        </body></html>
        """)

@app.route('/purchase-email-reject/<path:request_id>')
def purchase_email_reject(request_id):
    try:
        originl_req_id = decrypt(str(request_id))
        purchase_data = get_purchase_request_data_by_id(request_id=originl_req_id)
        
        if not purchase_data:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Purchase Request Not Found</h2>
            <p>The requested purchase data could not be found....</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = purchase_data["email_data"].get("Emaill Status")
        Status = purchase_data["header_data"].get("Status")
        
        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This purchase order is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)

        # Check if Status is 2 with defined Email_status values - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status.lower() == "pending" or Email_status.lower() == "sent for approval" or Email_status.lower() == "reminder sent":
                if is_email_otp_required():
                    email_row = purchase_data.get("email_data") or {}
                    po_approver = (email_row.get("Approver Mail ID") or "").strip()
                    if not po_approver:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this purchase order.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_PURCHASE_ORDER, "reject", originl_req_id, cache_identity=po_approver
                    )
                    save_otp_in_session(po_approver, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_email(
                            po_approver, "Purchase Order", "reject", otp, originl_req_id, ttl_rem
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "purchase_email_reject.html",
                    req_no=originl_req_id,
                    buy_from_vendor_name=purchase_data["header_data"].get("Buy-from Vendor Name"),
                    ship_to_name=purchase_data["header_data"].get("Ship-to Name"),
                    order_date=purchase_data["header_data"].get("Order Date"),
                    **(
                        {
                            "otp_sent_to": po_approver,
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase requisition has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This purchase order has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
        
        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase order has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 10000);</script>
        </body></html>
        """)

@app.route('/purchase-email-process-approval-and-reject/<path:request_id>', methods=['POST'])
def purchase_email_process_approval_and_reject(request_id):
    try:
        action = request.form.get('action')
        reason = request.form.get('reason', '')
        form_details_str = request.form.get('formDetails', '{}')
        form_details = json.loads(form_details_str)

        client_details_str = request.form.get('clientmachinedetails', '{}')
        client_details = json.loads(client_details_str)

        datetime_str = form_details.get('datetime')
        ip_address = form_details.get('ipAddress')

        browser_data = form_details.get('browser') or {}
        browser_info = client_details.get('browserName') or {}
        browser_name = browser_data.get('userAgent')
        browser_name_detail = browser_info.get('name') or browser_data.get('appName')
        browser_version = browser_info.get('version') or browser_data.get('appVersion')

        os_data = form_details.get('os') or {}
        os_info = client_details.get('operatingSystem') or {}
        os_name = os_data.get('name') or os_info.get('name')
        os_version = os_data.get('version') or os_info.get('version')
        os_architecture = os_info.get('architecture')

        device_data = form_details.get('device', {})
        screen_width = device_data.get('screenWidth')
        screen_height = device_data.get('screenHeight')
        is_touch_device = device_data.get('isTouchDevice')
        client_machine_details = request.form.get('client_machine_details', '{}')

        device_string = format_device_details_string(form_details, client_details)

        print(f"Action: {action}")
        print(f"Reason: {reason}")
        print(f"DateTime: {datetime_str}")
        print(f"IP Address: {ip_address}")
        print(f"Browser: {_telemetry_scalar(browser_name_detail)} v{_telemetry_scalar(browser_version)}")
        print(f"OS: {_telemetry_scalar(os_name)} {_telemetry_scalar(os_version)} ({_telemetry_scalar(os_architecture)})")
        print(f"Screen: {screen_width}x{screen_height}")
        print(f"Touch Device: {is_touch_device}")
        print(f"Foormatted Device String: {device_string}")
        
        if not action:
            raise ValueError('Action is required')
        
        if action == 'reject' and not reason.strip():
            raise ValueError('Rejection reason is required')

        po_approver = None
        if is_email_otp_required():
            entered_otp = request.form.get("otp", "").strip()
            if not entered_otp:
                return jsonify({"status": "Error", "success": False, "message": "OTP is required."}), 400

            purchase_for_otp = get_purchase_request_data_by_id(request_id=request_id)
            if not purchase_for_otp or not purchase_for_otp.get("email_data"):
                return jsonify({"status": "Error", "success": False, "message": "Purchase request not found."}), 400
            po_approver = (purchase_for_otp["email_data"].get("Approver Mail ID") or "").strip()
            if not po_approver:
                return jsonify({"status": "Error", "success": False, "message": "Approver email not found."}), 400
            ok_otp, otp_msg, otp_reason = verify_otp_from_session(po_approver, request_id, entered_otp)
            if not ok_otp:
                err = {"status": "Error", "success": False, "message": otp_msg}
                if otp_reason:
                    err["reason"] = otp_reason
                return jsonify(err), 400
        
        # Get client IP from request headers
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        if client_ip and client_ip != '127.0.0.1':
            # If multiple IPs in X-Forwarded-For, take the first one
            client_ip = client_ip.split(',')[0].strip()
        
        # Parse client machine details and update with real IP
        client_details = json.loads(client_machine_details) if client_machine_details else {}
        if client_ip and client_ip != '127.0.0.1':
            client_details['clientIP'] = client_ip
        
        # Only collect approver's client-side machine details
        approver_machine_details = {
            "ts": datetime.now().strftime("%Y%m%d%H%M"),
            "ip": client_ip if client_ip and client_ip != '127.0.0.1' else "0.0.0.0"
        }
        
        # Get full timestamp
        full_timestamp = get_full_timestamp()

        # Updating Email table after sending Email
        if action == 'reject':
            email_status = "Rejected"
            status = 0  # Rejected
        elif action == 'approve':
            email_status = "Approved"
            status = 1  # Approved
        else:
            raise ValueError('Invalid action')
        sql = f"""
              UPDATE {PURCHASE_EMAIL_TABLE}
              SET [Emaill Status] = ?,
                  [Reason] = ?,
                  [Timestamps] = ?,
                  [User Data] = ?
              WHERE [No_] = ?
              """       
        sql_status_update = f"""
              UPDATE {PURCHASE_HEADER_MAIN}
              SET [Status] = ?
              WHERE [No_] = ?
              """  
        reason_val = reason.strip() if reason and reason.strip() else "None"
        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]
        try:
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, email_status, reason_val, current_time, device_string, request_id)
                cursor.execute(sql_status_update, status, request_id)
                approval_status = 3 if action == 'reject' else 4
                sql_approval_update = f"""
                    UPDATE {APPROVAL_ENTRY_TABLE}
                    SET [Status] = ?
                    WHERE [Document No_] = ?
                      AND [Entry No_] = (
                        SELECT MAX([Entry No_])
                        FROM {APPROVAL_ENTRY_TABLE}
                        WHERE [Document No_] = ?
                      )
                """
                cursor.execute(sql_approval_update, approval_status, request_id, request_id)


                # Delete restriction records if action is not reject
                if action != 'reject':
                    # Get distinct Record IDs from APPROVAL_ENTRY_TABLE
                    sql_get_record_ids = f"""
                        SELECT DISTINCT [Record ID to Approve]
                        FROM {APPROVAL_ENTRY_TABLE}
                        WHERE [Document No_] = ?
                        AND [Record ID to Approve] IS NOT NULL
                    """
                    cursor.execute(sql_get_record_ids, request_id)
                    record_ids = [row[0] for row in cursor.fetchall()]

                    # Delete rows from RESTRICTION_RECORDS_TABLE for each Record ID
                    if record_ids:
                        try:
                            # Use parameterized query with IN clause
                            placeholders = ','.join('?' * len(record_ids))
                            sql_delete_restrictions = f"""
                                DELETE FROM {RESTRICTION_RECORDS_TABLE}
                                WHERE [Record ID] IN ({placeholders})
                            """
                            cursor.execute(sql_delete_restrictions, *record_ids)
                            rows_deleted = cursor.rowcount
                            if rows_deleted > 0:
                                print(f"Deleted {rows_deleted} restriction record(s) for Document No: {request_id}, Record IDs: {record_ids}")
                            else:
                                print(f"No restriction records found to delete for Document No: {request_id}, Record IDs: {record_ids}")
                        except Exception as delete_error:
                            print(f"Warning: Could not delete restriction records for Document No: {request_id}. Error: {str(delete_error)}. Continuing with next steps...")

                conn.commit()

            if action == 'approve':
                print(f"Triggering order release API for request_id: {request_id}")

                scheduler.add_job(
                    delayed_po_api_call,
                    'date',
                    run_date=datetime.now() + timedelta(minutes=1),  # Schedule to run after 1 minute
                    args=[request_id, 1]  # Initial attempt
                )

            send_purchase_response_email_to_creator(request_id, email_status, reason)

        except Exception as insert_error:
            print(f"Error updating email status: {insert_error}")
            return jsonify({"status": "Error", "success": False, "error": str(insert_error)}), 500

        if is_email_otp_required() and po_approver:
            clear_pending_otp(CHANNEL_PURCHASE_ORDER, action, request_id, cache_identity=po_approver)
        response_data = {"status": email_status, "success": True, "req_id": request_id, "timestamp": full_timestamp, "reason": reason, "client_details": client_details, "approver_machine_details": approver_machine_details}

        return jsonify(response_data)
        
    except Exception as e:
        response_data = {"status": "Error", "success": False, "error": str(e)}
        return jsonify(response_data)









#Routes for Sales Order
@app.route('/sales-email-approve/<path:request_id>')
def sales_email_approve(request_id):
    """Show email approval page"""
    try:
        originl_req_id = decrypt(str(request_id))
        sales_order_data = get_sales_order_data_by_id(request_id=originl_req_id)

        if not sales_order_data:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Sales Order Not Found</h2>
            <p>The requested sales order data could not be found....</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = sales_order_data["email_data"].get("Emaill Status")
        Status = sales_order_data["header_data"].get("Status")

        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This sales order is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)


        # Check if Status is 2 with defined Email_status values - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status.lower() == "pending" or Email_status.lower() == "sent for approval" or Email_status.lower() == "reminder sent":
                if is_email_otp_required():
                    em = sales_order_data.get("email_data") or {}
                    so_approver = (em.get("Approver Mail ID") or "").strip()
                    if not so_approver:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this sales order.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_SALES_ORDER, "approve", originl_req_id, cache_identity=so_approver
                    )
                    save_otp_in_session(so_approver, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_email(
                            so_approver, "Sales Order", "approve", otp, originl_req_id, ttl_rem
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "sales_order_email_approve.html",
                    req_no=originl_req_id,
                    buy_from_vendor_name=sales_order_data["header_data"].get("Buy-from Vendor Name"),
                    customer_name=sales_order_data["header_data"].get("Sell-to Customer Name"),
                    order_date=sales_order_data["header_data"].get("Order Date"),
                    **(
                        {
                            "otp_sent_to": so_approver,
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This sales order has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This sales order has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This sales order has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 10000);</script>
        </body></html>
        """)

@app.route('/sales-email-reject/<path:request_id>')
def sales_email_reject(request_id):
    try:
        originl_req_id = decrypt(str(request_id))
        sales_order_data = get_sales_order_data_by_id(request_id=originl_req_id)

        if not sales_order_data:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Sales Order Not Found</h2>
            <p>The requested sales order data could not be found....</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = sales_order_data["email_data"].get("Emaill Status")
        Status = sales_order_data["header_data"].get("Status")

        # Check if Status is 0 - Data is still in process
        if Status == 0 or Status == "0":
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This sales order is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)


        # Check if Status is 2 with defined Email_status values - Show approval template
        elif Status == 2 or Status == "2":
            if Email_status.lower() == "pending" or Email_status.lower() == "sent for approval" or Email_status.lower() == "reminder sent":
                if is_email_otp_required():
                    em = sales_order_data.get("email_data") or {}
                    so_approver = (em.get("Approver Mail ID") or "").strip()
                    if not so_approver:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this sales order.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp, ttl_rem = get_or_issue_pending_otp(
                        CHANNEL_SALES_ORDER, "reject", originl_req_id, cache_identity=so_approver
                    )
                    save_otp_in_session(so_approver, originl_req_id, otp, ttl_seconds=ttl_rem)
                    if should_send_otp:
                        otp_res = send_otp_email(
                            so_approver, "Sales Order", "reject", otp, originl_req_id, ttl_rem
                        )
                        if not otp_res.get("success"):
                            return _render_otp_send_failed()
                return render_template(
                    "sales_order_email_reject.html",
                    req_no=originl_req_id,
                    buy_from_vendor_name=sales_order_data["header_data"].get("Buy-from Vendor Name"),
                    customer_name=sales_order_data["header_data"].get("Sell-to Customer Name"),
                    order_date=sales_order_data["header_data"].get("Order Date"),
                    **(
                        {
                            "otp_sent_to": so_approver,
                            "otp_expiry_seconds": ttl_rem,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This sales order has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        elif Status == 3 or Status == "3" or Status == 1 or Status == "1":
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This sales order has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)

        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This sales order has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 10000);</script>
        </body></html>
        """)

@app.route('/sales-order-email-process-approve-reject/<path:request_id>', methods=['POST'])
def sales_order_email_process_approval_and_reject(request_id):
    """Process email approval/rejection"""
    try:
        action = request.form.get('action')
        reason = request.form.get('reason', '')
        form_details_str = request.form.get('formDetails', '{}')
        form_details = json.loads(form_details_str)

        client_details_str = request.form.get('clientmachinedetails', '{}')
        client_details = json.loads(client_details_str)

        datetime_str = form_details.get('datetime')
        ip_address = form_details.get('ipAddress')

        browser_data = form_details.get('browser') or {}
        browser_info = client_details.get('browserName') or {}
        browser_name = browser_data.get('userAgent')
        browser_name_detail = browser_info.get('name') or browser_data.get('appName')
        browser_version = browser_info.get('version') or browser_data.get('appVersion')

        os_data = form_details.get('os') or {}
        os_info = client_details.get('operatingSystem') or {}
        os_name = os_data.get('name') or os_info.get('name')
        os_version = os_data.get('version') or os_info.get('version')
        os_architecture = os_info.get('architecture')

        device_data = form_details.get('device', {})
        screen_width = device_data.get('screenWidth')
        screen_height = device_data.get('screenHeight')
        is_touch_device = device_data.get('isTouchDevice')
        client_machine_details = request.form.get('client_machine_details', '{}')

        device_string = format_device_details_string(form_details, client_details)

        print(f"Action: {action}")
        print(f"Reason: {reason}")
        print(f"DateTime: {datetime_str}")
        print(f"IP Address: {ip_address}")
        print(f"Browser: {_telemetry_scalar(browser_name_detail)} v{_telemetry_scalar(browser_version)}")
        print(f"OS: {_telemetry_scalar(os_name)} {_telemetry_scalar(os_version)} ({_telemetry_scalar(os_architecture)})")
        print(f"Screen: {screen_width}x{screen_height}")
        print(f"Touch Device: {is_touch_device}")
        print(f"Foormatted Device String: {device_string}")
        
        if not action:
            raise ValueError('Action is required')
        
        if action == 'reject' and not reason.strip():
            raise ValueError('Rejection reason is required')

        so_approver = None
        if is_email_otp_required():
            entered_otp = request.form.get("otp", "").strip()
            if not entered_otp:
                return jsonify({"status": "Error", "success": False, "message": "OTP is required."}), 400

            so_data_otp = get_sales_order_data_by_id(request_id=request_id)
            if not so_data_otp or not so_data_otp.get("email_data"):
                return jsonify({"status": "Error", "success": False, "message": "Sales order not found."}), 400
            so_approver = (so_data_otp["email_data"].get("Approver Mail ID") or "").strip()
            if not so_approver:
                return jsonify({"status": "Error", "success": False, "message": "Approver email not found."}), 400
            ok_otp, otp_msg, otp_reason = verify_otp_from_session(so_approver, request_id, entered_otp)
            if not ok_otp:
                err = {"status": "Error", "success": False, "message": otp_msg}
                if otp_reason:
                    err["reason"] = otp_reason
                return jsonify(err), 400
        
        # Get client IP from request headers
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        if client_ip and client_ip != '127.0.0.1':
            # If multiple IPs in X-Forwarded-For, take the first one
            client_ip = client_ip.split(',')[0].strip()
        
        # Parse client machine details and update with real IP
        client_details = json.loads(client_machine_details) if client_machine_details else {}
        if client_ip and client_ip != '127.0.0.1':
            client_details['clientIP'] = client_ip
        
        # Only collect approver's client-side machine details
        approver_machine_details = {
            "ts": datetime.now().strftime("%Y%m%d%H%M"),
            "ip": client_ip if client_ip and client_ip != '127.0.0.1' else "0.0.0.0"
        }
        
        # Get full timestamp
        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

        # Updating Email table after sending Email
        if action == 'reject':
            email_status = "Rejected"
            status = 0  # Rejected
        else:
            email_status = "Approved"
            status = 1  # Approved
        try:
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                approval_status = 3 if action == 'reject' else 4
                sql_approval_update = f"""
                    UPDATE {APPROVAL_ENTRY_TABLE}
                    SET [Status] = ?
                    WHERE [Document No_] = ?
                      AND [Entry No_] = (
                        SELECT MAX([Entry No_])
                        FROM {APPROVAL_ENTRY_TABLE}
                        WHERE [Document No_] = ?
                      )
                """
                cursor.execute(sql_approval_update, approval_status, request_id, request_id)

                # Perform SQL UPDATE before sending email
                sql = f"""
                    UPDATE {SALES_ORDER_EMAIL_TABLE}
                    SET [Emaill Status] = ?,
                    [User Data] = ?,
                    [Timestamps] = ?,
                    [Reason] = ?
                    WHERE [No_] = ?
                """
                cursor.execute(sql, email_status, device_string, current_time, reason, request_id)
        
                sql_status_update = f"""
                  UPDATE {SALES_ORDER_MAIN}
                  SET [Status] = ?
                  WHERE [No_] = ?
                  """  
                cursor.execute(sql_status_update, status, request_id)

                # Delete restriction records if action is not reject
                if action != 'reject':
                    # Get distinct Record IDs from APPROVAL_ENTRY_TABLE
                    sql_get_record_ids = f"""
                        SELECT DISTINCT [Record ID to Approve]
                        FROM {APPROVAL_ENTRY_TABLE}
                        WHERE [Document No_] = ?
                        AND [Record ID to Approve] IS NOT NULL
                    """
                    cursor.execute(sql_get_record_ids, request_id)
                    record_ids = [row[0] for row in cursor.fetchall()]
                    
                    # Delete rows from RESTRICTION_RECORDS_TABLE for each Record ID
                    if record_ids:
                        try:
                            # Use parameterized query with IN clause
                            placeholders = ','.join('?' * len(record_ids))
                            sql_delete_restrictions = f"""
                                DELETE FROM {RESTRICTION_RECORDS_TABLE}
                                WHERE [Record ID] IN ({placeholders})
                            """
                            cursor.execute(sql_delete_restrictions, *record_ids)
                            rows_deleted = cursor.rowcount
                            if rows_deleted > 0:
                                print(f"Deleted {rows_deleted} restriction record(s) for SO Document No: {request_id}, Record IDs: {record_ids}")
                            else:
                                print(f"No restriction records found to delete for SO Document No: {request_id}, Record IDs: {record_ids}")
                        except Exception as delete_error:
                            print(f"Warning: Could not delete restriction records for SO Document No: {request_id}. Error: {str(delete_error)}. Continuing with next steps...")
                            
                conn.commit()

            if action != 'reject':
                print(f"Triggering order release API for request_id: {request_id}")

                scheduler.add_job(
                    delayed_so_api_call,
                    'date',
                    run_date=datetime.now() + timedelta(minutes=1),  # Schedule to run after 1 minute
                    args=[request_id, 1]  # Initial attempt
                )

            send_sales_order_response_email_to_customer(request_id, email_status, status, current_time, device_string, reason)

        except Exception as insert_error:
            print(f"Error updating email status: {insert_error}")
            return jsonify({"status": "Error", "success": False, "error": str(insert_error)}), 500

        if is_email_otp_required() and so_approver:
            clear_pending_otp(CHANNEL_SALES_ORDER, action, request_id, cache_identity=so_approver)
        response_data = {"status": email_status, "success": True, "req_id": request_id, "timestamp": current_time, "reason": reason, "client_details": client_details, "approver_machine_details": approver_machine_details}

        return jsonify(response_data)
        
    except Exception as e:
        response_data = {"status": "Error", "success": False, "error": str(e)}
        return jsonify(response_data)











#Routes for JOB CARD
@app.route('/job-card-email-approve/<path:request_id>')
def job_card_email_approve(request_id):
    """Show email approval page"""
    try:
        originl_req_id = decrypt(str(request_id))
        # job_card_data = get_job_card_data_by_id(request_id=request_id)
        job_card_data = get_job_card_data_by_id(request_id=originl_req_id)

        if not job_card_data:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Job Card Not Found</h2>
            <p>The requested job card data could not be found....</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = job_card_data["header_data"].get("Emaill Status")
        Status = job_card_data["secondary_data"].get("Status")
        TPT_Approval_Status = job_card_data["header_data"].get("TPT_Approval Status")
        Approved = job_card_data["header_data"].get("Approved")

        
        # Check if Status is 0 - Data is still in process
        if (int(TPT_Approval_Status) if TPT_Approval_Status is not None else None) == 0 and \
           (int(Approved) if Approved is not None else None) == 0:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This Job Card is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
            
        # Check if Status is 2 with defined Email_status values - Show approval template
        elif (int(TPT_Approval_Status) if TPT_Approval_Status is not None else None) == 1 and \
             (int(Approved) if Approved is not None else None) == 0:
            if Email_status.lower() == "pending" or Email_status.lower() == "sent for approval" or Email_status.lower() == "reminder sent":
                if is_email_otp_required():
                    approver_email = (job_card_data["header_data"].get("Approver Mail ID") or "").strip()
                    if not approver_email:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this job card.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp_email, otp_ttl_remaining = get_or_issue_pending_otp(
                        CHANNEL_JOB_CARD, "approve", originl_req_id, cache_identity=approver_email
                    )
                    save_otp_in_session(approver_email, originl_req_id, otp, ttl_seconds=otp_ttl_remaining)
                    otp_mail_result = {"success": True}
                    if should_send_otp_email:
                        otp_mail_result = send_otp_email(
                            to_email=approver_email,
                            order_type="Job Card",
                            action="approve",
                            otp=otp,
                            request_id=originl_req_id,
                            ttl_seconds=otp_ttl_remaining
                        )
                    if not otp_mail_result.get("success", True):
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ OTP Email Failed</h2>
                        <p>Could not send OTP email. Please try again later.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                return render_template(
                    "job_card_email_approve.html",
                    job_card_number=originl_req_id,
                    department=job_card_data["header_data"].get("Department Name"),
                    prepared_by=job_card_data["header_data"].get("PREPARED BY"),
                    objective=job_card_data["header_data"].get("OBJECTIVE OF JOB CARD"),
                    **(
                        {
                            "otp_sent_to": approver_email,
                            "otp_expiry_seconds": otp_ttl_remaining,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This JOB Card has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
                
                
        elif (int(TPT_Approval_Status) if TPT_Approval_Status is not None else None) == 2 and \
             (int(Approved) if Approved is not None else None) == 1:
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This JOB Card has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
            
        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This job card has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)

    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 10000);</script>
        </body></html>
        """)


@app.route('/job-card-email-reject/<path:request_id>')
def job_card_email_reject(request_id):
    """Show email approval page"""
    try:
        originl_req_id = decrypt(str(request_id))
        # job_card_data = get_job_card_data_by_id(request_id=request_id)
        job_card_data = get_job_card_data_by_id(request_id=originl_req_id)
        
        if not job_card_data:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>❌ Job Card Not Found</h2>
            <p>The requested job card data could not be found....</p>
            <script>setTimeout(function(){window.close();}, 3000);</script>
            </body></html>
            """)
        
        Email_status = job_card_data["header_data"].get("Emaill Status")
        Status = job_card_data["secondary_data"].get("Status")
        TPT_Approval_Status = job_card_data["header_data"].get("TPT_Approval Status")
        Approved = job_card_data["header_data"].get("Approved")
        
        # Check if Status is 0 - Data is still in process
        if (int(TPT_Approval_Status) if TPT_Approval_Status is not None else None) == 0 and \
           (int(Approved) if Approved is not None else None) == 0:
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⏳ Data is Still in Process</h2>
            <p>This Job Card is currently being processed.</p>
            <p>You will receive another approval email.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """)
            
        # Check if Status is 2 with defined Email_status values - Show approval template
        elif (int(TPT_Approval_Status) if TPT_Approval_Status is not None else None) == 1 and \
             (int(Approved) if Approved is not None else None) == 0:
            if Email_status.lower() == "pending" or Email_status.lower() == "sent for approval" or Email_status.lower() == "reminder sent":
                if is_email_otp_required():
                    jc_approver = job_card_data["header_data"].get("Approver Mail ID") or ""
                    jc_approver = str(jc_approver).strip()
                    if not jc_approver:
                        return render_template_string("""
                        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                        <h2>❌ Configuration Error</h2>
                        <p>No approver email is configured for this job card.</p>
                        <script>setTimeout(function(){window.close();}, 5000);</script>
                        </body></html>
                        """)
                    otp, should_send_otp_email, otp_ttl_remaining = get_or_issue_pending_otp(
                        CHANNEL_JOB_CARD, "reject", originl_req_id, cache_identity=jc_approver
                    )
                    save_otp_in_session(jc_approver, originl_req_id, otp, ttl_seconds=otp_ttl_remaining)
                    if should_send_otp_email:
                        otp_mail_result = send_otp_email(
                            to_email=jc_approver,
                            order_type="Job Card",
                            action="reject",
                            otp=otp,
                            request_id=originl_req_id,
                            ttl_seconds=otp_ttl_remaining,
                        )
                        if not otp_mail_result.get("success", True):
                            return render_template_string("""
                            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                            <h2>❌ OTP Email Failed</h2>
                            <p>Could not send OTP email. Please try again later.</p>
                            <script>setTimeout(function(){window.close();}, 5000);</script>
                            </body></html>
                            """)
                return render_template(
                    "job_card_email_reject.html",
                    job_card_number=originl_req_id,
                    department=job_card_data["header_data"].get("Department Name"),
                    prepared_by=job_card_data["header_data"].get("PREPARED BY"),
                    objective=job_card_data["header_data"].get("OBJECTIVE OF JOB CARD"),
                    **(
                        {
                            "otp_sent_to": jc_approver,
                            "otp_expiry_seconds": otp_ttl_remaining,
                        }
                        if is_email_otp_required()
                        else {}
                    ),
                )
            else:
                return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This JOB Card has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
                
                
        elif (int(TPT_Approval_Status) if TPT_Approval_Status is not None else None) == 2 and \
             (int(Approved) if Approved is not None else None) == 1:
            return render_template_string("""
                <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
                <h2>⚠️ Request Already Processed</h2>
                <p>This JOB Card has already been processed.</p>
                <p><strong>Current Status:</strong> {{ current_status }}</p>
                <p>No further action is required.</p>
                <script>setTimeout(function(){window.close();}, 5000);</script>
                </body></html>
                """, current_status=Email_status)
            
        else:
            # For any other Status value
            return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h2>⚠️ Invalid Status</h2>
            <p>This purchase order has an invalid or unexpected status.</p>
            <p><strong>Current Status:</strong> {{ current_status }}</p>
            <p>Please contact support for assistance.</p>
            <script>setTimeout(function(){window.close();}, 5000);</script>
            </body></html>
            """, current_status=Status)
        
    except Exception as e:
        return render_template_string(f"""
        <html><body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
        <h2>❌ Error</h2>
        <p>An error occurred: {str(e)}</p>
        <script>setTimeout(function(){{window.close();}}, 10000);</script>
        </body></html>
        """)


@app.route('/job-card-email-process-approve-reject/<path:request_id>', methods=['POST'])
def job_card_email_process_approval_and_reject(request_id):
    """Process email approval/rejection"""
    try:
        action = request.form.get('action')
        reason = request.form.get('reason', '')
        form_details_str = request.form.get('formDetails', '{}')
        form_details = json.loads(form_details_str)

        client_details_str = request.form.get('clientmachinedetails', '{}')
        client_details = json.loads(client_details_str)

        datetime_str = form_details.get('datetime')
        ip_address = form_details.get('ipAddress')

        browser_data = form_details.get('browser') or {}
        browser_info = client_details.get('browserName') or {}
        browser_name = browser_data.get('userAgent')
        browser_name_detail = browser_info.get('name') or browser_data.get('appName')
        browser_version = browser_info.get('version') or browser_data.get('appVersion')

        os_data = form_details.get('os') or {}
        os_info = client_details.get('operatingSystem') or {}
        os_name = os_data.get('name') or os_info.get('name')
        os_version = os_data.get('version') or os_info.get('version')
        os_architecture = os_info.get('architecture')

        device_data = form_details.get('device', {})
        screen_width = device_data.get('screenWidth')
        screen_height = device_data.get('screenHeight')

        device_string = format_device_details_string(form_details, client_details)

        print(f"Action: {action}")
        print(f"Reason: {reason}")
        print(f"DateTime: {datetime_str}")
        print(f"IP Address: {ip_address}")
        print(f"Browser: {_telemetry_scalar(browser_name_detail)} v{_telemetry_scalar(browser_version)}")
        print(f"OS: {_telemetry_scalar(os_name)} {_telemetry_scalar(os_version)} ({_telemetry_scalar(os_architecture)})")
        print(f"Screen: {screen_width}x{screen_height}")
        print(f"Foormatted Device String: {device_string}")
        
        if not action:
            raise ValueError('Action is required')
        
        if action == 'reject' and not reason.strip():
            raise ValueError('Rejection reason is required')
        

        approver_email = None
        if is_email_otp_required():
            entered_otp = request.form.get('otp', '').strip()
            if not entered_otp:
                return jsonify({
                    "status": "Error",
                    "success": False,
                    "message": "OTP is required."
                }), 400

            job_card_data = get_job_card_data_by_id(request_id=request_id)
            approver_email = (job_card_data["header_data"].get("Approver Mail ID") or "").strip()
            if not approver_email:
                return jsonify({"status": "Error", "success": False, "message": "Approver email not found."}), 400

            ok, message, otp_reason = verify_otp_from_session(approver_email, request_id, entered_otp)

            if not ok:
                err = {"status": "Error", "success": False, "message": message}
                if otp_reason:
                    err["reason"] = otp_reason
                return jsonify(err), 400

        
        # Get full timestamp
        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

        # Updating Email table after sending Email
        if action == 'reject':
            email_status = "Rejected"
            tpt_approval_status = 0
            approved = 0
        elif action == 'approve':
            email_status = "Approved"     
            tpt_approval_status = 2
            approved = 1
        else:
            raise ValueError('Invalid action')

        try:
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                approval_status = 3 if action == 'reject' else 4
                sql_approval_update = f"""
                    UPDATE {APPROVAL_ENTRY_TABLE}
                    SET [Status] = ?
                    WHERE [Document No_] = ?
                      AND [Entry No_] = (
                        SELECT MAX([Entry No_])
                        FROM {APPROVAL_ENTRY_TABLE}
                        WHERE [Document No_] = ?
                      )
                """
                cursor.execute(sql_approval_update, approval_status, request_id, request_id)
                # Perform SQL UPDATE before sending email
                sql = f"""
                    UPDATE {JOB_CARD_MAIN_TABLE}
                    SET [Emaill Status] = ?,
                    [TPT_Approval Status] = ?,
                    [Approved] = ?,
                    [User Data] = ?,
                    [Timestamps] = ?,
                    [Reason] = ?
                    WHERE [No_] = ?
                """
                cursor.execute(sql, email_status, tpt_approval_status, approved, device_string, current_time, reason, request_id)


                # Delete restriction records if action is not reject
                if action != 'reject':
                    # Get distinct Record IDs from APPROVAL_ENTRY_TABLE
                    sql_get_record_ids = f"""
                        SELECT DISTINCT [Record ID to Approve]
                        FROM {APPROVAL_ENTRY_TABLE}
                        WHERE [Document No_] = ?
                        AND [Record ID to Approve] IS NOT NULL
                    """
                    cursor.execute(sql_get_record_ids, request_id)
                    record_ids = [row[0] for row in cursor.fetchall()]
                    
                    # Delete rows from RESTRICTION_RECORDS_TABLE for each Record ID
                    if record_ids:
                        try:
                            # Use parameterized query with IN clause
                            placeholders = ','.join('?' * len(record_ids))
                            sql_delete_restrictions = f"""
                                DELETE FROM {RESTRICTION_RECORDS_TABLE}
                                WHERE [Record ID] IN ({placeholders})
                            """
                            cursor.execute(sql_delete_restrictions, *record_ids)
                            rows_deleted = cursor.rowcount
                            if rows_deleted > 0:
                                print(f"Deleted {rows_deleted} restriction record(s) for SO Document No: {request_id}, Record IDs: {record_ids}")
                            else:
                                print(f"No restriction records found to delete for SO Document No: {request_id}, Record IDs: {record_ids}")
                        except Exception as delete_error:
                            print(f"Warning: Could not delete restriction records for SO Document No: {request_id}. Error: {str(delete_error)}. Continuing with next steps...")

                conn.commit()

            #Sending response email to creator
            response_success = send_job_card_response_email_to_customer( request_id, email_status,  reason)

            if not response_success['success']:
                return jsonify({"status": "Error", "success": False, "message": response_success['message']})

        except Exception as insert_error:
            print(f"Error updating email status: {insert_error}")
            return jsonify({"status": "Error", "success": False, "error": str(insert_error)}), 500

        response_data = {"status": email_status, "success": True, "req_id": request_id, "reason": reason, "client_details": client_details}

        if is_email_otp_required() and approver_email:
            clear_pending_otp(CHANNEL_JOB_CARD, action, request_id, cache_identity=approver_email)
        return jsonify(response_data)
        
    except Exception as e:
        response_data = {"status": "Error", "success": False, "error": str(e)}
        return jsonify(response_data)








#Routes for changing Reminder Interval
@app.route('/time-config', methods=['GET', 'POST'])
def time_config():
    """Password-protected route for configuring hours and days"""
    # Password from environment variable or default
    PASSWORD = os.getenv('TIME_CONFIG_PASSWORD', 'transpek0548')
    
    # Check if user is authenticated
    if not session.get('time_config_authenticated'):
        # Handle password submission
        if request.method == 'POST' and 'password' in request.form:
            if request.form['password'] == PASSWORD:
                session['time_config_authenticated'] = True
                flash('Authentication successful!', 'success')
                return redirect(url_for('time_config'))
            else:
                flash('Incorrect password. Please try again.', 'error')
        
        # Show password form
        return render_template('time_config_password.html')
    
    # User is authenticated - show the form
    if request.method == 'POST' and 'hours' in request.form and 'days' in request.form:
        try:
            hours = int(request.form.get('hours', 0))
            days = int(request.form.get('days', 0))
            
            # Use pyodbc for direct SQL operations
            with pyodbc.connect(get_odbc_connection_string()) as conn:
                cursor = conn.cursor()
                
                # Check if row with Line No = 1 exists
                check_query = f"SELECT [Line No] FROM {REMINDER_DURATION_TABLE} WHERE [Line No] = ?"
                cursor.execute(check_query, (1,))
                existing_row = cursor.fetchone()
                
                if existing_row:
                    # Update existing row
                    # Note: timestamp column is a SQL Server timestamp/rowversion type and is automatically managed - don't update it
                    update_query = f"""
                        UPDATE {REMINDER_DURATION_TABLE}
                        SET [Hours] = ?, [Days] = ?, [$systemModifiedAt] = GETDATE()
                        WHERE [Line No] = ?
                    """
                    cursor.execute(update_query, (hours, days, 1))
                    flash(f'Configuration updated successfully! Hours: {hours}, Days: {days}', 'success')
                else:
                    # Create new row (Line No = 1)
                    # Generate UUIDs for system fields (NEWSEQUENTIALID() can only be used in DEFAULT, so we generate UUIDs in Python)
                    system_id = str(uuid.uuid4()).upper()
                    system_created_by = '00000000-0000-0000-0000-000000000000'
                    system_modified_by = '00000000-0000-0000-0000-000000000000'
                    
                    # Note: timestamp column is a SQL Server timestamp/rowversion type and is automatically managed - don't include it in INSERT
                    insert_query = f"""
                        INSERT INTO {REMINDER_DURATION_TABLE}
                        ([Line No], [Hours], [Days], [$systemId], [$systemCreatedAt], [$systemCreatedBy], [$systemModifiedAt], [$systemModifiedBy])
                        VALUES (?, ?, ?, ?, GETDATE(), ?, GETDATE(), ?)
                    """
                    cursor.execute(insert_query, (1, hours, days, system_id, system_created_by, system_modified_by))
                    flash(f'Configuration created successfully! Hours: {hours}, Days: {days}', 'success')
                
                conn.commit()
            
            return redirect(url_for('time_config'))
            
        except ValueError:
            flash('Invalid input. Please enter valid numbers for hours and days.', 'error')
        except Exception as e:
            flash(f'Error saving configuration: {str(e)}', 'error')
    
    # Get current configuration to display in form
    current_hours = 0
    current_days = 0
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            select_query = f"SELECT [Hours], [Days] FROM {REMINDER_DURATION_TABLE} WHERE [Line No] = ?"
            cursor.execute(select_query, (1,))
            result = cursor.fetchone()
            if result:
                current_hours = result[0] if result[0] is not None else 0
                current_days = result[1] if result[1] is not None else 0
    except Exception as e:
        print(f"Error fetching configuration: {str(e)}")
    
    return render_template('time_config.html', hours=current_hours, days=current_days)

@app.route('/time-config/logout', methods=['POST'])
def time_config_logout():
    """Logout from time config page"""
    session.pop('time_config_authenticated', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('time_config'))



@app.template_filter('from_json')
def from_json_filter(value):
    """Template filter to parse JSON string"""
    try:
        if isinstance(value, str):
            return json.loads(value)
        return value or {}
    except (json.JSONDecodeError, TypeError):
        return {}

@app.template_filter('format_datetime')
def format_datetime(value):
    """Format datetime value for display"""
    if not value:
        return 'N/A'
    try:
        if isinstance(value, str):
            # Try to parse the datetime string
            dt = datetime.fromisoformat(value.replace('Z', ''))
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return str(value)
    except Exception as e:
        print(f"Error formatting datetime: {str(e)}")
        return str("N/A")




# Start the scheduler when the app starts
start_scheduler()
# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())



# For local development
if __name__ == '__main__':
    # app.debug = True
    app.run(host='0.0.0.0', port=5000)
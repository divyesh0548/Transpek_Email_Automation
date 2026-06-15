
from Utility_Functions.config.constants import PURCHASE_REQ_LINE_TABLE, PURCHASE_REQ_TABLE
from Utility_Functions.config.database import get_odbc_connection_string, get_engine, row_to_dict, get_table_schema
import pyodbc
from datetime import datetime, date, time
from sqlalchemy import text
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import os
import json
import socket
import uuid
from Utility_Functions.config.secure import encrypt, decrypt

def get_status_text(status_code):
    """Convert status code to text - now handles both numeric and text status"""
    if isinstance(status_code, str):
        return status_code  # Already text
    
    status_map = {
        0: "Draft",
        1: "Approved", 
        2: "Pending",
        3: "Rejected"
    }
    return status_map.get(status_code, f"Unknown ({status_code})")

def fetch_IM_Purchase_line_items(no_):
    """
    Fetch all line items for a given document number from XYZ table
    Returns a list of dictionaries with item details
    """
    items_list = []
    
    query_lines = f"""
        SELECT 
            [No_], 
            [Description], 
            [Quantity], 
            [Unit of Measure], 
            [Unit Cost], 
            [Line Amount], 
            [Job No_],
            [Inventory], 
            [Expected Receipt Date]
        FROM {PURCHASE_REQ_LINE_TABLE}
        WHERE [Document No_] = ?
        ORDER BY [Line No_] ASC
    """
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            cursor.execute(query_lines, (no_,))
            line_items = cursor.fetchall()
            
            if line_items:
                print(f"Found {len(line_items)} line items for Document No_: {no_}")
                
                # Convert rows to dictionaries BEFORE closing cursor
                for line_row in line_items:
                    item_dict = row_to_dict(cursor, line_row)
                    items_list.append(item_dict)
                    
                    # no = item_dict.get('No_', 'N/A')
                    # desc = item_dict.get('Description', 'N/A')
                    # qty = item_dict.get('Quantity', 0)
                    # unit = item_dict.get('Unit of Measure', '')
                    # cost = item_dict.get('Unit Cost', 0)
                    # amount = item_dict.get('Amount', 0)
                    # job_no = item_dict.get('Job No_', '')
                    # inventory = item_dict.get('Inventory', '')
                    # receipt_date = item_dict.get('Expected Receipt Date', '')
            else:
                print(f"No line items found for Document No_: {no_}")
            
            cursor.close()  # Close cursor AFTER processing all rows
        
    except Exception as e:
        print(f"Error fetching line items from XYZ table: {str(e)}")
    
    return items_list

def fix_IM_Purchase_email_status():
    """Separate function to fix purchase orders with open status but email already sent.
    This function runs as a scheduled job every 5 seconds."""
    conn = None
    cursor = None
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            if not conn:
                print("Failed to connect to database")
                return

            fix_query = f"""
                SELECT [No_]
                FROM {PURCHASE_REQ_TABLE} 
                WHERE 
                    [Status] = 0
                    AND (
                            [Email Send] = '1'
                            OR [Emaill Status] = 'pending by accountants'
                            OR [Approved By Account Dept_] = 1
                        )
            """
            cursor.execute(fix_query)
            rows_to_fix = cursor.fetchall()

            if rows_to_fix:
                print(f"Found {len(rows_to_fix)} PURCHASE REQ records with open status but email is sent. Fixing them now...")
                for row in rows_to_fix:
                    update_fix_query = f"""
                        UPDATE {PURCHASE_REQ_TABLE}
                        SET [Email Send] = '0',
                            [Approved By Account Dept_] = 0,
                            [Emaill Status] = 'pending'
                        WHERE [No_] = ?
                    """
                    cursor.execute(update_fix_query, row[0] )  # Assuming '1' is the value for approved by account dept, adjust if needed
                    print(f" Updated PURCHASE REQ No_: {row[0]}")
                conn.commit()
                print("Fix completed successfully.\n")
            else:
                # No rows to fix, silently continue
                pass

    except Exception as e:
        print(f"Error in fix_IM_Purchase_email_status: {str(e)}")
        # Don't raise exception, just log and continue


def check_IM_Purchase_pending_emails():
    conn = None
    cursor = None
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            if not conn:
                print("Failed to connect to database")
                return

            # Columns aligned with PURCHASE_REQ_TABLE (see tables.txt); avoid SELECT *
            query = f"""
                SELECT
                    [Document Type],
                    [No_],
                    [Employee No_],
                    [Employee Name],
                    [Your Reference],
                    [Request Date],
                    [Posting Date],
                    [Expected Receipt Date],
                    [Posting Description],
                    [Due Date],
                    [Location Code],
                    [Shortcut Dimension 1 Code],
                    [Shortcut Dimension 2 Code],
                    [Shortcut Dimension 2 Value],
                    [Comment],
                    [Posting No_],
                    [Last Posting No_],
                    [Reason Code],
                    [Gen_ Bus_ Posting Group],
                    [Document Date],
                    [No_ Series],
                    [Posting No_ Series],
                    [Status],
                    [Dimension Set ID],
                    [Responsibility Center],
                    [Assigned User ID],
                    [Posted],
                    [Purchase Type],
                    [Indenting Department],
                    [Employee Department],
                    [Type of Jobwork],
                    [Capital Item Premises],
                    [Shortcut Dimension 3 Code],
                    [Shortcut Dimension 6 Code],
                    [Indent Type],
                    [Approved By],
                    [Approved Date],
                    [Approved Time],
                    [Job Card No_],
                    [Job Card Date],
                    [Job Task No_],
                    [Approved By Account Dept_],
                    [Approver Mail ID],
                    [Creator Mail ID]
                FROM {PURCHASE_REQ_TABLE}
                WHERE
                    [Status] = 2
                    AND (
                        [Email Send] = '0'
                        OR [Email Send] IS NULL
                        OR [Email Send] = ''
                    )
                    AND [Emaill Status] = 'approved by acc'
            """

            cursor.execute(query)
            column_names = [column[0] for column in cursor.description]
            pending_emails = cursor.fetchall()

            if pending_emails:
                print(f"Found {len(pending_emails)} pending purchase request emails to process")

                for email_row in pending_emails:
                    rd = dict(zip(column_names, email_row))
                    no_ = rd.get("No_")
                    print(f"Processing IM Purchase No_: {no_}")

                    approver_mail_raw = rd.get("Approver Mail ID")
                    creator_mail_raw = rd.get("Creator Mail ID")
                    approver_mail_id = approver_mail_raw if approver_mail_raw else None
                    creator_mail_id = creator_mail_raw if creator_mail_raw else None
                    print(f"Fetched Approver Mail ID: {approver_mail_id}, Creator Mail ID: {creator_mail_id}")

                    document_type = rd.get("Document Type")
                    no = rd.get("No_")
                    employee_no = rd.get("Employee No_")
                    employee_name = rd.get("Employee Name")
                    your_reference = rd.get("Your Reference")
                    request_date = rd.get("Request Date")
                    posting_date = rd.get("Posting Date")
                    expected_receipt_date = rd.get("Expected Receipt Date")
                    posting_description = rd.get("Posting Description")
                    due_date = rd.get("Due Date")
                    location_code = rd.get("Location Code")
                    shortcut_dimension_1_code = rd.get("Shortcut Dimension 1 Code")
                    shortcut_dimension_2_code = rd.get("Shortcut Dimension 2 Code")
                    shortcut_dimension_2_value = rd.get("Shortcut Dimension 2 Value")
                    comment = rd.get("Comment")
                    posting_no = rd.get("Posting No_")
                    last_posting_no = rd.get("Last Posting No_")
                    reason_code = rd.get("Reason Code")
                    gen_bus_posting_group = rd.get("Gen_ Bus_ Posting Group")
                    document_date = rd.get("Document Date")
                    no_series = rd.get("No_ Series")
                    posting_no_series = rd.get("Posting No_ Series")
                    dimension_set_id = rd.get("Dimension Set ID")
                    responsibility_center = rd.get("Responsibility Center")
                    assigned_user_id = rd.get("Assigned User ID")
                    posted = rd.get("Posted")
                    purchase_type = rd.get("Purchase Type")
                    indenting_department = rd.get("Indenting Department")
                    employee_department = rd.get("Employee Department")
                    type_of_jobwork = rd.get("Type of Jobwork")
                    capital_item_premises = rd.get("Capital Item Premises")
                    shortcut_dimension_3_code = rd.get("Shortcut Dimension 3 Code")
                    shortcut_dimension_6_code = rd.get("Shortcut Dimension 6 Code")
                    indent_type = rd.get("Indent Type")
                    approved_by = rd.get("Approved By")
                    approved_date = rd.get("Approved Date")
                    approved_time = rd.get("Approved Time")
                    job_card_no = rd.get("Job Card No_")
                    job_card_date = rd.get("Job Card Date")
                    job_task_no = rd.get("Job Task No_")
                    approved_by_account_dept = rd.get("Approved By Account Dept_")
                    approver_email = approver_mail_id
                    creator_email = creator_mail_id

                    header_data = {
                        "Document Type": document_type,
                        "No_": no,
                        "Employee No_": employee_no,
                        "Employee Name": employee_name,
                        "Your Reference": your_reference,
                        "Request Date": request_date,
                        "Posting Date": posting_date,
                        "Expected Receipt Date": expected_receipt_date,
                        "Posting Description": posting_description,
                        "Due Date": due_date,
                        "Location Code": location_code,
                        "Shortcut Dimension 1 Code": shortcut_dimension_1_code,
                        "Shortcut Dimension 2 Code": shortcut_dimension_2_code,
                        "Shortcut Dimension 2 Value": shortcut_dimension_2_value,
                        "Comment": comment,
                        "Posting No_": posting_no,
                        "Last Posting No_": last_posting_no,
                        "Reason Code": reason_code,
                        "Gen_ Bus_ Posting Group": gen_bus_posting_group,
                        "Document Date": document_date,
                        "No_ Series": no_series,
                        "Posting No_ Series": posting_no_series,
                        "Status": 2,  # Integer status - 2 for Pending
                        "Dimension Set ID": dimension_set_id,
                        "Responsibility Center": responsibility_center,
                        "Assigned User ID": assigned_user_id,
                        "Posted": posted,
                        "Purchase Type": purchase_type,
                        "Indenting Department": indenting_department,
                        "Employee Department": employee_department,
                        "Type of Jobwork": type_of_jobwork,
                        "Capital Item Premises": capital_item_premises,
                        "Shortcut Dimension 3 Code": shortcut_dimension_3_code,
                        "Shortcut Dimension 6 Code": shortcut_dimension_6_code,
                        "Indent Type": indent_type,
                        "Approved By": approved_by,
                        "Approved Date": approved_date,
                        "Approved Time": approved_time,
                        "Job Card No_": job_card_no,
                        "Job Card Date": job_card_date,
                        "Job Task No_": job_task_no,
                        "Approved By Account Dept_": approved_by_account_dept,
                        "$systemCreatedAt": str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255],
                        "$systemModifiedAt": str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255],
                        "Approver Mail ID": approver_email,
                        "Creator Mail ID": creator_email,
                        "Emaill Status": "Pending",
                        "Reason": "",
                        "Email Send": '0',
                        "Response Mail Send": '0',
                    }

                    items_list = fetch_IM_Purchase_line_items(no)

                    print(f"Header Data : {header_data}")
                    print(f"Line Items : {items_list}")
                    print(f"Processing data with No : {no_}")
                    current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]


                    if approver_email and approver_email.strip():
                        # Send email
                        try:
                            sent = send_approval_email(header_data, items_list)
                            if sent:
                                update_query = f"""
                                UPDATE {PURCHASE_REQ_TABLE}
                                SET [Email Send] = 1,
                                    [Emaill Status] = 'sent to hod for approval',
                                    [Timestamps] = ?
                                WHERE [No_] = ?
                                """
                                cursor.execute(update_query, (current_time, no_))
                                conn.commit()
                                print(f"✅ Successfully processed IM Purchase No_: {no_}")
                            else:
                                print(f"❌ Email sending failed for IM Purchase No_: {no_}")
                        except Exception as e:
                            print(f"Error sending approval email for IM Purchase No_ {no_}: {str(e)}")
                            # Email send failed, but status is already updated to prevent reprocessing

                    else:
                        print(f"❌ Invalid Approver Email ID for Order No: {no_}")

                    print(f"✅ Successfully processed IM Requisition order: {no_}")

                print("Successfully processed all pending emails")
            else:
                print("No pending email found in IM purchase requests")

            # status_zero_query = f"""
            #     SELECT [No_]
            #     FROM {PURCHASE_REQ_TABLE}
            #     WHERE
            #         [Status] = 0
            #         AND (
            #             [Emaill Status] = 'pending'
            #             OR [Emaill Status] = 'Pending'
            #             OR [Emaill Status] = ''
            #             OR [Emaill Status] IS NULL
            #         )
            #     """
            # cursor.execute(status_zero_query)
            # unsubmitted = cursor.fetchall()

            # if unsubmitted:
            #     print(f"\n{'='*60}")
            #     print(f"Found {len(unsubmitted)} records which are not sent for approval.")
            #     print(f"{'='*60}")
            #     for record in unsubmitted[:5]:
            #         print(f"  Purchase Requisition No_: {record[0]}")
            #     if len(unsubmitted) > 5:
            #         print(f"  ... and {len(unsubmitted) - 5} more record(s)")
            #     print(f"{'='*60}\n")

    except pyodbc.Error as e:
        print(f"Database error: {str(e)}")
        if conn:
            conn.rollback()
    except Exception as e:
        print(f"Error checking pending emails: {str(e)}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def check_IM_Purchase_pending_emails_for_accountants():
    """Check for pending emails to send to accountants"""
    conn = None
    cursor = None
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            if not conn:
                print("Failed to connect to database")
                return
    
            # Columns aligned with PURCHASE_REQ_TABLE (see tables.txt); include accountant approver fields
            query = f"""
                SELECT
                    [Document Type],
                    [No_],
                    [Employee No_],
                    [Employee Name],
                    [Your Reference],
                    [Request Date],
                    [Posting Date],
                    [Expected Receipt Date],
                    [Posting Description],
                    [Due Date],
                    [Location Code],
                    [Shortcut Dimension 1 Code],
                    [Shortcut Dimension 2 Code],
                    [Shortcut Dimension 2 Value],
                    [Comment],
                    [Posting No_],
                    [Last Posting No_],
                    [Reason Code],
                    [Gen_ Bus_ Posting Group],
                    [Document Date],
                    [No_ Series],
                    [Posting No_ Series],
                    [Status],
                    [Dimension Set ID],
                    [Responsibility Center],
                    [Assigned User ID],
                    [Posted],
                    [Purchase Type],
                    [Indenting Department],
                    [Employee Department],
                    [Type of Jobwork],
                    [Capital Item Premises],
                    [Shortcut Dimension 3 Code],
                    [Shortcut Dimension 6 Code],
                    [Indent Type],
                    [Approved By],
                    [Approved Date],
                    [Approved Time],
                    [Job Card No_],
                    [Job Card Date],
                    [Job Task No_],
                    [Approved By Account Dept_],
                    [Account dept Approver 1],
                    [Account dept Approver 2]
                FROM {PURCHASE_REQ_TABLE}
                WHERE
                    [Status] = 2
                    AND (
                        [Emaill Status] = ''
                        OR [Emaill Status] IS NULL
                        OR [Emaill Status] = 'pending'
                        OR [Emaill Status] = 'Pending'
                    )
            """

            cursor.execute(query)
            column_names = [column[0] for column in cursor.description]
            pending_emails = cursor.fetchall()

            if pending_emails:
                print(f"Found {len(pending_emails)} pending purchase request emails to send to accountants")

                for email_row in pending_emails:
                    rd = dict(zip(column_names, email_row))
                    no_ = rd.get("No_")
                    print(f"Processing IM Purchase No_: {no_} for accountants")

                    acc1_raw = rd.get("Account dept Approver 1")
                    acc2_raw = rd.get("Account dept Approver 2")
                    acc1_id = acc1_raw if acc1_raw else None
                    acc2_id = acc2_raw if acc2_raw else None
                    print(f"Fetched Accountant 1 Mail ID: {acc1_id}, Accountant 2 Mail ID: {acc2_id}")

                    document_type = rd.get("Document Type")
                    no = rd.get("No_")
                    employee_no = rd.get("Employee No_")
                    employee_name = rd.get("Employee Name")
                    your_reference = rd.get("Your Reference")
                    request_date = rd.get("Request Date")
                    posting_date = rd.get("Posting Date")
                    expected_receipt_date = rd.get("Expected Receipt Date")
                    posting_description = rd.get("Posting Description")
                    due_date = rd.get("Due Date")
                    location_code = rd.get("Location Code")
                    shortcut_dimension_1_code = rd.get("Shortcut Dimension 1 Code")
                    shortcut_dimension_2_code = rd.get("Shortcut Dimension 2 Code")
                    shortcut_dimension_2_value = rd.get("Shortcut Dimension 2 Value")
                    comment = rd.get("Comment")
                    posting_no = rd.get("Posting No_")
                    last_posting_no = rd.get("Last Posting No_")
                    reason_code = rd.get("Reason Code")
                    gen_bus_posting_group = rd.get("Gen_ Bus_ Posting Group")
                    document_date = rd.get("Document Date")
                    no_series = rd.get("No_ Series")
                    posting_no_series = rd.get("Posting No_ Series")
                    dimension_set_id = rd.get("Dimension Set ID")
                    responsibility_center = rd.get("Responsibility Center")
                    assigned_user_id = rd.get("Assigned User ID")
                    posted = rd.get("Posted")
                    purchase_type = rd.get("Purchase Type")
                    indenting_department = rd.get("Indenting Department")
                    employee_department = rd.get("Employee Department")
                    type_of_jobwork = rd.get("Type of Jobwork")
                    capital_item_premises = rd.get("Capital Item Premises")
                    shortcut_dimension_3_code = rd.get("Shortcut Dimension 3 Code")
                    shortcut_dimension_6_code = rd.get("Shortcut Dimension 6 Code")
                    indent_type = rd.get("Indent Type")
                    approved_by = rd.get("Approved By")
                    approved_date = rd.get("Approved Date")
                    approved_time = rd.get("Approved Time")
                    job_card_no = rd.get("Job Card No_")
                    job_card_date = rd.get("Job Card Date")
                    job_task_no = rd.get("Job Task No_")
                    approved_by_account_dept = rd.get("Approved By Account Dept_")
                    accountant1_email = acc1_id
                    accountant2_email = acc2_id

                    header_data = {
                        "Document Type": document_type,
                        "No_": no,
                        "Employee No_": employee_no,
                        "Employee Name": employee_name,
                        "Your Reference": your_reference,
                        "Request Date": request_date,
                        "Posting Date": posting_date,
                        "Expected Receipt Date": expected_receipt_date,
                        "Posting Description": posting_description,
                        "Due Date": due_date,
                        "Location Code": location_code,
                        "Shortcut Dimension 1 Code": shortcut_dimension_1_code,
                        "Shortcut Dimension 2 Code": shortcut_dimension_2_code,
                        "Shortcut Dimension 2 Value": shortcut_dimension_2_value,
                        "Comment": comment,
                        "Posting No_": posting_no,
                        "Last Posting No_": last_posting_no,
                        "Reason Code": reason_code,
                        "Gen_ Bus_ Posting Group": gen_bus_posting_group,
                        "Document Date": document_date,
                        "No_ Series": no_series,
                        "Posting No_ Series": posting_no_series,
                        "Status": 2,
                        "Dimension Set ID": dimension_set_id,
                        "Responsibility Center": responsibility_center,
                        "Assigned User ID": assigned_user_id,
                        "Posted": posted,
                        "Purchase Type": purchase_type,
                        "Indenting Department": indenting_department,
                        "Employee Department": employee_department,
                        "Type of Jobwork": type_of_jobwork,
                        "Capital Item Premises": capital_item_premises,
                        "Shortcut Dimension 3 Code": shortcut_dimension_3_code,
                        "Shortcut Dimension 6 Code": shortcut_dimension_6_code,
                        "Indent Type": indent_type,
                        "Approved By": approved_by,
                        "Approved Date": approved_date,
                        "Approved Time": approved_time,
                        "Job Card No_": job_card_no,
                        "Job Card Date": job_card_date,
                        "Job Task No_": job_task_no,
                        "Approved By Account Dept_": approved_by_account_dept,
                        "$systemCreatedAt": str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255],
                        "$systemModifiedAt": str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255],
                        "acc1_id": accountant1_email,
                        "acc2_id": accountant2_email,
                    }

                    items_list = fetch_IM_Purchase_line_items(no)

                    print(f"Header Data : {header_data}")
                    print(f"Line Items : {items_list}")
                    print(f"Processing data with No : {no_}")
                    current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

                    # Send email to accountants then update status
                    try:
                        print(f"Line Items fetched: {items_list} for Document No_: {no_}")
                        sent = send_accountant_email(
                            header_data, items_list, accountant1_email, accountant2_email
                        )
                        if not sent:
                            print(
                                f"IM Purchase No_: {no_} accountant email failed; "
                                "database not updated."
                            )
                            continue

                        update_query = f"""
                            UPDATE {PURCHASE_REQ_TABLE}
                            SET [Emaill Status] = 'pending by accountants',
                                [Timestamps] = ?
                            WHERE [No_] = ?
                                AND (
                                    [Emaill Status] = ''
                                    OR [Emaill Status] IS NULL
                                    OR [Emaill Status] = 'pending'
                                    OR [Emaill Status] = 'Pending'
                                )
                        """
                        cursor.execute(update_query, (current_time, no_))
                        rows_updated = cursor.rowcount
                        conn.commit()

                        if rows_updated == 0:
                            print(
                                f"IM Purchase No_: {no_} email sent but row was not updated "
                                "(status may have changed)."
                            )

                    except Exception as e:
                        print(
                            f"Error in accountant email/update for IM Purchase No_ {no_}: {str(e)}"
                        )
                
                print("Successfully processed all pending emails for accountants")
            else:
                print("No pending email found for accountants in IM purchase requests")
            
    except pyodbc.Error as e:
        print(f"Database error: {str(e)}")
    except Exception as e:
        print(f"Error checking pending emails for accountants: {str(e)}")


def get_purchase_request_by_id(request_id):
    query = f"SELECT * FROM {PURCHASE_REQ_TABLE} WHERE [No_] = :request_id"
    params = {"request_id": str(request_id)}
    
    engine = get_engine()
    with engine.connect() as connection:
        result = connection.execute(text(query), params).fetchone()
        
    if result:
        # Convert Row to dictionary
        return dict(result._mapping)
    return None

def is_request_already_processed(request_id):
    try:
        req = get_purchase_request_by_id(request_id)
        if req:
            email_status = req.get('Email Status', '').lower()  # Get Email Status and convert to lowercase
            req['StatusText'] = get_status_text(email_status)
            return email_status in ['approved', 'rejected']  # Already processed if approved or rejected
        return False
    except Exception as e:
        print(f"Error checking request status: {e}")
        return False

def send_approval_email(data, line_item_data):
    """Send approval email for a purchase requisition"""
    try:
        print(f"[DEBUG] Preparing to send approval email for document: {data.get('No_', '')}")
        print(f"[DEBUG] Full purchase_req dict: {json.dumps(data, default=str, indent=2)}")
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        print(f"[DEBUG] SMTP_SERVER: {smtp_server}")
        print(f"[DEBUG] SMTP_PORT: {smtp_port}")
        print(f"[DEBUG] SMTP_USERNAME: {smtp_user}")
        print(f"[DEBUG] SMTP_PASSWORD: {'SET' if smtp_password else 'NOT SET'}")
        if not all([smtp_server, smtp_user, smtp_password]):
            print("Email configuration incomplete")
            print(f"SMTP_SERVER: {'✓' if smtp_server else '✗'}")
            print(f"SMTP_USERNAME: {'✓' if smtp_user else '✗'}")
            print(f"SMTP_PASSWORD: {'✓' if smtp_password else '✗'}")
            return False
        

        from IM_Purchase_Requisition_PDF_Generator import get_logo_base64
        # Prepare email content
        data['logo_base64'] = get_logo_base64()

        from IM_Purchase_Requisition_PDF_Generator import generate_purchase_requisition_pdf
        pdf_buffer = generate_purchase_requisition_pdf(
            data, 
            line_item_data, 
            "template.pdf"
        )

        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        recipient_email = data.get('Approver Mail ID') or data.get('approver_mailid') or data.get('Creator Mail ID') or ''
        print(f"[DEBUG] Recipient email for document {data.get('No_', '')}: {recipient_email}")
        msg['To'] = recipient_email
        subject_text = f"IM Purchase Requisition Approval Required - {data.get('No_', '')}"
        msg['Subject'] = subject_text
        base_url = os.getenv('BASE_URL', 'http://localhost:5000')
        req_id = data.get('No_', '')
        encrypted_req_id = encrypt(str(req_id))
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear Approver,</p>
        <p>A new purchase requisition requires your approval:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{data.get('No_', '')}</td></tr>
            <tr><td><strong>Employee:</strong></td><td>{data.get('Employee Name', '')}</td></tr>
            <tr><td><strong>Department:</strong></td><td>{data.get('Indenting Department', '')}</td></tr>
            <tr><td><strong>Request Date:</strong></td><td>{data.get('Request Date', '')}</td></tr>
            <tr><td><strong>Description:</strong></td><td>{data.get('Posting Description', '')}</td></tr>
        </table>
        <p>Please review the attached PDF and take action:</p>
        <div style='margin: 30px 0;'>
            <a href='{base_url}/email-approve/{encrypted_req_id}' 
               style='background-color: #28a745; color: white; padding: 12px 24px; text-decoration: none; margin-right: 10px; border-radius: 4px;'>
               ✓ APPROVE
            </a>
            <a href='{base_url}/email-reject/{encrypted_req_id}' 
               style='background-color: #dc3545; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px;'>
               ✗ REJECT
            </a>
        </div>
        <p>Best regards,<br>
        Transpek System</p>
        </body>
        </html>
        """
        msg.attach(MIMEText(body_html, 'html'))
        
        from email.mime.application import MIMEApplication
        pdf_attachment = MIMEApplication(pdf_buffer.getvalue(), _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition','attachment',filename=f'Purchase_Requisition_{req_id}.pdf')
        msg.attach(pdf_attachment)
        import re
        if not recipient_email or not isinstance(recipient_email, str) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", recipient_email):
            print(f"❌ Invalid recipient email for document {req_id}: '{recipient_email}'")
            return False
        print(f"[DEBUG] Attempting SMTP send for document {req_id}...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"✅ Approval email sent for document {data.get('No_', '')}")
        return True
    except Exception as e:
        print(f"Error sending approval email: {e}")
        return False


def send_accountant_email(data, line_item_data, acc1_email, acc2_email):
    """Send email to accountants for a purchase requisition"""
    try:
        print(f"[DEBUG] Preparing to send accountant email for document: {data.get('No_', '')}")
        print(f"[DEBUG] Full purchase_req dict: {json.dumps(data, default=str, indent=2)}")
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv('SMTP_PASSWORD')
        print(f"[DEBUG] SMTP_SERVER: {smtp_server}")
        print(f"[DEBUG] SMTP_PORT: {smtp_port}")
        print(f"[DEBUG] SMTP_USERNAME: {smtp_user}")
        print(f"[DEBUG] SMTP_PASSWORD: {'SET' if smtp_password else 'NOT SET'}")
        if not all([smtp_server, smtp_user, smtp_password]):
            print("Email configuration incomplete")
            print(f"SMTP_SERVER: {'✓' if smtp_server else '✗'}")
            print(f"SMTP_USERNAME: {'✓' if smtp_user else '✗'}")
            print(f"SMTP_PASSWORD: {'✓' if smtp_password else '✗'}")
            return False
        

        from IM_Purchase_Requisition_PDF_Generator import get_logo_base64
        # Prepare email content
        data['logo_base64'] = get_logo_base64()

        from IM_Purchase_Requisition_PDF_Generator import generate_purchase_requisition_pdf
        pdf_buffer = generate_purchase_requisition_pdf(
            data, 
            line_item_data, 
            "template.pdf"
        )

        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        
        # Combine both accountant emails
        recipient_emails = []
        if acc1_email:
            recipient_emails.append(acc1_email)
        if acc2_email:
            recipient_emails.append(acc2_email)
        
        if not recipient_emails:
            print(f"[DEBUG] No accountant emails found for document {data.get('No_', '')}")
            return False
        
        recipient_email = ', '.join(recipient_emails)
        print(f"[DEBUG] Recipient emails for document {data.get('No_', '')}: {recipient_email}")
        msg['To'] = recipient_email
        subject_text = f"IM Purchase Requisition Approval Required - {data.get('No_', '')}"
        msg['Subject'] = subject_text
        base_url = os.getenv('BASE_URL', 'http://localhost:5000')
        req_id = data.get('No_', '')
        encrypted_req_id = encrypt(str(req_id))
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear Accountant,</p>
        <p>A new purchase requisition requires your review:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{data.get('No_', '')}</td></tr>
            <tr><td><strong>Employee:</strong></td><td>{data.get('Employee Name', '')}</td></tr>
            <tr><td><strong>Department:</strong></td><td>{data.get('Indenting Department', '')}</td></tr>
            <tr><td><strong>Request Date:</strong></td><td>{data.get('Request Date', '')}</td></tr>
            <tr><td><strong>Description:</strong></td><td>{data.get('Posting Description', '')}</td></tr>
        </table>
        <p>Please review the attached PDF and take action:</p>
        <div style='margin: 30px 0;'>
            <a href='{base_url}/email-accountant-approve/{encrypted_req_id}' 
               style='background-color: #28a745; color: white; padding: 12px 24px; text-decoration: none; margin-right: 10px; border-radius: 4px;'>
               ✓ APPROVE
            </a>
            <a href='{base_url}/email-accountant-reject/{encrypted_req_id}' 
               style='background-color: #dc3545; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px;'>
               ✗ REJECT
            </a>
        </div>
        <p>Best regards,<br>
        Transpek System</p>
        </body>
        </html>
        """
        msg.attach(MIMEText(body_html, 'html'))
        
        from email.mime.application import MIMEApplication
        pdf_attachment = MIMEApplication(pdf_buffer.getvalue(), _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition','attachment',filename=f'Purchase_Requisition_{req_id}.pdf')
        msg.attach(pdf_attachment)
        import re
        
        # Validate all recipient emails
        for email_addr in recipient_emails:
            if not email_addr or not isinstance(email_addr, str) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_addr):
                print(f"❌ Invalid accountant email for document {req_id}: '{email_addr}'")
            else:
                print(f"✅ Valid email: {email_addr}")
        
        print(f"[DEBUG] Attempting SMTP send for document {req_id}...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"✅ Accountant email sent for document {data.get('No_', '')}")
        return True
    except Exception as e:
        print(f"Error sending accountant email: {e}")
        return False


def send_response_email_to_creator(purchase_req, response_status, reason=None):
    """Send response email to creator after approval/rejection"""
    try:
        print(f"[DEBUG] Preparing to send response email to creator for document: {purchase_req.get('No_', '')}")
        
        creator_email = purchase_req.get('Creator Mail ID')
        if not creator_email:
            print(f"ERROR: No creator email found for document {purchase_req.get('No_', '')}")
            return False
        
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        
        if not all([smtp_server, smtp_user, smtp_password]):
            print("ERROR: Email configuration incomplete")
            return False
        
        # Create email message
        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        msg['To'] = creator_email
        msg['Subject'] = f"Response for Document {purchase_req.get('No_', '')}"

        # Determine status text and color
        is_approved = "approved" in response_status.lower()
        status_text = "Approved by HOD" if is_approved else "Rejected by HOD"
        
        # Create email body
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear User,</p>
        <p>Your document has been processed with the following response:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{purchase_req.get('No_', '')}</td></tr>
            <tr><td><strong>Response:</strong></td><td style='color: {"green" if "Approved" in response_status else "red"}'>{status_text}</td></tr>
            {f"<tr><td><strong>Reason:</strong></td><td>{reason}</td></tr>" if reason else ""}
            <tr><td><strong>Response Time:</strong></td><td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
        </table>
        <p>Best regards,<br>
        Transpek System</p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body_html, 'html'))
        
        # Send email
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Response email sent to creator {creator_email} for document {purchase_req.get('No_', '')}")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to send response email to creator: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_accountant_response_email_to_creator(purchase_req, response_status, reason=None):
    """Send response email to creator after accountant approval/rejection"""
    try:
        print(f"[DEBUG] Preparing to send accountant response email to creator for document: {purchase_req.get('No_', '')}")
        
        creator_email = purchase_req.get('Creator Mail ID')
        if not creator_email:
            print(f"ERROR: No creator email found for document {purchase_req.get('No_', '')}")
            return False
        
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        
        if not all([smtp_server, smtp_user, smtp_password]):
            print("ERROR: Email configuration incomplete")
            return False
        
        # Create email message
        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        msg['To'] = creator_email
        msg['Subject'] = f"Accountant Response for Document {purchase_req.get('No_', '')}"
        
        # Determine status text and color
        is_approved = "approved" in response_status.lower() or "approved by acc" in response_status.lower()
        status_text = "Approved by Accountant" if is_approved else "Rejected by Accountant"
        status_color = "green" if is_approved else "red"
        
        # Create email body
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear User,</p>
        <p>Your document has been processed by the accountant with the following response:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{purchase_req.get('No_', '')}</td></tr>
            <tr><td><strong>Response:</strong></td><td style='color: {status_color}'>{status_text}</td></tr>
            {f"<tr><td><strong>Reason:</strong></td><td>{reason}</td></tr>" if reason else ""}
            <tr><td><strong>Response Time:</strong></td><td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
        </table>
        <p>Best regards,<br>
        Transpek System</p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body_html, 'html'))
        
        # Send email
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        
        print(f" Accountant response email sent to creator {creator_email} for document {purchase_req.get('No_', '')}")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to send accountant response email to creator: {e}")
        import traceback
        traceback.print_exc()
        return False


def to_nav_iso_datetime(value, fallback_date=None):
    """
    Convert datetime/date/time-like input to NAV-style UTC string:
    YYYY-MM-DDTHH:MM:SS.mmmZ
    """
    if value is None or value == "":
        return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Already close to expected format; keep as-is
        if "T" in s and s.endswith("Z"):
            return s
        # Try parsing common ISO values
        try:
            parse_str = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt_val = datetime.fromisoformat(parse_str)
        except Exception:
            return s
    elif isinstance(value, datetime):
        dt_val = value
    elif isinstance(value, date):
        dt_val = datetime.combine(value, datetime.min.time())
    elif isinstance(value, time):
        base_date = fallback_date if isinstance(fallback_date, date) else date(1754, 1, 1)
        dt_val = datetime.combine(base_date, value)
    else:
        return str(value)

    return dt_val.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        
def requisition_line_items_approval_update(order_no):
    """
    For the given requisition/order number, update all related line rows:
    - [Approved for Process] = 1
    - [Line Status] = 1
    """
    try:
        with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
            cursor = conn.cursor()

            update_query = f"""
                UPDATE {PURCHASE_REQ_LINE_TABLE}
                SET [Approved for Process] = 1,
                    [Line Status] = 1
                WHERE [Document No_] = ?
            """
            cursor.execute(update_query, (order_no,))
            conn.commit()

            updated_count = cursor.rowcount
            # Some ODBC drivers return -1 for rowcount; treat that as "executed".
            if updated_count == 0:
                print(f"[DEBUG] No line items found for order_no: {order_no}")
                return False
            print(f"[DEBUG] Updated {updated_count} line items for order_no: {order_no}")
            return True
    except Exception as e:
        print(f"[ERROR] Failed to update line items for order_no {order_no}: {e}")
        import traceback
        traceback.print_exc()
        return False



def update_purchase_request_status_with_text(request_id, int_status, text_status, reason=None, user_data=None, timestemp=None, approval_data=None):

    approved_date = approval_data.get("approved_date") if approval_data else None
    approved_time = approval_data.get("approved_time") if approval_data else None

    try:
        # Get schema columns
        schema = get_table_schema(PURCHASE_REQ_TABLE)
        schema_cols = []
        for col in schema:
            if hasattr(col, '_mapping'):
                schema_cols.append(col._mapping['column_name'])
            elif isinstance(col, dict):
                schema_cols.append(col['column_name'])
            elif isinstance(col, tuple) and len(col) > 0:
                # Try first element if tuple
                schema_cols.append(col[0])
        
        print(f"DEBUG: Available schema columns: {schema_cols}")
        
        # Simplified column mapping based on actual schema
        col_map = {}
        
        # Map Status column
        if 'Status' in schema_cols:
            col_map['Status'] = 'Status'
        elif 'status' in schema_cols:
            col_map['Status'] = 'status'
        
        # Map Emaill Status column
        if 'Emaill Status' in schema_cols:
            col_map['Emaill Status'] = 'Emaill Status'
        elif 'email_status' in schema_cols:
            col_map['Emaill Status'] = 'email_status'
        
        # Map User Data column
        if 'User Data' in schema_cols:
            col_map['User Data'] = 'User Data'
        elif 'user_data' in schema_cols:
            col_map['User Data'] = 'user_data'
        
        # Map Timestamps column
        if 'Timestamps' in schema_cols:
            col_map['Timestamps'] = 'Timestamps'
        elif 'timestemp' in schema_cols:
            col_map['Timestamps'] = 'timestemp'
        
        # Map Reason column
        if 'Reason' in schema_cols:
            col_map['Reason'] = 'Reason'
        elif 'reason' in schema_cols:
            col_map['Reason'] = 'reason'
        
        # Map Response Mail Send column
        if 'Response Mail Send' in schema_cols:
            col_map['Response Mail Send'] = 'Response Mail Send'
        elif 'response_mail_send' in schema_cols:
            col_map['Response Mail Send'] = 'response_mail_send'
        
        # Map Approved by Account Dept_ column
        if 'Approved by Account Dept_' in schema_cols:
            col_map['Approved by Account Dept_'] = 'Approved by Account Dept_'
        elif 'approved_by_acc_dept' in schema_cols:
            col_map['Approved by Account Dept_'] = 'approved_by_acc_dept'

        # Map Approved Date column
        if 'Approved Date' in schema_cols:
            col_map['Approved Date'] = 'Approved Date'
        elif 'approved_date' in schema_cols:
            col_map['Approved Date'] = 'approved_date'

        # Map Approved Time column
        if 'Approved Time' in schema_cols:
            col_map['Approved Time'] = 'Approved Time'
        elif 'approved_time' in schema_cols:
            col_map['Approved Time'] = 'approved_time'

        # Map Approved By column
        if 'Approved By' in schema_cols:
            col_map['Approved By'] = 'Approved By'
        elif 'approved_by' in schema_cols:
            col_map['Approved By'] = 'approved_by'
        
        print(f"DEBUG: Column mapping: {col_map}")
        
        # Build update query
        updates = []
        params = {"request_id": str(request_id)}
        
        # Update Status
        if col_map.get('Status'):
            updates.append(f"[{col_map['Status']}] = :int_status")
            params["int_status"] = int_status
        
        # Update Emaill Status
        if col_map.get('Emaill Status'):
            status_text = 'Approved' if int_status == 1 else 'Rejected' if int_status == 3 else 'Pending'
            updates.append(f"[{col_map['Emaill Status']}] = :email_status")
            params["email_status"] = status_text
        
        # Update Approved by Accountant boolean (only set to 0 when int_status is 0; otherwise leave column unchanged)
        if col_map.get('Approved by Account Dept_') and int_status == 0:
            updates.append(f"[{col_map['Approved by Account Dept_']}] = :approved_by_acc_dept")
            params["approved_by_acc_dept"] = 0

        # Update Approved Date/Time in NAV datetime format
        approved_date_iso = to_nav_iso_datetime(approved_date)
        approved_time_iso = to_nav_iso_datetime(approved_time, fallback_date=date(1754, 1, 1))

        if col_map.get('Approved Date') and approved_date_iso:
            updates.append(f"[{col_map['Approved Date']}] = :approved_date")
            params["approved_date"] = approved_date_iso

        if col_map.get('Approved Time') and approved_time_iso:
            updates.append(f"[{col_map['Approved Time']}] = :approved_time")
            params["approved_time"] = approved_time_iso

        # Update Approved By (approver_name) when action is approved
        approver_name = approval_data.get("approver_name") if approval_data else None
        if col_map.get('Approved By') and approver_name and int_status == 1:
            updates.append(f"[{col_map['Approved By']}] = :approver_name")
            params["approver_name"] = approver_name
        
        # Update Reason
        if col_map.get('Reason') and reason:
            updates.append(f"[{col_map['Reason']}] = :reason")
            params["reason"] = reason
        
        # Update User Data
        if col_map.get('User Data'):
            if user_data is None:
                user_data = collect_user_machine_details()
            
            # Ensure User Data fits within 240 character limit
            if isinstance(user_data, str) and len(user_data) > 240:
                try:
                    # Parse JSON and keep only essential fields
                    parsed = json.loads(user_data)
                    minimal_details = {
                        "ts": parsed.get("ts", ""),
                        "ip": parsed.get("ip", "0.0.0.0")
                    }
                    user_data = json.dumps(minimal_details, separators=(',', ':'))
                    
                    # If still too long, use absolute minimal format
                    if len(user_data) > 240:
                        user_data = f'{{"ts":"{minimal_details.get("ts", "")}","ip":"{minimal_details.get("ip", "0.0.0.0")}"}}'
                        
                    # Final safety check
                    if len(user_data) > 240:
                        user_data = user_data[:240]
                        
                except Exception as e:
                    print(f"DEBUG: Failed to parse User Data JSON, using minimal format: {e}")
                    # Use absolute minimal format
                    user_data = f'{{"ts":"{datetime.now().strftime("%Y%m%d%H%M")}","ip":"0.0.0.0"}}'
                    if len(user_data) > 240:
                        user_data = user_data[:240]
            
            updates.append(f"[{col_map['User Data']}] = :user_data")
            params["user_data"] = user_data
        
        # Update Timestamps
        if col_map.get('Timestamps') and timestemp:
            updates.append(f"[{col_map['Timestamps']}] = :timestemp")
            params["timestemp"] = timestemp
        
        if not updates:
            print("ERROR: No columns to update")
            return False
        
        # Execute main update
        query = f"UPDATE {PURCHASE_REQ_TABLE} SET {', '.join(updates)} WHERE [No_] = :request_id"
        
        engine = get_engine()
        with engine.connect() as connection:
            result = connection.execute(text(query), params)
            connection.commit()
            update_success = result.rowcount > 0
        
        if update_success:

            if int_status == 1:
                try:
                    requisition_line_items_approval_update(request_id)
                except Exception as e:
                    # Do not fail the main approval/rejection flow if line update fails.
                    print(f"[ERROR] Line items approval update failed for {request_id}: {e}")
            # Send response email to creator
            try:
                req = get_purchase_request_by_id(request_id)
                if req and req.get('Creator Mail ID'):
                    creator_email = req.get('Creator Mail ID')
                    print(f"DEBUG: Sending response email to creator: {creator_email}")
                    
                    # Send response email
                    if send_response_email_to_creator(req, text_status, reason):
                        print(f"DEBUG: Response email sent successfully to creator")
                        
                        # Update Response Mail Send status
                        if col_map.get('Response Mail Send'):
                            response_update_query = f"UPDATE {PURCHASE_REQ_TABLE} SET [{col_map['Response Mail Send']}] = 'sent' WHERE [No_] = :request_id"
                            with engine.connect() as conn:
                                conn.execute(text(response_update_query), {"request_id": str(request_id)})
                                conn.commit()
                            print(f"DEBUG: Updated Response Mail Send to 'sent'")
                    else:
                        print(f"DEBUG: Failed to send response email to creator")
                        # Update Response Mail Send status to failed
                        if col_map.get('Response Mail Send'):
                            response_update_query = f"UPDATE {PURCHASE_REQ_TABLE} SET [{col_map['Response Mail Send']}] = 'failed' WHERE [No_] = :request_id"
                            with engine.connect() as conn:
                                conn.execute(text(response_update_query), {"request_id": str(request_id)})
                                conn.commit()
                            print(f"DEBUG: Updated Response Mail Send to 'failed'")
                else:
                    print(f"DEBUG: No creator email found for request {request_id}")
            except Exception as e:
                print(f"ERROR: Failed to send response email: {e}")
        
        return update_success
        
    except Exception as e:
        print(f"ERROR: Failed to update purchase request status: {e}")
        return False


def collect_user_machine_details(request_object=None):
    try:
        # Start with only the most essential info
        machine_details = {
            "ts": datetime.now().strftime("%Y%m%d%H%M"),  # Short timestamp (12 chars)
            "ip": "0.0.0.0"  # Default IP
        }
        
        # Try to get local IP address (most important)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            machine_details["ip"] = local_ip
            s.close()
        except:
            pass
            
        # If Flask request object is provided, get minimal web info
        if request_object:
            try:
                # Get remote IP (most important)
                remote_ip = request_object.remote_addr
                if remote_ip and remote_ip != "127.0.0.1":
                    machine_details["rip"] = remote_ip
                    
            except:
                pass
                
        # Convert to JSON with minimal separators
        json_str = json.dumps(machine_details, separators=(',', ':'))
        
        # Ensure it's well under 240 characters
        if len(json_str) > 240:
            # Remove remote IP if present
            if 'rip' in machine_details:
                del machine_details['rip']
            json_str = json.dumps(machine_details, separators=(',', ':'))
            
            # If still too long, use absolute minimal format
            if len(json_str) > 240:
                minimal_details = {
                    "ts": machine_details.get("ts", ""),
                    "ip": machine_details.get("ip", "0.0.0.0")
                }
                json_str = json.dumps(minimal_details, separators=(',', ':'))
                
                # Final fallback: just timestamp and IP
                if len(json_str) > 240:
                    json_str = f'{{"ts":"{machine_details.get("ts", "")}","ip":"{machine_details.get("ip", "0.0.0.0")}"}}'
        
        # Final safety check - if still too long, truncate
        if len(json_str) > 240:
            json_str = json_str[:240]
        
        return json_str
        
    except Exception as e:
        # Return absolute minimal info if anything fails
        return json.dumps({
            "ts": datetime.now().strftime("%Y%m%d%H%M"),
            "ip": "0.0.0.0"
        }, separators=(',', ':'))


def get_full_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f %Z")
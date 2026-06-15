from Utility_Functions.config.constants import JOB_WORK_ITEMS_TABLE, JOB_CARD_MAIN_TABLE, JOB_CARD_SECONDARY_TABLE, JOB_TASK_COSTING_TABLE
from Utility_Functions.config.database import get_odbc_connection_string, row_to_dict, get_table_schema, get_engine
import pyodbc
import uuid
from datetime import datetime
from sqlalchemy import text
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import os
import json
import re
import uuid
from Utility_Functions.config.secure import encrypt, decrypt

# Job card columns used by check_job_task_pending_emails (see job_main_table.txt)
_JOB_CARD_MAIN_PENDING_COLUMNS = (
    "[No_]",
    "[AOP]",
    "[OBJECTIVE OF JOB CARD]",
    "[EXPECTED BENEFITS]",
    "[COMPLETION AFTER DATE OF PASS]",
    "[PREPARED BY]",
    "[CHECKED BY]",
    "[Department Name]",
    "[Approver ID]",
    "[TPT_Job Type]",
    "[Approver Mail ID]",
    "[Creator Mail ID]",
)
JOB_CARD_MAIN_PENDING_SELECT = ", ".join(_JOB_CARD_MAIN_PENDING_COLUMNS)


def fetch_job_tasks(no_):
    tasks_list = []
    
    query_lines = f"""
        SELECT [Job Task No_], [Description], [Recognized Costs Amount]
        FROM {JOB_WORK_ITEMS_TABLE}
        WHERE [Job No_] = ?
        ORDER BY [Job Task No_] ASC
    """
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            cursor.execute(query_lines, (no_,))
            line_items = cursor.fetchall()
            
            if line_items:
                print(f"Found {len(line_items)} Tasks for JOB No: {no_}")
                
                # Convert rows to dictionaries BEFORE closing cursor
                for line_row in line_items:
                    item_dict = row_to_dict(cursor, line_row)
                    tasks_list.append(item_dict)

            else:
                print(f"No Tasks found for JOB No: {no_}")
            
            cursor.close()  # Close cursor AFTER processing all rows
        
    except Exception as e:
        print(f"Error fetching JOB Tasks: {str(e)}")
    
    return tasks_list

def fetch_job_tasks_new(no_):
    tasks_list = []
    
    query_lines = f"""
        SELECT [Job Task No_], [Description]
        FROM {JOB_WORK_ITEMS_TABLE}
        WHERE [Job No_] = ?
        ORDER BY [Job Task No_] ASC
    """
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            cursor.execute(query_lines, (no_,))
            line_items = cursor.fetchall()
            
            if line_items:
                print(f"Found {len(line_items)} Tasks for JOB No: {no_}")
                
                # Convert rows to dictionaries BEFORE closing cursor
                for line_row in line_items:
                    item_dict = row_to_dict(cursor, line_row)
                    tasks_list.append(item_dict)

                # Fetch and totalize costs per (Job No_, Job Task No_) from costing table.
                # Requirement: number of output rows remains same; we only add total cost column.
                task_nos = [str(t.get('Job Task No_', '') or '') for t in tasks_list]
                task_nos = [t for t in task_nos if t]

                if task_nos:
                    placeholders = ", ".join(["?"] * len(task_nos))
                    query_cost = f"""
                        SELECT
                            [Job Task No_],
                            SUM([Total Cost (LCY)]) AS [Total Cost (LCY)]
                        FROM {JOB_TASK_COSTING_TABLE}
                        WHERE [Job No_] = ?
                          AND [Job Task No_] IN ({placeholders})
                        GROUP BY [Job Task No_]
                    """

                    params = [no_] + task_nos
                    cursor.execute(query_cost, params)
                    cost_rows = cursor.fetchall()

                    # Map: job_task_no -> total_cost
                    cost_map = {}
                    for cost_row in cost_rows:
                        cost_dict = row_to_dict(cursor, cost_row)
                        cost_map[str(cost_dict.get('Job Task No_', ''))] = float(cost_dict.get('Total Cost (LCY)', 0) or 0)

                    # Attach totals back onto each task row (default 0 if not present)
                    for task in tasks_list:
                        task_no = str(task.get('Job Task No_', '') or '')
                        task['Total Cost (LCY)'] = cost_map.get(task_no, 0.0)

            else:
                print(f"No Tasks found for JOB No: {no_}")
            
            cursor.close()  # Close cursor AFTER processing all rows
        
    except Exception as e:
        print(f"Error fetching JOB Tasks: {str(e)}")
    
    return tasks_list


def fix_job_task_email_status():
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
                FROM {JOB_CARD_MAIN_TABLE}
                WHERE 
                    [TPT_Approval Status] = 0
                    AND [Approved] = 0
                    AND [Email Send] = '1'
            """
            cursor.execute(fix_query)
            rows_to_fix = cursor.fetchall()

            if rows_to_fix:
                print(f"Found {len(rows_to_fix)} JOB Card records with open status but email is sent. Fixing them now...")
                for row in rows_to_fix:
                    update_fix_query = f"""
                        UPDATE {JOB_CARD_MAIN_TABLE}
                        SET [Email Send] = '0'
                        WHERE [No_] = ?
                    """
                    cursor.execute(update_fix_query, (row[0],))
                    print(f" Updated JOB Card No_: {row[0]}")
                conn.commit()
                print("Fix completed successfully.\n")
            else:
                # No rows to fix, silently continue
                pass

    except Exception as e:
        print(f"Error in fix_job_task_email_status: {str(e)}")
        # Don't raise exception, just log and continue



def check_job_task_pending_emails():
    conn = None
    cursor = None

    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            if not conn:
                print("Failed to connect to database")
                return
            
            # STEP 2: Continue with the original query
            query = f"""
                SELECT {JOB_CARD_MAIN_PENDING_SELECT}
                FROM {JOB_CARD_MAIN_TABLE}
                WHERE 
                    [TPT_Approval Status] = 1
                    AND [Approved] = 0
                    AND (
                        [Email Send] = '0'
                        OR [Email Send] IS NULL
                        OR [Email Send] = ''
                    )
            """
            
            cursor.execute(query)
            pending_cols = [c[0] for c in cursor.description]
            pending_rows = cursor.fetchall()

        if pending_rows:
            print(f"Found {len(pending_rows)} pending emails to process")

            for raw in pending_rows:
                email_row = dict(zip(pending_cols, raw))
                no_ = email_row.get("No_")
                print(f"Processing JOB Task No_: {no_}")

                query_main = f"""
                    SELECT *
                    FROM {JOB_CARD_SECONDARY_TABLE}
                    WHERE [No_] = ?
                """
                cursor.execute(query_main, (no_,))
                job_secondary_data = cursor.fetchone()

                if email_row.get("TPT_Job Type") == 1:
                    job_card_category = "Capital WIP"
                else:
                    job_card_category = " "   

                if job_secondary_data:
                    department = email_row.get("Department Name")
                    job_card_number = email_row.get("No_")
                    apo_type = email_row.get("AOP")
                    plant_name = job_secondary_data[11]
                    objective = email_row.get("OBJECTIVE OF JOB CARD")
                    prepared_by = email_row.get("PREPARED BY")
                    creator_email = email_row.get("Creator Mail ID")
                    checked_by = email_row.get("CHECKED BY")
                    approver_email = email_row.get("Approver Mail ID")
                    job_type = job_card_category
                    date_of_preparation = job_secondary_data[6]
                    expected_benefits = email_row.get("EXPECTED BENEFITS")
                    time_required = email_row.get("COMPLETION AFTER DATE OF PASS")
                    job_description = job_secondary_data[3]
                    category = job_secondary_data[31]

                    header_data = {
                        'department': department,
                        'job_card_number': job_card_number,
                        'aop_type': apo_type,
                        'plant_name': plant_name,
                        'objective': objective,
                        'prepared_by': prepared_by,
                        'creator_email': creator_email,
                        'checked_by': checked_by,
                        'approver_name': '-',
                        'approver_email': approver_email,
                        'date_of_preparation': date_of_preparation,
                        'expected_benefit': expected_benefits,
                        'time_required': time_required,
                        'job_description': job_description,
                        'category': category,
                        'job_card_category': job_type
                    }

                    # Tasks_list = fetch_job_tasks(no_)
                    Tasks_list = fetch_job_tasks_new(no_)

                    print(f"Header Data : {header_data}")
                    print(f"Line Items : {Tasks_list}")
                    print(f"Processing JOB data with No : {no_}")
                    current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

                    try:
                        update_query = f"""
                         UPDATE {JOB_CARD_MAIN_TABLE}
                         SET [Email Send] = 1,
                             [Emaill Status] = 'sent for approval',
                             [Timestamps] = ?
                         WHERE [No_] = ?
                         """
                        cursor.execute(update_query, (current_time,no_))
                        conn.commit()

                        send_job_card_approval_email(header_data, Tasks_list)

                    except Exception as e:
                        print(f"Error sending approval email for JOB Task No_ {no_}: {str(e)}")
                        conn.rollback()
                else:
                    print(f"No secondary data found for JOB Task No_ {no_}")

            print("Successfully processed all pending emails")
        else:
            print("No pending email found in JOB Tasks")

        status_zero_query = f"""
            SELECT [No_]
            FROM {JOB_CARD_MAIN_TABLE}
            WHERE 
                [TPT_Approval Status] = 0
                AND [Approved] = 0
                AND (
                    LOWER([Emaill Status]) = 'pending'
                    OR [Emaill Status] = '' 
                    OR [Emaill Status] IS NULL
                )
            """
        cursor.execute(status_zero_query)
        unsubmitted_records = cursor.fetchall()

        # if unsubmitted_records:
        #     print(f"\n{'='*60}")
        #     print(f"Found {len(unsubmitted_records)} JOB Task records not sent for approval.")
        #     print(f"{'='*60}")
        #     # Show only first 5 records
        #     for record in unsubmitted_records[:5]:
        #         print(f"  JOB Task No_: {record[0]}")
        #     if len(unsubmitted_records) > 5:
        #         print(f"  ... and {len(unsubmitted_records) - 5} more record(s)")
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


def send_job_card_approval_email(data, line_items):
    """Send approval email for a purchase requisition"""
    try:
        print(f"[DEBUG] Preparing to send approval email for document: {data.get('job_card_number', '')}")
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")


        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        recipient_email = data.get('approver_email')
        recipient_name = data.get('approver_name', 'Approver')
        msg['To'] = recipient_email
        subject_text = f"JOB Work Approval Required - {data.get('job_card_number', '')}"
        msg['Subject'] = subject_text
        base_url = os.getenv('BASE_URL', 'http://localhost:5000')
        req_id = data.get('job_card_number', '')
        encrypted_req_id = encrypt(str(req_id))
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear {recipient_name},</p>
        <p>A new job work requires your approval:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>JOB Card No:</strong></td><td>{data.get('job_card_number', '')}</td></tr>
            <tr><td><strong>Date Of Preparation:</strong></td><td>{data.get('date_of_preparation', '')}</td></tr>
            <tr><td><strong>Prepared By:</strong></td><td>{data.get('prepared_by', '')}</td></tr>
            <tr><td><strong>Expected Benefits:</strong></td><td>{data.get('expected_benefit', '')}</td></tr>
        </table>
        <p>Please review the attached PDF and take action:</p>
        <div style='margin: 30px 0;'>
            <a href='{base_url}/job-card-email-approve/{encrypted_req_id}' 
               style='background-color: #28a745; color: white; padding: 12px 24px; text-decoration: none; margin-right: 10px; border-radius: 4px;'>
               ✓ APPROVE
            </a>
            <a href='{base_url}/job-card-email-reject/{encrypted_req_id}' 
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
        if not recipient_email or not isinstance(recipient_email, str) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", recipient_email):
            print(f"❌ Invalid recipient email for document {req_id}: '{recipient_email}'")
            return False
        
        #Generating Purchase order PDF
        from job_card_pdf_generator import generate_job_card_pdf_main

        pdf_buffer = generate_job_card_pdf_main(
            data,
            line_items,
            "Job_Card_Template.html",
            "JOB_Card.pdf"
        )

        pdf_attachment = MIMEApplication(pdf_buffer.getvalue(), _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition','attachment',filename=f'JOB_CARD_{req_id}.pdf')
        msg.attach(pdf_attachment)
        
        print(f"[DEBUG] Attempting SMTP send for document {req_id}...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        try:
            print("Testing PDF generation......")
            server.send_message(msg)
            server.quit()


        except Exception as e:
            print(f"Error sending email for Document No {data.get('No_','')}")
            print(f"Error : {e}")
            return False

        print(f"✅ Approval email sent for document {data.get('No_', '')}")
    
    except Exception as e:
        print(f"Error occured in send email function....")
        print(f"Error : {e}")
        return False

def send_job_card_response_email_to_customer(request_id, response_status, reason=None):
    try:
        sql = f"""SELECT [Creator Mail ID] FROM {JOB_CARD_MAIN_TABLE} WHERE [No_] = ?"""
        try:
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, request_id)
                row = cursor.fetchone()
            if row:
                creator_email = row[0]
        except Exception as e:
            error_string = str(e)
            print(f"Error occurred while fetching creator email: {error_string}")
            response= {'success': False, 'message': f'{error_string} Error in data fetching'}
        

        if not creator_email:
            print(f"ERROR: No creator email found for document {request_id}")
            response= {'success': False, 'message': 'No creator email found'}
            return response
        
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        
        if not all([smtp_server, smtp_user, smtp_password]):
            print("ERROR: Email configuration incomplete")
            response= {'success': False, 'message': 'Email configuration incomplete'}
            return response
        
        # Create email message
        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        msg['To'] = creator_email
        msg['Subject'] = f"Response for Document {request_id}"
        
        # Create email body
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear User,</p>
        <p>Your document has been processed with the following response:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>JOB Card No:</strong></td><td>{request_id}</td></tr>
            <tr><td><strong>Response:</strong></td><td style='color: {"green" if "Approved" in response_status else "red"}'>{response_status}</td></tr>
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
        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()

            # Email sent successfully, perform SQL UPDATE
            sql = f"""
                UPDATE {JOB_CARD_MAIN_TABLE}
                SET [Response Mail Send] = ?
                WHERE [No_] = ?
            """
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, 1, request_id)
                conn.commit()

            print(f"✅ Response email sent to creator {creator_email} for document {request_id}")
            response= {'success': True, 'message': 'Response email sent successfully'}
            return response

        except Exception as e:
            error_string = str(e)
            response= {'success': False, 'message': f'{error_string} Error in data insertion'}
            return response
        

        
    except Exception as e:
        error_string = str(e)
        print(f"ERROR: Failed to send response email to creator: {error_string}")
        import traceback
        traceback.print_exc()
        response= {'success': False, 'message': f'{error_string}'}
        return response

def get_job_card_data_by_id(request_id):
    query = f"SELECT * FROM {JOB_CARD_MAIN_TABLE} WHERE [No_] = :request_id"
    query_for_secondary_data =  f"SELECT * FROM {JOB_CARD_SECONDARY_TABLE} WHERE [No_] = :request_id"
    params = {"request_id": str(request_id)}

    engine = get_engine()
    with engine.connect() as connection:
        result_header = connection.execute(text(query), params).fetchone()
        secondary_data = connection.execute(text(query_for_secondary_data), params).fetchone()

    if result_header:
        # Convert results to dictionaries (if not None)
        header_data = dict(result_header._mapping) if result_header else None
        secondary_data = dict(secondary_data._mapping) if secondary_data else None
        return {
            "header_data": header_data,
            "secondary_data": secondary_data
            }
    else:
        return None

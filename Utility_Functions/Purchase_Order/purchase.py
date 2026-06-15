from Utility_Functions.config.constants import PURCHASE_LINE_TABLE, PURCHASE_EMAIL_TABLE, PURCHASE_HEADER_MAIN, PURCHASE_HEADER_STATE, PURCHASE_HEADER_GST, APPROVAL_ENTRY_TABLE
from Utility_Functions.config.database import get_odbc_connection_string, get_table_schema, row_to_dict, get_state_info, get_engine
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

# Purchase Header columns used by check_purchase_order_pending_emails (see purchase_main_table.txt)
_PURCHASE_HEADER_MAIN_COLUMNS = (
    "[Order Date]",
    "[Pay-to Vendor No_]",
    "[Buy-from Vendor Name]",
    "[Buy-from Address]",
    "[Buy-from Contact]",
    "[Buy-from Post Code]",
    "[Payment Terms Code]",
)
PURCHASE_HEADER_MAIN_SELECT = ", ".join(_PURCHASE_HEADER_MAIN_COLUMNS)


def fetch_purchase_line_items(no_):
    items_list = []
    
    query_lines = f"""
        SELECT [Description], [Unit of Measure], [Direct Unit Cost], [Quantity], [Amount]
        FROM {PURCHASE_LINE_TABLE}
        WHERE [Document No_] = ?
        ORDER BY [Line No_] ASC
    """
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            cursor.execute(query_lines, (no_,))
            line_items = cursor.fetchall()
            
            if line_items:
                print(f"Found {len(line_items)} line items for Purchase No_: {no_}")
                
                # Convert rows to dictionaries BEFORE closing cursor
                for line_row in line_items:
                    item_dict = row_to_dict(cursor, line_row)
                    items_list.append(item_dict)
                    
                    desc = item_dict.get('Description', 'N/A')
                    qty = item_dict.get('Quantity', 0)
                    unit = item_dict.get('Unit of Measure', '')
                    cost = item_dict.get('Direct Unit Cost', 0)
                    
                    print(f"  - {desc}: {qty} {unit} @ {cost}")
            else:
                print(f"No line items found for Purchase No_: {no_}")
            
            cursor.close()  # Close cursor AFTER processing all rows
        
    except Exception as e:
        print(f"Error fetching line items: {str(e)}")
    
    return items_list

def extract_name_from_email(email):
    if '@' not in email:
        return ""
    name_part = email.split('@')[0].strip()
    if '.' in name_part:
        words = name_part.split('.')
        if len(words) >= 2:
            name_words = [word.capitalize() for word in words[:2]]
            return ' '.join(name_words)
    return name_part.capitalize()
 
def fix_purchase_order_email_status():
    conn = None
    cursor = None
    
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            if not conn:
                print("Failed to connect to database")
                return
            
            fix_query = f"""
                SELECT pe.[No_]
                FROM {PURCHASE_EMAIL_TABLE} pe
                INNER JOIN {PURCHASE_HEADER_MAIN} ost ON pe.[No_] = ost.[No_]
                WHERE 
                    ost.[Status] = 0
                    AND pe.[Email Send] = '1'
            """
            cursor.execute(fix_query)
            rows_to_fix = cursor.fetchall()
            
            if rows_to_fix:
                print(f"Found {len(rows_to_fix)} records with open status but email is sent. Fixing them now...")
                for row in rows_to_fix:
                    update_fix_query = f"""
                        UPDATE {PURCHASE_EMAIL_TABLE}
                        SET [Email Send] = '0'
                        WHERE [No_] = ?
                    """
                    cursor.execute(update_fix_query, (row[0],))
                    print(f"  Updated Purchase No_: {row[0]}")
                conn.commit()
                print("Fix completed successfully.\n")
            else:
                # No rows to fix, silently continue
                pass
                
    except Exception as e:
        print(f"Error in fix_purchase_order_email_status: {str(e)}")
        # Don't raise exception, just log and continue


def check_purchase_order_pending_emails():
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
                SELECT 
                  pe.[No_],
                  pe.[Approver Mail ID],
                  pe.[Creator Mail ID]
                FROM {PURCHASE_EMAIL_TABLE} pe
                INNER JOIN {PURCHASE_HEADER_MAIN} ost ON pe.[No_] = ost.[No_]
                WHERE 
                    ost.[Status] = 2
                    AND (
                        pe.[Email Send] = '0'
                        OR pe.[Email Send] IS NULL
                        OR pe.[Email Send] = ''
                    )
            """
            
            cursor.execute(query)
            pending_emails = cursor.fetchall()
            
            if pending_emails:
                print(f"Found {len(pending_emails)} pending emails to process")

                for email_row in pending_emails:
                    no_ = email_row[0]  # Assuming column 0 is "No_"
                    print(f"Processing Purchase No_: {no_}")

                    # Fetch related data from the main header table
                    cursor2 = conn.cursor()
                    query_main = f"""
                            SELECT {PURCHASE_HEADER_MAIN_SELECT}
                            FROM {PURCHASE_HEADER_MAIN}
                            WHERE [No_] = ?
                        """
                    cursor2.execute(query_main, (no_,))
                    main_colnames = [c[0] for c in cursor2.description]
                    main_row = cursor2.fetchone()
                    main_rd = dict(zip(main_colnames, main_row)) if main_row else {}
                    
                    state_query = f"""
                        SELECT [State]
                        FROM {PURCHASE_HEADER_STATE}
                        WHERE [No_] = ?
                    """
                    cursor2.execute(state_query, (no_,))
                    state_data = cursor2.fetchone()

                    print(f"   State Data: {state_data}")
                    
                    gst_query = f"""
                        SELECT *
                        FROM {PURCHASE_HEADER_GST}
                        WHERE [No_] = ?
                    """
                    cursor2.execute(gst_query, (no_,))
                    gst_data = cursor2.fetchone()

                    state_info = get_state_info(state_data[0]) if state_data else {}
                    order_date = main_rd.get("Order Date")
                    buy_from_vendor_no = main_rd.get("Pay-to Vendor No_")
                    buy_from_vendor_name = main_rd.get("Buy-from Vendor Name")
                    buy_from_address = main_rd.get("Buy-from Address")
                    buy_from_city = main_rd.get("Buy-from Contact")
                    buy_from_post_code = main_rd.get("Buy-from Post Code")
                    state = state_info.get("state_name", "") if state_info else ""
                    vendor_gst_reg_no = gst_data[16] if gst_data else ""
                    payment_terms_code = main_rd.get("Payment Terms Code")
                    approver_email = email_row[1]
                    creator_email = email_row[2]

                    header_data = {
                        'Document Type': 1,  # Default to 1
                        'No_': email_row[0],
                        'Order Date': order_date,
                        'Buy-from Vendor No_': buy_from_vendor_no,
                        'Buy-from Vendor Name': buy_from_vendor_name,
                        'Buy-from Address': buy_from_address,
                        'Buy-from City': buy_from_city,
                        'Buy-from Post Code': buy_from_post_code,
                        'State': state,
                        'Vendor GST Reg_ No_': vendor_gst_reg_no,
                        'Payment Terms Code': payment_terms_code,
                        'Approver Mail ID': approver_email,
                        'Creator Mail ID': creator_email,
                        'Creator Name': extract_name_from_email(creator_email)
                    }

                    items_list = fetch_purchase_line_items(email_row[0])

                    print(f"Header Data : {header_data}")
                    print(f"Line Items : {items_list}")
                    print(f"Processing data with No : {email_row[0]}")

                    current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

                    approver_email_id = email_row[1]

                    if approver_email_id and approver_email_id.strip():
                        # Send email first; update DB only on successful send
                        email_sent = send_purchase_approval_email(header_data, items_list)
                        if email_sent:
                            update_query = f"""
                             UPDATE {PURCHASE_EMAIL_TABLE}
                             SET [Email Send] = 1,
                                 [Emaill Status] = 'sent for approval',
                                 [Timestamps] = ?
                             WHERE [No_] = ?
                             """
                            cursor.execute(update_query, (current_time, email_row[0]))
                            conn.commit()
                            print(f"✅ Successfully processed Domestic order: {no_}")
                        else:
                            print(
                                f"❌ Email sending failed for Order No: {email_row[0]}; "
                                "database not updated."
                            )
                    else:
                        print(f"❌ Invalid Approver Email ID for Order No: {email_row[0]}")
                
                conn.commit()
                print("Successfully processed all pending emails")
            else:
                print("No pending email found in purchase orders")

            # STEP 3: Continue with status zero query
            # status_zero_query = f"""
            #     SELECT pe.[No_]
            #     FROM {PURCHASE_EMAIL_TABLE} pe
            #     INNER JOIN {PURCHASE_HEADER_MAIN} ost ON pe.[No_] = ost.[No_]
            #     WHERE 
            #         ost.[Status] = 0
            #         AND (
            #             pe.[Emaill Status] = 'pending' 
            #             OR pe.[Emaill Status] = 'Pending'
            #             OR pe.[Emaill Status] = '' 
            #             OR pe.[Emaill Status] IS NULL
            #         )
            #     """
            # cursor.execute(status_zero_query)
            # unsubmitted_records = cursor.fetchall()
            
            # if unsubmitted_records:
            #     print(f"\n{'='*60}")
            #     print(f"Found {len(unsubmitted_records)} Purchase order records not sent for approval.")
            #     print(f"{'='*60}")
            # # Show only first 5 records
            # for record in unsubmitted_records[:5]:
            #     print(f"  Purchase Order No_: {record[0]}")
            # if len(unsubmitted_records) > 5:
            #     print(f"  ... and {len(unsubmitted_records) - 5} more record(s)")
            # print(f"{'='*60}\n")
                
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


def send_purchase_approval_email(data, line_items):
    """Send approval email for a purchase requisition"""
    try:
        print(f"[DEBUG] Preparing to send approval email for document: {data.get('No_', '')}")
        print(f"[DEBUG] Full purchase_req dict: {json.dumps(data, default=str, indent=2)}")
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
    
        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        recipient_email = data.get('Approver Mail ID')
        print(f"[DEBUG] Recipient email for document {data.get('No_', '')}: {recipient_email}")
        msg['To'] = recipient_email
        # msg['To'] = "divyeshparmar0909@gmail.com" # For testing purposes, override recipient email
        subject_text = f"Purchase Order Approval Required - {data.get('No_', '')}"
        msg['Subject'] = subject_text
        base_url = os.getenv('BASE_URL', 'http://localhost:5000')
        req_id = data.get('No_', '')
        encrypted_req_id = encrypt(str(req_id))
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear Approver,</p>
        <p>A new purchase request requires your approval:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{data.get('No_', '')}</td></tr>
            <tr><td><strong>Order Date:</strong></td><td>{data.get('Order Date', '')}</td></tr>
            <tr><td><strong>Vendor:</strong></td><td>{data.get('Buy-from Vendor Name', '')}</td></tr>
            <tr><td><strong>Vendor GST:</strong></td><td>{data.get('Vendor GST Reg_ No_', '')}</td></tr>
        </table>
        <p>Please review the attached PDF and take action:</p>
        <div style='margin: 30px 0;'>
            <a href='{base_url}/purchase-email-approve/{encrypted_req_id}' 
               style='background-color: #28a745; color: white; padding: 12px 24px; text-decoration: none; margin-right: 10px; border-radius: 4px;'>
               ✓ APPROVE
            </a>
            <a href='{base_url}/purchase-email-reject/{encrypted_req_id}' 
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
        
        #Generating Purchase order PDF
        from purchase_order_pdf_generator import generate_purchase_order_pdf_main

        pdf_buffer = generate_purchase_order_pdf_main(
            data,
            line_items,
            "purchase_order.pdf"
        )

        from email.mime.application import MIMEApplication
        pdf_attachment = MIMEApplication(pdf_buffer.getvalue(), _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition','attachment',filename=f'Purchase_Order_{req_id}.pdf')
        msg.attach(pdf_attachment)
        
        print(f"[DEBUG] Attempting SMTP send for document {req_id}...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
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
        return True
    
    except Exception as e:
        print(f"Error occured in send email function....")
        print(f"Error : {e}")
        return False

def send_purchase_response_email_to_creator(request_id, response_status, reason=None):
    """Send response email to creator after approval/rejection"""
    try:
        sql = f"""SELECT [Creator Mail ID] FROM {PURCHASE_EMAIL_TABLE} WHERE [No_] = ?"""
        try:
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                 cursor = conn.cursor()
                 cursor.execute(sql, request_id)
                 row = cursor.fetchone()
            if row:
                creator_email = row[0]

        except Exception as e:
            print(f"Error occurred while fetching creator email: {e}")
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
        msg['Subject'] = f"Response for Document {request_id}"
        
        # Create email body
        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <h2>Document Response Update</h2>
        <p>Dear User,</p>
        <p>Your document has been processed with the following response:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{request_id}</td></tr>
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
                UPDATE {PURCHASE_EMAIL_TABLE}
                SET [Response Mail Send] = ?
                WHERE [No_] = ?
            """
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, 1, request_id)
                conn.commit()
        except Exception as e:
            print("An error occurred:", e)
        
        print(f"✅ Response email sent to creator {creator_email} for document {request_id}")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to send response email to creator: {e}")
        return False

def get_purchase_request_data_by_id(request_id):
    query = f"SELECT * FROM {PURCHASE_HEADER_MAIN} WHERE [No_] = :request_id"
    query_for_email_data =  f"SELECT * FROM {PURCHASE_EMAIL_TABLE} WHERE [No_] = :request_id"
    params = {"request_id": str(request_id)}

    engine = get_engine()
    with engine.connect() as connection:
        result_header = connection.execute(text(query), params).fetchone()
        result_email = connection.execute(text(query_for_email_data), params).fetchone()

    if result_header or result_email:
        # Convert results to dictionaries (if not None)
        header_data = dict(result_header._mapping) if result_header else None
        email_data = dict(result_email._mapping) if result_email else None
        return {
            "header_data": header_data,
            "email_data": email_data
        }
    return None

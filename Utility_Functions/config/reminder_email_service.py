import pyodbc
from datetime import datetime, timedelta
import logging
import os
import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ReminderEmailService:
    """Service to handle reminder email triggers based on timestamp and email status"""
    
    def __init__(
        self,
        table_name: str,
        reminder_duration_hours: int = 24,
        reminder_duration_days: int = 0,
        reminder_duration_minutes: int = 0,
        data_table_name: str = None,
        status_column_name: str = "status",
        timestamp_column: str = "Timestamps",
        email_status_column: str = "Emaill Status",
        order_number_column: str = "No_",
        approver_email_column: str = "Approver Mail ID"
    ):
        self.table_name = table_name  # Email data table
        self.data_table_name = data_table_name  # Data table with status column
        self.status_column_name = status_column_name  # Status column name in data table
        # Calculate total reminder duration
        self.reminder_duration = timedelta(
            days=reminder_duration_days,
            hours=reminder_duration_hours,
            minutes=reminder_duration_minutes
        )
        self.timestamp_column = timestamp_column
        self.email_status_column = email_status_column
        self.order_number_column = order_number_column
        self.approver_email_column = approver_email_column
        
        # Valid email statuses that should stop reminders (case-insensitive)
        # Note: "reminder sent" is not included so reminders can be sent repeatedly
        self.valid_statuses = {"approved", "rejected"}
        
        # Check if table has specific keywords for special handling
        self.is_special_table = self._is_special_table()
        
        # Log the reminder duration being used
        total_hours = self.reminder_duration.total_seconds() / 3600
        logger.info(
            f"ReminderEmailService initialized with duration: "
            f"{reminder_duration_days} days, {reminder_duration_hours} hours, {reminder_duration_minutes} minutes "
            f"(Total: {total_hours:.2f} hours / {self.reminder_duration.total_seconds() / 60:.2f} minutes)"
        )
        print(
            f"[REMINDER SERVICE] Using reminder duration: "
            f"{reminder_duration_days} days, {reminder_duration_hours} hours, {reminder_duration_minutes} minutes "
            f"(Total: {total_hours:.2f} hours)"
        )
    
    def _is_special_table(self) -> bool:
        """
        Check if table name is the IM Purchase Requisition table that requires special handling.
        Uses specific patterns to avoid false positives with other tables.
        
        Target table pattern: "TPT_IM Purch_ Req_ Header"
        """
        table_name_lower = self.table_name.lower()
        
        # Most specific pattern: Look for "TPT_IM Purch_ Req_ Header" pattern
        # This is the exact table structure we need
        specific_patterns = [
            "tpt_im purch_ req_ header",  # Exact pattern (case-insensitive)
            "tpt_im purch req header",    # Without underscores
        ]
        
        # Check if any specific pattern matches
        for pattern in specific_patterns:
            if pattern in table_name_lower:
                return True
        
        # Secondary check: Look for combination of "TPT_IM" and "Purch_ Req_" together
        # This ensures we match the IM Purchase table specifically
        if "tpt_im" in table_name_lower:
            # Must also contain purchase requisition indicators
            req_indicators = ["purch_ req_", "purch req", "requisition"]
            if any(indicator in table_name_lower for indicator in req_indicators):
                # Additional check: should NOT be a line table
                if "line" not in table_name_lower:
                    return True
        
        return False
    
    @staticmethod
    def get_odbc_connection_string():
        """Get ODBC connection string from DATABASE_URL"""
        conn_str = os.getenv("DATABASE_URL")
        if not conn_str:
            raise ValueError("DATABASE_URL not set in environment")
        
        # Extract ODBC connection string from SQLAlchemy URL
        # Format: mssql+pyodbc:///?odbc_connect=<actual_connection_string>
        if "odbc_connect=" in conn_str:
            # Extract the part after odbc_connect=
            odbc_conn_str = conn_str.split("odbc_connect=")[1]
            # URL decode common characters
            odbc_conn_str = odbc_conn_str.replace("%20", " ").replace("%3D", "=").replace("%3B", ";")
        else:
            # Fallback: try to use the connection string as-is
            odbc_conn_str = conn_str
    
        return odbc_conn_str

    
    def _should_send_reminder(self, timestamp_str: str, email_status: str) -> bool:
        try:
            # Validate timestamp string
            if not timestamp_str or not timestamp_str.strip():
                logger.debug("Empty or invalid timestamp string. Skipping.")
                return False
            
            # Parse timestamp (handle milliseconds)
            # Format: 2025-12-05T14:28:01.502
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            
            # Get current time
            current_time = datetime.now()
            
            # Check if timestamp is greater than 2025-12-09T00:00:00
            threshold_date = datetime(2025, 12, 20, 0, 0, 0)
            if timestamp <= threshold_date:
                logger.debug(f"Timestamp {timestamp} is before threshold. Skipping.")
                return False
            
            # Check if enough time has passed (timestamp + reminder_duration < current_time)
            reminder_trigger_time = timestamp + self.reminder_duration
            if current_time < reminder_trigger_time:
                time_remaining = reminder_trigger_time - current_time
                logger.info(
                    f"Not enough time passed for reminder. Last timestamp: {timestamp}, "
                    f"Reminder will trigger at {reminder_trigger_time}, "
                    f"Time remaining: {time_remaining}"
                )
                return False
            
            logger.info(
                f"Reminder should be sent. Last timestamp: {timestamp}, "
                f"Current time: {current_time}, "
                f"Time since last reminder: {current_time - timestamp}"
            )
            
            # Check email status (case-insensitive)
            # Only skip if status is approved or rejected (reminder sent is allowed for repeated reminders)
            # For special tables, allow "pending by accountants", "approved by acc"
            status_lower = email_status.strip().lower() if email_status else ""
            logger.info(f"Checking email status: '{email_status}' (lowercase: '{status_lower}')")
            
            # For special tables, allow specific statuses
            if self.is_special_table:
                allowed_statuses = ["pending by accountants", "approved by acc", "sent to hod for approval"]
                if status_lower in allowed_statuses:
                    logger.info(f"Email status '{email_status}' is allowed for special table. Proceeding.")
                    return True
                # Still skip if it's just "approved" or "rejected" (without "by acc")
                if status_lower in self.valid_statuses:
                    logger.info(f"Email status '{email_status}' indicates final status (approved/rejected). Skipping.")
                    return False
            else:
                # For non-special tables, use standard logic
                if status_lower in self.valid_statuses:
                    logger.info(f"Email status '{email_status}' indicates final status (approved/rejected). Skipping.")
                    return False
                
            return True
            
        except Exception as e:
            logger.info(f"Error checking reminder conditions: {str(e)}")
            return False
    
    def _get_email_subject(self, order_number: str) -> str:
        """
        Determine email subject based on table name and order number prefix.
        
        Args:
            order_number: The order number to include in the subject
            
        Returns:
            str: The email subject text
        """
        table_name_lower = self.table_name.lower()
        
        # Sales Order table - check for SE/SD prefixes
        if "sales" in table_name_lower and "header" in table_name_lower:
            if order_number.startswith('SE'):
                return f"Sales Export Order Approval Required - {order_number}"
            elif order_number.startswith('SD'):
                return f"Sales Domestic Order Approval Required - {order_number}"
            else:
                return f"Sales Order Approval Required - {order_number}"
        
        # Purchase Order table
        elif "purchase" in table_name_lower and "header" in table_name_lower:
            return f"Purchase Order Approval Required - {order_number}"
        
        # Job Task table
        elif "job" in table_name_lower:
            return f"Job Task Approval Required - {order_number}"
        
        # IM Purchase table
        elif "im purch" in table_name_lower or "purch_ req" in table_name_lower:
            return f"IM Purchase Requisition Approval Required - {order_number}"
        
        # Default fallback for any other table
        else:
            return f"Order Approval Required - {order_number}"
    
    def _send_reminder_email(self, order_number: str, conn, email_status: str = None) -> bool:
        try:
            # For special tables, determine recipients based on email status
            if self.is_special_table and email_status:
                status_lower = email_status.strip().lower() if email_status else ""
                
                # If status is "pending by accountants", send to accountants
                if status_lower == "pending by accountants":
                    return self._send_reminder_to_accountants(order_number, conn)
                
                # If status is "sent to HOD for approval" send to approver
                elif status_lower == "sent to hod for approval":
                    logger.info(f"Sending reminder to approver for order {order_number} with status: {email_status}")
                    return self._send_reminder_to_approver(order_number, conn)
            
            # Default behavior: send to approver
            return self._send_reminder_to_approver(order_number, conn)
            
        except Exception as e:
            logger.error(f"Error sending reminder email for order {order_number}: {str(e)}")
            return False
    
    def _send_reminder_to_approver(self, order_number: str, conn) -> bool:
        """Send reminder email to approver"""
        try:
            # Fetch approver email from database
            cursor = conn.cursor()
            fetch_query = f"""
                SELECT [{self.approver_email_column}]
                FROM {self.table_name}
                WHERE [{self.order_number_column}] = ?
            """
            cursor.execute(fetch_query, (order_number,))
            row = cursor.fetchone()
            
            if not row or not row[0]:
                logger.error(f"No approver email found for order {order_number}")
                return False
            
            approver_email = str(row[0]).strip()
            
            # Validate email format
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", approver_email):
                logger.error(f"Invalid approver email format for order {order_number}: '{approver_email}'")
                return False
            
            # Get SMTP configuration
            smtp_server = os.getenv("SMTP_SERVER")
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
            smtp_user = os.getenv("SMTP_USERNAME")
            smtp_password = os.getenv("SMTP_PASSWORD")
            
            # Validate required SMTP settings
            if not all([smtp_server, smtp_user, smtp_password]):
                logger.error("Missing SMTP configuration")
                return False
            
            # Determine subject based on table name and order number prefix
            subject_text = self._get_email_subject(order_number)
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_user
            msg['To'] = approver_email
            msg['Subject'] = subject_text
            
            # Create email body
            body_html = f"""
            <html>
            <body style='font-family: Arial, sans-serif; line-height: 1.6;'>
                <h2 style='color: #333;'>Reminder: Approval Required</h2>
                <p>Dear Approver,</p>
                <p>This is a reminder that the following order is pending your approval:</p>
                <p style='font-weight: bold; font-size: 16px; color: #0066cc;'>Order Number: {order_number}</p>
                <p>Please review and take appropriate action at your earliest convenience.</p>
                <p>Thank you.</p>
            </body>
            </html>
            """
            
            # Attach HTML body
            msg.attach(MIMEText(body_html, 'html'))
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
            
            logger.info(f"Reminder email sent successfully for order: {order_number} to approver {approver_email}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending reminder email to approver for order {order_number}: {str(e)}")
            return False
    
    def _send_reminder_to_accountants(self, order_number: str, conn) -> bool:
        """Send reminder email to accountants (acc1_id and acc2_id)"""
        try:
            # Fetch accountant emails from database
            cursor = conn.cursor()
            fetch_query = f"""
                SELECT [Account dept Approver 1], [Account dept Approver 2]
                FROM {self.table_name}
                WHERE [{self.order_number_column}] = ?
            """
            cursor.execute(fetch_query, (order_number,))
            row = cursor.fetchone()
            
            if not row:
                logger.error(f"No record found for order {order_number}")
                return False
            
            acc1_email = str(row[0]).strip() if row[0] else None
            acc2_email = str(row[1]).strip() if row[1] else None
            
            # Collect valid email addresses
            recipient_emails = []
            if acc1_email and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", acc1_email):
                recipient_emails.append(acc1_email)
            if acc2_email and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", acc2_email):
                recipient_emails.append(acc2_email)
            
            if not recipient_emails:
                logger.error(f"No valid accountant emails found for order {order_number}")
                return False
            
            # Get SMTP configuration
            smtp_server = os.getenv("SMTP_SERVER")
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
            smtp_user = os.getenv("SMTP_USERNAME")
            smtp_password = os.getenv("SMTP_PASSWORD")
            
            # Validate required SMTP settings
            if not all([smtp_server, smtp_user, smtp_password]):
                logger.error("Missing SMTP configuration")
                return False
            
            # Determine subject based on table name and order number prefix
            subject_text = self._get_email_subject(order_number)
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_user
            msg['To'] = ', '.join(recipient_emails)
            msg['Subject'] = subject_text
            
            # Create email body
            body_html = f"""
            <html>
            <body style='font-family: Arial, sans-serif; line-height: 1.6;'>
                <h2 style='color: #333;'>Reminder: Approval Required</h2>
                <p>Dear Accountant,</p>
                <p>This is a reminder that the following order is pending your review:</p>
                <p style='font-weight: bold; font-size: 16px; color: #0066cc;'>Order Number: {order_number}</p>
                <p>Please review and take appropriate action at your earliest convenience.</p>
                <p>Thank you.</p>
            </body>
            </html>
            """
            
            # Attach HTML body
            msg.attach(MIMEText(body_html, 'html'))
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
            
            logger.info(f"Reminder email sent successfully for order: {order_number} to accountants {', '.join(recipient_emails)}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending reminder email to accountants for order {order_number}: {str(e)}")
            return False
    
    def _update_email_status_atomic(self, conn, order_number: str, old_timestamp_str: str) -> bool:
        try:
            cursor = conn.cursor()
            
            # Parse the old timestamp to calculate the trigger time
            try:
                old_timestamp = datetime.fromisoformat(old_timestamp_str.replace("Z", "+00:00"))
                reminder_trigger_time = old_timestamp + self.reminder_duration
                current_time = datetime.now()
                
                # Format trigger time for SQL comparison
                trigger_time_str = reminder_trigger_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]
                current_time_str = current_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]
            except Exception as e:
                logger.error(f"Error parsing timestamp for order {order_number}: {str(e)}")
                return False
            
            # Generate new timestamp in format: 2025-11-20T17:22:30.949
            new_timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]
            
            # Atomic update: Only update if current time >= trigger time AND status is not approved/rejected
            # This ensures only one process can successfully update the record
            # We check that the timestamp matches (to ensure we're updating the right version)
            # and that enough time has passed using DATEDIFF
            # For special tables, allow "pending by accountants", "approved by acc"
            if self.is_special_table:
                update_query = f"""
                    UPDATE {self.table_name}
                    SET [{self.timestamp_column}] = ?
                    WHERE [{self.order_number_column}] = ?
                        AND (
                            LOWER(LTRIM(RTRIM([{self.email_status_column}]))) = 'pending by accountants'
                            OR LOWER(LTRIM(RTRIM([{self.email_status_column}]))) = 'approved by acc'
                            OR LOWER(LTRIM(RTRIM([{self.email_status_column}]))) = 'sent to hod for approval'
                        )
                        AND [{self.timestamp_column}] = ?
                        AND DATEDIFF(SECOND, 
                            TRY_CAST([{self.timestamp_column}] AS DATETIME2), 
                            GETDATE()) >= ?
                """
            else:
                update_query = f"""
                    UPDATE {self.table_name}
                    SET [{self.timestamp_column}] = ?
                    WHERE [{self.order_number_column}] = ?
                        AND LOWER(LTRIM(RTRIM([{self.email_status_column}]))) NOT IN ('approved', 'rejected')
                        AND [{self.timestamp_column}] = ?
                        AND DATEDIFF(SECOND, 
                            TRY_CAST([{self.timestamp_column}] AS DATETIME2), 
                            GETDATE()) >= ?
                """
            
            # Calculate seconds in reminder_duration
            total_seconds = int(self.reminder_duration.total_seconds())
            
            cursor.execute(update_query, (new_timestamp, order_number, old_timestamp_str, total_seconds))
            rows_affected = cursor.rowcount
            conn.commit()
            
            if rows_affected > 0:
                logger.info(f"Atomically updated timestamp for order {order_number}")
                return True
            else:
                logger.debug(f"Order {order_number} was not updated (condition not met or already processed by another instance)")
                return False
                
        except Exception as e:
            logger.error(f"Error in atomic update for order {order_number}: {str(e)}")
            if conn:
                conn.rollback()
            return False
    
    def process_reminders(self, connection_func=None) -> dict:
        results = {
            "total_processed": 0,
            "reminders_sent": 0,
            "errors": 0,
            "orders_processed": []
        }
        
        conn = None
        try:
            # Get database connection
            if connection_func:
                conn = connection_func()
            else:
                conn = pyodbc.connect(self.get_odbc_connection_string())
            
            if not conn:
                logger.error("Failed to connect to database")
                results["errors"] += 1
                return results
            
            cursor = conn.cursor()
        
            if self.data_table_name:
                # Join with data table and check status = 2 in the data table
                if self.is_special_table:
                    # Special conditions for tables with keywords (e.g., IM Purchase)
                    # Include records with status "pending by accountants", "approved by acc"
                    select_query = f"""
                        SELECT 
                            e.[{self.order_number_column}],
                            e.[{self.timestamp_column}],
                            e.[{self.email_status_column}]
                        FROM {self.table_name} e
                        INNER JOIN {self.data_table_name} d
                            ON e.[{self.order_number_column}] = d.[{self.order_number_column}]
                        WHERE e.[{self.timestamp_column}] IS NOT NULL 
                            AND e.[{self.timestamp_column}] <> ''
                            AND LEN(LTRIM(RTRIM(e.[{self.timestamp_column}]))) > 0
                            AND d.[{self.status_column_name}] = 2
                            AND (
                                LOWER(LTRIM(RTRIM(e.[{self.email_status_column}]))) = 'pending by accountants'
                                OR LOWER(LTRIM(RTRIM(e.[{self.email_status_column}]))) = 'approved by acc'
                                OR LOWER(LTRIM(RTRIM(e.[{self.email_status_column}]))) = 'sent to hod for approval'
                            )
                        ORDER BY e.[{self.order_number_column}] DESC
                    """
                else:
                    # Standard query for other tables
                    select_query = f"""
                        SELECT 
                            e.[{self.order_number_column}],
                            e.[{self.timestamp_column}],
                            e.[{self.email_status_column}]
                        FROM {self.table_name} e
                        INNER JOIN {self.data_table_name} d
                            ON e.[{self.order_number_column}] = d.[{self.order_number_column}]
                        WHERE e.[{self.timestamp_column}] IS NOT NULL 
                            AND e.[{self.timestamp_column}] <> ''
                            AND LEN(LTRIM(RTRIM(e.[{self.timestamp_column}]))) > 0
                            AND (
                                e.[{self.email_status_column}] IS NULL 
                                OR LOWER(LTRIM(RTRIM(e.[{self.email_status_column}]))) NOT IN ('approved', 'rejected')
                            )
                            AND d.[{self.status_column_name}] = 2
                        ORDER BY e.[{self.order_number_column}] DESC
                    """
            else:
                # Query from table_name only - all columns including status are in the same table
                if self.is_special_table:
                    # Special conditions for tables with keywords (e.g., IM Purchase)
                    select_query = f"""
                        SELECT 
                            [{self.order_number_column}],
                            [{self.timestamp_column}],
                            [{self.email_status_column}]
                        FROM {self.table_name}
                        WHERE [{self.timestamp_column}] IS NOT NULL 
                            AND [{self.timestamp_column}] <> ''
                            AND LEN(LTRIM(RTRIM([{self.timestamp_column}]))) > 0
                            AND [{self.status_column_name}] = 2
                            AND (
                                LOWER(LTRIM(RTRIM([{self.email_status_column}]))) = 'pending by accountants'
                                OR LOWER(LTRIM(RTRIM([{self.email_status_column}]))) = 'approved by acc'
                                OR LOWER(LTRIM(RTRIM([{self.email_status_column}]))) = 'sent to hod for approval'
                            )
                        ORDER BY [{self.order_number_column}] DESC
                    """
                else:
                    # Standard query for other tables
                    select_query = f"""
                        SELECT 
                            [{self.order_number_column}],
                            [{self.timestamp_column}],
                            [{self.email_status_column}]
                        FROM {self.table_name}
                        WHERE [{self.timestamp_column}] IS NOT NULL 
                            AND [{self.timestamp_column}] <> ''
                            AND LEN(LTRIM(RTRIM([{self.timestamp_column}]))) > 0
                            AND (
                                [{self.email_status_column}] IS NULL 
                                OR LOWER(LTRIM(RTRIM([{self.email_status_column}]))) NOT IN ('approved', 'rejected')
                            )
                            AND [{self.status_column_name}] = 2
                        ORDER BY [{self.order_number_column}] DESC
                    """
            
            table_info = f"{self.table_name}"
            if self.data_table_name:
                table_info += f" JOIN {self.data_table_name}"
            logger.info(f"Executing query on table(s): {table_info}")
            
            # Print reminder duration being used for this processing run
            total_hours = self.reminder_duration.total_seconds() / 3600
            print(
                f"[REMINDER SERVICE] Processing reminders with duration: "
                f"{self.reminder_duration.days} days, {int(self.reminder_duration.seconds // 3600)} hours, "
                f"{int((self.reminder_duration.seconds % 3600) // 60)} minutes "
                f"(Total: {total_hours:.2f} hours)"
            )
            logger.info(
                f"Processing reminders with duration: "
                f"{self.reminder_duration.days} days, {int(self.reminder_duration.seconds // 3600)} hours, "
                f"{int((self.reminder_duration.seconds % 3600) // 60)} minutes "
                f"(Total: {total_hours:.2f} hours)"
            )
            
            cursor.execute(select_query)
            rows = cursor.fetchall()
            
            logger.info(f"Found {len(rows)} records to process")
            
            # Track processed order numbers in this batch to prevent duplicates
            processed_in_batch = set()
            
            # Process each record
            for row in rows:
                try:
                    order_number = str(row[0])
                    timestamp = str(row[1])
                    email_status = str(row[2]) if row[2] else ""
                    
                    results["total_processed"] += 1
                    
                    # Skip if we've already processed this order in this batch (prevent duplicates)
                    if order_number in processed_in_batch:
                        logger.info(f"Order {order_number} already processed in this batch. Skipping duplicate.")
                        continue
                    
                    # Re-fetch current timestamp and status to ensure we have the latest data
                    # This prevents processing with stale data from the initial query
                    verify_cursor = conn.cursor()
                    verify_query = f"""
                        SELECT [{self.timestamp_column}], [{self.email_status_column}]
                        FROM {self.table_name}
                        WHERE [{self.order_number_column}] = ?
                    """
                    verify_cursor.execute(verify_query, (order_number,))
                    verify_row = verify_cursor.fetchone()

                    logger.info(f"Verifying order {order_number}: fetched row: {verify_row}")
                    
                    if not verify_row:
                        logger.info(f"Order {order_number} not found in database. Skipping.")
                        continue
                    
                    # Get fresh timestamp and status
                    current_timestamp_str = str(verify_row[0]) if verify_row[0] else ""
                    current_status = str(verify_row[1]).strip().lower() if verify_row[1] else ""
                    
                    # Skip if status changed to approved or rejected (unless it's special status for special tables)
                    if self.is_special_table:
                        # For special tables, only skip if it's just "approved" or "rejected" (not "approved by acc")
                        # Allow "pending by accountants", "approved by acc"
                        if current_status in ('approved', 'rejected'):
                            logger.info(f"Order {order_number} status changed to '{current_status}'. Skipping.")
                            continue
                    else:
                        # For non-special tables, use standard logic
                        if current_status in ('approved', 'rejected'):
                            logger.info(f"Order {order_number} status changed to '{current_status}'. Skipping.")
                            continue
                    
                    # Validate current timestamp
                    if not current_timestamp_str or not current_timestamp_str.strip():
                        logger.info(f"Order {order_number} has empty timestamp. Skipping.")
                        continue
                    
                    # Check if reminder should be sent using the FRESH timestamp
                    if self._should_send_reminder(current_timestamp_str, current_status):
                        logger.info(f"Should send reminder passed {order_number}")
                        # Try atomic update first - this prevents concurrent processing
                        # Only proceed with sending email if atomic update succeeds
                        if self._update_email_status_atomic(conn, order_number, current_timestamp_str):
                            logger.info(f"Atomic update succeeded for order {order_number}, sending email.")
                            # Atomic update succeeded, now send the email
                            # Pass email_status to determine recipients for special tables
                            if self._send_reminder_email(order_number, conn, email_status=current_status):
                                logger.info(f"Reminder email sent for order {order_number}")
                                results["reminders_sent"] += 1
                                results["orders_processed"].append(order_number)
                                processed_in_batch.add(order_number)  # Mark as processed
                            else:
                                results["errors"] += 1
                                processed_in_batch.add(order_number)  # Mark as processed even on error
                        else:
                            # Atomic update failed - another process likely handled it
                            logger.info(f"Order {order_number} was already processed by another instance. Skipping.")
                            processed_in_batch.add(order_number)  # Mark as processed to avoid retry in this batch
                            
                except Exception as e:
                    logger.info(f"Error processing row: {str(e)}")
                    results["errors"] += 1
                    continue
            
            logger.info(
                f"Processing complete. Sent {results['reminders_sent']} reminders "
                f"out of {results['total_processed']} records"
            )
            
            return results
            
        except Exception as e:
            logger.error(f"Critical error in process_reminders: {str(e)}")
            results["errors"] += 1
            return results
            
        finally:
            if conn:
                try:
                    conn.close()
                    logger.info("Database connection closed")
                except Exception as e:
                    logger.error(f"Error closing connection: {str(e)}")
    
    def reset_email_status(self, connection_func=None) -> dict:
        results = {
            "total_updated": 0,
            "errors": 0
        }
        
        conn = None
        try:
            # Get database connection
            if connection_func:
                conn = connection_func()
            else:
                conn = pyodbc.connect(self.get_odbc_connection_string())
            
            if not conn:
                logger.error("Failed to connect to database")
                results["errors"] += 1
                return results
            
            cursor = conn.cursor()
            
            # Determine which table contains the status column
            # If data_table_name is provided, update status in data_table_name
            # Otherwise, update status in table_name
            target_table = self.data_table_name if self.data_table_name else self.table_name
            
            # Update all rows: set status column to 0
            update_query = f"""
                UPDATE {target_table}
                SET [{self.status_column_name}] = 0
            """
            
            logger.info(f"Setting status column to 0 for all rows in table: {target_table}")
            cursor.execute(update_query)
            rows_affected = cursor.rowcount
            conn.commit()
            
            results["total_updated"] = rows_affected
            logger.info(f"Updated status to 0 for {rows_affected} records in table: {target_table}")
            
            return results
            
        except Exception as e:
            logger.error(f"Critical error in reset_email_status: {str(e)}")
            results["errors"] += 1
            if conn:
                conn.rollback()
            return results
            
        finally:
            if conn:
                try:
                    conn.close()
                    logger.info("Database connection closed")
                except Exception as e:
                    logger.error(f"Error closing connection: {str(e)}")


def send_reminders(
    table_name: str,
    reminder_duration_hours: int = 24,
    reminder_duration_days: int = 0,
    reminder_duration_minutes: int = 0,
    connection_func=None,
    data_table_name: str = None,
    status_column_name: str = "status",
    timestamp_column: str = "Timestamps",
    email_status_column: str = "Emaill Status",
    order_number_column: str = "No_",
    approver_email_column: str = "Approver Mail ID"
    ) -> dict:
    
    service = ReminderEmailService(
        table_name=table_name,
        reminder_duration_hours=reminder_duration_hours,
        reminder_duration_days=reminder_duration_days,
        reminder_duration_minutes=reminder_duration_minutes,
        data_table_name=data_table_name,
        status_column_name=status_column_name,
        timestamp_column=timestamp_column,
        email_status_column=email_status_column,
        order_number_column=order_number_column,
        approver_email_column=approver_email_column
    )
    
    return service.process_reminders(connection_func)


# Pre-configured functions for different table structures
# You can customize these based on your 4 different data structures

def send_reminders_table1(
    table_name: str,
    reminder_duration_hours: int = 24,
    reminder_duration_days: int = 0,
    reminder_duration_minutes: int = 0,
    connection_func=None,
    data_table_name: str = None,
    status_column_name: str = "status"
) -> dict:
    return send_reminders(
        table_name=table_name,
        reminder_duration_hours=reminder_duration_hours,
        reminder_duration_days=reminder_duration_days,
        reminder_duration_minutes=reminder_duration_minutes,
        connection_func=connection_func,
        data_table_name=data_table_name,
        status_column_name=status_column_name,
        timestamp_column="Timestamps",
        email_status_column="Emaill Status",
        order_number_column="No_",
        approver_email_column="Approver Mail ID"
    )




def reset_email_status_for_testing(
    table_name: str,
    connection_func=None,
    timestamp_column: str = "Timestamps",
    email_status_column: str = "Emaill Status",
    order_number_column: str = "No_"
    ) -> dict:
    """
    Reset email_status from 'Reminder sent' to 'approved' for testing purposes.
    
    Args:
        table_name: Name of the email data table
        connection_func: Optional connection function
        timestamp_column: Column name for timestamps
        email_status_column: Column name for email status
        order_number_column: Column name for order number
    
    Returns:
        dict: Results containing count of records updated and any errors
    """
    service = ReminderEmailService(
        table_name=table_name,
        timestamp_column=timestamp_column,
        email_status_column=email_status_column,
        order_number_column=order_number_column
    )
    return service.reset_email_status(connection_func)


if __name__ == "__main__":
    # Example usage
    # You need to implement your connection function
    
    def get_connection():
        """Example connection function - replace with your actual connection"""
        # from your_config import get_odbc_connection_string
        # return pyodbc.connect(get_odbc_connection_string())
        return pyodbc.connect(ReminderEmailService.get_odbc_connection_string())
    
    # Call the main function
    results = send_reminders(
        table_name="SalesOrderEmail",  # Replace with your table name
        reminder_duration_hours=24,     # Send reminders 24 hours after timestamp
        reminder_duration_days=0,       # No additional days
        reminder_duration_minutes=0,    # Optional: minutes for testing
        connection_func=get_connection
    )
    
    print("\n=== PROCESSING SUMMARY ===")
    print(f"Total Records Processed: {results['total_processed']}")
    print(f"Reminders Sent: {results['reminders_sent']}")
    print(f"Errors: {results['errors']}")
    print(f"Orders with Reminders: {results['orders_processed']}")

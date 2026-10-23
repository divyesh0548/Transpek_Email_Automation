from dotenv import load_dotenv
from sqlalchemy import text
from Utility_Functions.IM_Purchase.im_purchase import check_IM_Purchase_pending_emails, check_IM_Purchase_pending_emails_for_accountants, fix_IM_Purchase_email_status
from Utility_Functions.Purchase_Order.purchase import check_purchase_order_pending_emails, fix_purchase_order_email_status
from Utility_Functions.Sales_Order.sales_order import check_sales_order_pending_emails, fix_sales_order_email_status
from Utility_Functions.JOB_Task.job_task import check_job_task_pending_emails, fix_job_task_email_status
from Utility_Functions.config.reminder_email_service import send_reminders, reset_email_status_for_testing, send_reminders_table1
from Utility_Functions.config.database import get_odbc_connection_string
import os
import pyodbc

# Load environment variables
load_dotenv()


def _module_email_enabled(env_name):
    """One switch runs both the pending-email check and the status-fix job."""
    return os.getenv(env_name, "true").strip().lower() in ("1", "true", "yes")


# def get_time_configuration():
#     """Fetch hours and days from reminder_duration table"""
#     try:
#         with pyodbc.connect(get_odbc_connection_string()) as conn:
#             cursor = conn.cursor()
#             query = f"SELECT [Hours], [Days] FROM {REMINDER_DURATION_TABLE} WHERE [Line No] = ?"
#             cursor.execute(query, (1,))
#             result = cursor.fetchone()
            
#             if result:
#                 hours = result[0] if result[0] is not None else 12
#                 days = result[1] if result[1] is not None else 0
#                 return hours, days
#             else:
#                 # Return default values if no configuration exists
#                 print("Warning: No reminder duration configuration found, using defaults (12 hours, 0 days)")
#                 return 12, 0
#     except Exception as e:
#         # If table doesn't exist or query fails, use defaults
#         print(f"Warning: Could not fetch reminder duration configuration: {str(e)}, using defaults (12 hours, 0 days)")
#         return 12, 0


def background_email_checker():
    print("Checking for pending emails...")
    if _module_email_enabled("ENABLE_PURCHASE_ORDER_EMAIL"):
        check_purchase_order_pending_emails()
    if _module_email_enabled("ENABLE_SALES_ORDER_EMAIL"):
        check_sales_order_pending_emails()
    if _module_email_enabled("ENABLE_JOB_CARD_EMAIL"):
        check_job_task_pending_emails()
    if _module_email_enabled("ENABLE_IM_PURCHASE_EMAIL"):
        check_IM_Purchase_pending_emails()
        check_IM_Purchase_pending_emails_for_accountants()
    # Get dynamic reminder duration from database
    # reminder_hours, reminder_days = get_time_configuration()
    # print(f"Using reminder duration: {reminder_hours} hours, {reminder_days} days")
    
    # send_reminders_table1(
    #     table_name=SALES_ORDER_EMAIL_TABLE,
    #     reminder_duration_hours=reminder_hours, 
    #     reminder_duration_days=reminder_days,      
    #     reminder_duration_minutes=0, 
    #     data_table_name=SALES_ORDER_MAIN,
    #     status_column_name="Status",
    # )
    # send_reminders_table1(
    #     table_name=PURCHASE_REQ_TABLE,
    #     reminder_duration_hours=reminder_hours, 
    #     reminder_duration_days=reminder_days,       
    #     reminder_duration_minutes=0, 
    #     status_column_name="Status",
    # )
    # send_reminders_table1(
    #     table_name=PURCHASE_EMAIL_TABLE,
    #     reminder_duration_hours=reminder_hours, 
    #     reminder_duration_days=reminder_days,       
    #     reminder_duration_minutes=0, 
    #     data_table_name=PURCHASE_HEADER_MAIN,
    #     status_column_name="Status",
    # )
    # send_reminders_table1(
    #     table_name=JOB_CARD_MAIN_TABLE,
    #     reminder_duration_hours=reminder_hours, 
    #     reminder_duration_days=reminder_days,      
    #     reminder_duration_minutes=0,  
    #     data_table_name=JOB_CARD_SECONDARY_TABLE,
    #     status_column_name="Status",
    # )
    # reset_email_status_for_testing(
    #     table_name=JOB_CARD_SECONDARY_TABLE
    # )


def backgroud_email_status_fix():
    print("Fixing email status...")
    if _module_email_enabled("ENABLE_PURCHASE_ORDER_EMAIL"):
        fix_purchase_order_email_status()
    if _module_email_enabled("ENABLE_SALES_ORDER_EMAIL"):
        fix_sales_order_email_status()
    if _module_email_enabled("ENABLE_JOB_CARD_EMAIL"):
        fix_job_task_email_status()
    if _module_email_enabled("ENABLE_IM_PURCHASE_EMAIL"):
        fix_IM_Purchase_email_status()


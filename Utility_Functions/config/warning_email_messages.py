"""
Warning-email subjects and bodies.

Edit the text here. Placeholders:
  {module_name}  Purchase Order, Sales Order, Job Card, IM Purchase
  {order_no}     document number currently being processed
"""

REPEAT_HOURS = 12

DB_CONNECTION_SUBJECT = "Database connection failure"
DB_CONNECTION_BODY = "Please check the system for DB connection failure"

MISSING_COLUMN_SUBJECT = "In {module_name} column is missing"
MISSING_COLUMN_BODY = "In {module_name} column is missing."

MISSING_APPROVER_SUBJECT = "Approver email missing - {module_name} - {order_no}"
MISSING_APPROVER_BODY = (
    "Approver email is empty for {module_name} order {order_no}, "
    "so skipping the processing of that order."
)

MISSING_CREATOR_SUBJECT = "Creator email missing - {module_name} - {order_no}"
MISSING_CREATOR_BODY = (
    "Creator email is empty for {module_name} order {order_no}, "
    "so skipping the processing of that order."
)

MISSING_ACCOUNTANT_SUBJECT = "Accountant emails missing - {module_name} - {order_no}"
MISSING_ACCOUNTANT_BODY = (
    "Both accountant emails are empty for {module_name} order {order_no}, "
    "so skipping the processing of that order."
)

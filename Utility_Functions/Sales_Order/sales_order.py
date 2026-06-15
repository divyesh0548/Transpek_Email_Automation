from Utility_Functions.config.constants import SALES_ORDER_ITEMS_TABLE, SALES_ORDER_EMAIL_TABLE, SALES_ORDER_GST, SALES_ORDER_MAIN, SALES_PERSON_TABLE, SALES_ORDER_SHIPMENT_TABLE, SALES_ORDER_TPK_DETAILS_TABLE, SALES_ORDER_SHIP_TO_ADDRESS_TABLE, SALES_ORDER_CUSTOMER_TABLE, SALES_ORDER_SECONDARY_ITEMS_TABLE, APPROVAL_ENTRY_TABLE, COUNTRY_REGION_CODES_TABLE, TRANSPORT_METHOD_CODE_TABLE, PAYMENT_TERMS_CODE_TABLE, SHIPMENT_METHOD_TABLE, ENTRY_EXIT_POINTS_TABLE, CODE_CITY_TABLE
from Utility_Functions.config.database import get_odbc_connection_string, row_to_dict, get_table_schema, get_engine, get_state_info
from Utility_Functions.config.utility_functions import extract_name_from_email
import pyodbc
import uuid
from datetime import datetime
from sqlalchemy import text
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import os
import re
import io
import uuid
from Utility_Functions.config.secure import encrypt, decrypt

# Explicit Sales Header columns used by check_sales_order_pending_emails (order matches sales_main_table.txt)
_SALES_ORDER_MAIN_HEADER_COLUMNS = (
    "[No_]",
    "[Bill-to Customer No_]",
    "[Bill-to Name]",
    "[Bill-to Address]",
    "[Bill-to City]",
    "[Ship-to Name]",
    "[Ship-to Address]",
    "[Ship-to Contact]",
    "[Order Date]",
    "[Posting Date]",
    "[Payment Terms Code]",
    "[Shipment Method Code]",
    "[Currency Code]",
    "[Currency Factor]",
    "[Salesperson Code]",
    "[Transaction Type]",
    "[Transport Method]",
    "[Bill-to County]",
    "[Ship-to Post Code]",
    "[Document Date]",
    "[External Document No_]",
    "[Payment Method Code]",
    "[Posting No_ Series]",
    "[Promised Delivery Date]",
    "[Shipping Time]",
)
SALES_ORDER_MAIN_HEADER_SELECT = ", ".join(_SALES_ORDER_MAIN_HEADER_COLUMNS)


def fetch_sales_order_line_items(no_):
    """
    Fetch all line items for a given purchase number
    Returns a list of dictionaries with item details
    """
    items_list = []
    
    query_lines = f"""
        SELECT [No_], [Description], [Unit of Measure], [Unit Price], [Quantity], [Amount]
        FROM {SALES_ORDER_ITEMS_TABLE}
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
                    
                    product_code = item_dict.get('No_', 'N/A')
                    desc = item_dict.get('Description', 0)
                    quantity = item_dict.get('Quantity', '')
                    cost = item_dict.get('Unit Price', 0)
                    
                    print(f"  - {product_code}: {desc} {quantity} @ {cost}")
            else:
                print(f"No line items found for Purchase No_: {no_}")
            
            cursor.close()  # Close cursor AFTER processing all rows
        
    except Exception as e:
        print(f"Error fetching line items: {str(e)}")
    
    return items_list

def fix_sales_order_email_status():
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
                FROM {SALES_ORDER_EMAIL_TABLE} pe
                INNER JOIN {SALES_ORDER_MAIN} ost ON pe.[No_] = ost.[No_]
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
                        UPDATE {SALES_ORDER_EMAIL_TABLE}
                        SET [Email Send] = '0'
                        WHERE [No_] = ?
                    """
                    cursor.execute(update_fix_query, (row[0],))
                    print(f"  Updated Sales Order No_: {row[0]}")
                conn.commit()
                print("Fix completed successfully.\n")
            else:
                # No rows to fix, silently continue
                pass

                
    except Exception as e:
        print(f"Error in fix_sales_order_email_status: {str(e)}")
        # Don't raise exception, just log and continue

def format_iso_date_ddmmyyyy(value):
    """
    Convert an ISO datetime string like `2025-11-06T00:00:00.000Z`
    into `06/11/2025`.
    """
    if not value:
        return ""

    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        except Exception:
            try:
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
            except Exception:
                return s

    return dt.strftime("%d/%m/%Y")



def check_sales_order_pending_emails():
    conn = None
    cursor = None

    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            if not conn:
                print("Failed to connect to database")
                return

            # STEP 2: Fetch pending emails
            query = f"""
                SELECT 
                  pe.[No_],
                  pe.[Approver Mail ID],
                  pe.[Creator Mail ID]
                FROM {SALES_ORDER_EMAIL_TABLE} pe
                INNER JOIN {SALES_ORDER_MAIN} ost ON pe.[No_] = ost.[No_]
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
            print(f"Found {len(pending_emails)} pending sales order emails to process")

            for email_row in pending_emails:
                no_ = email_row[0]
                print(f"\n{'='*60}")
                print(f"Processing Sales Order No_: {no_}")
                print(f"{'='*60}")
                

                try:
                    # Determine order type based on prefix
                    is_export = no_.startswith('SE')
                    order_type = "Export" if is_export else "Domestic"
                    print(f"Order Type: {order_type}")

                    if not is_export:
                        
                        # Fetch main data
                        query_main = f"""
                            SELECT {SALES_ORDER_MAIN_HEADER_SELECT}
                            FROM {SALES_ORDER_MAIN}
                            WHERE [No_] = ?
                        """
                        cursor.execute(query_main, (no_,))
                        main_colnames = [c[0] for c in cursor.description]
                        main_data = cursor.fetchone()
                        main_rd = dict(zip(main_colnames, main_data)) if main_data else {}
                        
                        if not main_rd:
                            print(f"⚠️ Warning: No main data found for Sales Order {no_}. Skipping.")
                            continue

                        type_of_packing = None
                        payment_terms_data = None
                        shipment_method = None

                        gst_query = f"""
                            SELECT [Location GST Reg_ No_], [Customer GST Reg_ No_], [Ship-to GST Reg_ No_], [GST Ship-to State Code], [Mode of Transport]
                            FROM {SALES_ORDER_GST}
                            WHERE [No_] = ?
                        """
                        cursor.execute(gst_query, (no_,))
                        gst_data = cursor.fetchone()

                        transport_method_code = (main_rd.get("Transport Method") or "").strip()
                        if transport_method_code:
                            pack_and_code_query = f"""
                                SELECT [Description]
                                FROM {TRANSPORT_METHOD_CODE_TABLE}
                                WHERE [Code] = ?
                            """
                            cursor.execute(pack_and_code_query, (transport_method_code,))
                            type_of_packing = cursor.fetchone()

                        payment_terms_code = (main_rd.get("Payment Terms Code") or "").strip()
                        if payment_terms_code:
                            payment_terms_query = f"""
                                SELECT [Description]
                                FROM {PAYMENT_TERMS_CODE_TABLE}
                                WHERE [Code] = ?
                            """
                            cursor.execute(payment_terms_query, (payment_terms_code,))
                            payment_terms_data = cursor.fetchone()

                        shipment_method_code = (main_rd.get("Shipment Method Code") or "").strip()
                        if shipment_method_code:
                            shipment_method_query = f"""
                                SELECT [Description]
                                FROM {SHIPMENT_METHOD_TABLE}
                                WHERE [Code] = ?
                            """
                            cursor.execute(shipment_method_query, (shipment_method_code,))
                            shipment_method = cursor.fetchone()

                        # Inter_state: True if seller and customer state codes differ, else False (first 2 chars of GST = state code)
                        location_gst = (gst_data[0] or "").strip() if gst_data and len(gst_data) > 0 else ""
                        customer_gst = (gst_data[1] or "").strip() if gst_data and len(gst_data) > 1 else ""
                        ship_to_gst = (gst_data[2] or "").strip() if gst_data and len(gst_data) > 2 else ""
                        seller_gst = location_gst or ship_to_gst
                        seller_state = seller_gst[:2] if len(seller_gst) >= 2 else ""
                        customer_state = customer_gst[:2] if len(customer_gst) >= 2 else ""
                        inter_state = seller_state != customer_state

                        def fetch_agent_details(agent_code):
                            try:
                                commission_query = f"""
                                    SELECT [Name], [Commission _]
                                    FROM {SALES_PERSON_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(commission_query, (agent_code,))
                                result = cursor.fetchone()
                                return result
                            except Exception as e:
                                print(f"⚠️ Warning: Error fetching commission for {agent_code}: {e}")
                                return 0
                        
                        agent_data = fetch_agent_details(str(main_rd.get("Salesperson Code") or ""))

                        document_date = main_rd.get("Document Date")
                        document_date = format_iso_date_ddmmyyyy(document_date) if document_date else ""

                        creator_name = extract_name_from_email(email_row[2]) if len(email_row) > 2 else ""

                        # Build header data with safe defaults
                        header_data = {
                            # Basic Information
                            'no_': email_row[0] if len(email_row) > 2 else "",
                            'document_date': document_date,
                            'po_no': main_rd.get("External Document No_") or "",
                            'order_date': main_rd.get("Posting Date") or datetime.now(),
                            'agent_commission': agent_data[1] if agent_data else 0,
                            'agent_name': agent_data[0] if agent_data else "",
                            'shipment_method': shipment_method[0] if shipment_method else "",
                            'type_of_packing': type_of_packing[0] if type_of_packing else "",
                            'payment_terms': payment_terms_data[0] if payment_terms_data else "",
                            'order_type': order_type,
                            'Inter_state': inter_state,

                            # Customer Information
                            'sell_to_customer_name': main_rd.get("Bill-to Name") or "",
                            'sell_to_address': main_rd.get("Bill-to Address") or "",
                            'sell_to_city': main_rd.get("Bill-to City") or "",
                            'sell_to_post_code': main_rd.get("Bill-to County") or "",
                            'customer_gst_reg_no_': gst_data[1] if gst_data and len(gst_data) > 1 else "",
                            'sell_to_e_mail': email_row[2] if len(email_row) > 9 else "",

                            # Consignee Information
                            'consignee_name': main_rd.get("Ship-to Name") or "",
                            'consignee_address': main_rd.get("Ship-to Address") or "",
                            'consignee_city': main_rd.get("Ship-to Contact") or "",
                            'consignee_post_code': main_rd.get("Ship-to Post Code") or "",
                            'consignee_gst_reg_no_': gst_data[2] if gst_data and len(gst_data) > 2 else "",
                            'consignee_state_code': gst_data[3] if gst_data and len(gst_data) > 3 else "",

                            # Email Information
                            'approver_email': email_row[1] if len(email_row) > 2 else "",
                            'creator_name': creator_name,
                        }

                        # Get state info safely
                        consignee_state_code = header_data.get('consignee_state_code', '')
                        if consignee_state_code:
                            state_info_consignee = get_state_info(consignee_state_code)
                            if state_info_consignee:
                                header_data['consignee_state'] = state_info_consignee.get('state_name', "")
                                header_data['consignee_state_code'] = state_info_consignee.get('state_code', "")
                            else:
                                header_data['consignee_state'] = ""
                                header_data['consignee_state_code'] = consignee_state_code
                        else:
                            header_data['consignee_state'] = ""

                        # Fetch line items
                        items_list = fetch_sales_order_line_items(email_row[0])
                        
                        # Add commission to items
                        commission_dict = {}
                        product_codes = set(item.get('No_') for item in items_list if item.get('No_'))

                        for product_code in product_codes:
                            commission_value = agent_data[1] if agent_data else 0
                            commission_dict[product_code] = commission_value

                        for item in items_list:
                            product_code = item.get('No_')
                            item['commission'] = commission_dict.get(product_code, 0)

                        print(f"✓ Header Data prepared {header_data}")
                        print(f"✓ Line Items: {len(items_list)} items")

                        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

                        approver_email_id = email_row[1]

                        if approver_email_id and approver_email_id.strip():
                            # Updating email columns
                            update_query = f"""
                             UPDATE {SALES_ORDER_EMAIL_TABLE}
                             SET [Email Send] = 1,
                                 [Emaill Status] = 'sent for approval',
                                 [Timestamps] = ?
                             WHERE [No_] = ?
                             """
                            cursor.execute(update_query, (current_time, email_row[0]))
                            conn.commit()

                            # Send email
                            send_sales_approval_email(header_data, items_list)
                            print(f"✅ Successfully processed Domestic order: {no_}")
                        else:
                            print(f"❌ Invalid Approver Email ID for Order No: {email_row[0]}")
                        
                    
                
                    else:
                        # ==================== EXPORT ORDER PROCESSING ====================
                        
                        # Fetch main data
                        query_main = f"""
                            SELECT {SALES_ORDER_MAIN_HEADER_SELECT}
                            FROM {SALES_ORDER_MAIN}
                            WHERE [No_] = ?
                        """
                        cursor.execute(query_main, (no_,))
                        main_colnames = [c[0] for c in cursor.description]
                        main_data = cursor.fetchone()
                        main_rd = dict(zip(main_colnames, main_data)) if main_data else {}
                        
                        if not main_rd:
                            print(f"⚠️ Warning: No main data found for Sales Order {no_}. Skipping.")
                            continue

                        pack_and_code_data = None
                        payment_terms_data_export = None

                        # Fetch GST data
                        gst_query = f"""
                            SELECT *
                            FROM {SALES_ORDER_GST}
                            WHERE [No_] = ?
                        """
                        cursor.execute(gst_query, (no_,))
                        gst_data = cursor.fetchone()

                        # Fetch shipment data
                        shipment_query = f"""
                            SELECT *
                            FROM {SALES_ORDER_SHIPMENT_TABLE}
                            WHERE [No_] = ?
                        """
                        cursor.execute(shipment_query, (no_,))
                        shipment_data = cursor.fetchone()

                        #Fetch Enrty/Exit points data
                        if shipment_data:
                            if len(shipment_data) > 3:
                                loading_port_code = shipment_data[3]
    
                                loading_port_query = f"""
                                    SELECT [Description]
                                    FROM {ENTRY_EXIT_POINTS_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(loading_port_query, (loading_port_code,))
                                loading_port_details = cursor.fetchone()
                            
                            if len(shipment_data) > 4:
                                discharge_port_code = shipment_data[4]
                                discharge_port_query = f"""
                                    SELECT [Description]
                                    FROM {ENTRY_EXIT_POINTS_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(discharge_port_query, (discharge_port_code,))
                                discharge_port_details = cursor.fetchone()

                            if len(shipment_data) > 5:
                                city_place_code = shipment_data[5]
                                code_city_query = f"""
                                    SELECT [City]
                                    FROM {CODE_CITY_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(code_city_query, (city_place_code,))
                                place_of_delivery = cursor.fetchone()


                        country_code = shipment_data[7] if shipment_data and len(shipment_data) > 7 else ""
                        country_of_destination_name = ""
                        if country_code:
                            try:
                                country_query = f"""
                                    SELECT [Name]
                                    FROM {COUNTRY_REGION_CODES_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(country_query, (country_code,))
                                country_row = cursor.fetchone()
                                if country_row and len(country_row) > 0 and country_row[0]:
                                    country_of_destination_name = country_row[0]
                            except Exception:
                                country_of_destination_name = ""
                        
                        # Fetch customer data
                        customer_code = (main_rd.get("Bill-to Customer No_") or "").strip()
                        customer_data = None
                        if customer_code:
                            customer_query = f"""
                                SELECT [Name], [Address], [Address 2], [Contact], [Phone No_], [Credit Limit (LCY)], [E-Mail], [Fax No_]
                                FROM {SALES_ORDER_CUSTOMER_TABLE}
                                WHERE [No_] = ?
                            """
                            cursor.execute(customer_query, (customer_code,))
                            customer_data = cursor.fetchone()

                        # Fetch TPK data
                        tpk_query = f"""
                            SELECT [TPK Delivery Terms], [TPK LC Date], [TPK LC No_], [TPK Notify 1], [TPK Notify 2]
                            FROM {SALES_ORDER_TPK_DETAILS_TABLE}
                            WHERE [No_] = ?
                        """
                        cursor.execute(tpk_query, (no_,))
                        tpk_data = cursor.fetchone()
                        
                        # Fetch notify party data
                        notify1_data = None
                        notify2_data = None
                        
                        if tpk_data and len(tpk_data) > 3:
                            tpk_notify_1_code = tpk_data[3]
                            if tpk_notify_1_code:
                                tpk1_data_query = f"""
                                    SELECT [Name], [Address], [Address 2], [Contact], [Phone No_], [E-Mail], [Fax No_]
                                    FROM {SALES_ORDER_SHIP_TO_ADDRESS_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(tpk1_data_query, (tpk_notify_1_code,))
                                notify1_data = cursor.fetchone()

                        if tpk_data and len(tpk_data) > 4:
                            tpk_notify_2_code = tpk_data[4]
                            if tpk_notify_2_code:
                                tpk2_data_query = f"""
                                    SELECT [Name], [Address], [Address 2], [Contact], [Phone No_], [E-Mail], [Fax No_]
                                    FROM {SALES_ORDER_SHIP_TO_ADDRESS_TABLE}
                                    WHERE [Code] = ?
                                """
                                cursor.execute(tpk2_data_query, (tpk_notify_2_code,))
                                notify2_data = cursor.fetchone()
                        
                        # Fetch sales person data
                        sales_person_code_init = (main_rd.get("Salesperson Code") or "").strip()
                        sales_person_data = None
                        if sales_person_code_init:
                            sales_person_query = f"""
                                SELECT [Name], [Commission _]
                                FROM {SALES_PERSON_TABLE}
                                WHERE [Code] = ?
                            """
                            cursor.execute(sales_person_query, (sales_person_code_init,))
                            sales_person_data = cursor.fetchone()
                        
                        query_for_shipping_bill = f"""
                            SELECT [AA License No], [DBK Applicable]
                            FROM {SALES_ORDER_SECONDARY_ITEMS_TABLE}
                            WHERE [Document No_] = ?
                        """

                        cursor.execute(query_for_shipping_bill, (no_,))
                        shipping_bill_data = cursor.fetchone()

                        shipping_bill = "NA"

                        if shipping_bill_data:
                            aa_license_no = shipping_bill_data[0]
                            dbk_applicable = shipping_bill_data[1]
                            
                            # Logic implementation
                            if aa_license_no and aa_license_no.strip():  # Check if not empty
                                shipping_bill = "Shipment against adv lic"
                            elif dbk_applicable == "1" or dbk_applicable == 1:
                                shipping_bill = "duty drawback rate"
                            else:
                                shipping_bill = "NA"
                        else:
                            # Handle case when no data is returned
                            shipping_bill = "NA"
                        
                        transport_method_code = (main_rd.get("Transport Method") or "").strip()
                        if transport_method_code:
                            pack_and_code_query = f"""
                                SELECT [Description]
                                FROM {TRANSPORT_METHOD_CODE_TABLE}
                                WHERE [Code] = ?
                            """
                            cursor.execute(pack_and_code_query, (transport_method_code,))
                            pack_and_code_data = cursor.fetchone()

                        payment_terms_code_export = (main_rd.get("Payment Terms Code") or "").strip()
                        if payment_terms_code_export:
                            payment_terms_query_export = f"""
                                SELECT [Description]
                                FROM {PAYMENT_TERMS_CODE_TABLE}
                                WHERE [Code] = ?
                            """
                            cursor.execute(payment_terms_query_export, (payment_terms_code_export,))
                            payment_terms_data_export = cursor.fetchone()

                        
                        # Fetch line items
                        so_items_query = f"""
                            SELECT [No_], [Description], [Unit of Measure], [Unit Price], [Quantity], [Amount]
                            FROM {SALES_ORDER_ITEMS_TABLE}
                            WHERE [Document No_] = ?
                            ORDER BY [Line No_] ASC
                        """
                        cursor.execute(so_items_query, (no_,))
                        columns = [column[0] for column in cursor.description]
                        results = cursor.fetchall()
                        
                        if not results:
                            print(f" Warning: No line items found for Sales Order {no_}. Skipping.")
                            continue
                        
                        items_data = [dict(zip(columns, row)) for row in results]

                        # Build header data with safe defaults
                        items_grade = main_rd.get("Currency Code") or "EXPORT"
                        if tpk_data is not None:
                            lc_no = tpk_data[2],
                            lc_date = tpk_data[1],
                        else:
                            lc_no = " ",
                            lc_date =  " ",
                        
                        creator_name = extract_name_from_email(email_row[2]) if len(email_row) > 2 else ""
                        
                        header_data = {
                            "order_date": datetime.now().strftime('%d-%m-%y'),
                            "so_origianl_order_date": format_iso_date_ddmmyyyy(main_rd.get("Posting Date")) if main_rd.get("Posting Date") else datetime.now().strftime("%d/%m/%Y"),
                            "no_": main_rd.get("No_") or no_,
                            "agent_code": main_rd.get("Salesperson Code") or "",
                            "agent_name": sales_person_data[0] if sales_person_data else "",
                            "sell_to_customer_name": customer_data[0] if customer_data else "",
                            "customer_address_1": customer_data[1] if customer_data and len(customer_data) > 1 else "",
                            "customer_address_2": customer_data[2] if customer_data and len(customer_data) > 2 else "",
                            "customer_attn": customer_data[3] if customer_data and len(customer_data) > 3 else "",
                            "customer_phone_no": customer_data[4] if customer_data and len(customer_data) > 4 else "",
                            "customer_email": customer_data[6] if customer_data and len(customer_data) > 6 else "",
                            "order_no": main_rd.get("External Document No_") or "",
                            "consignee_name": main_rd.get("Ship-to Name") or "",
                            "consignee_address": main_rd.get("Ship-to Address") or "",
                            "consignee_attn": main_rd.get("Order Date") or "",
                            "currency": main_rd.get("Currency Code") if main_rd.get("Currency Code") is not None else "",
                            "tentative_shipment_date": main_rd.get("Promised Delivery Date") or datetime.now(),
                            "deliver_date": main_rd.get("Shipping Time") or datetime.now(),
                            "third_party_insepection": main_rd.get("Transaction Type") if main_rd.get("Transaction Type") is not None else 0,
                            "shipping_remakrs": gst_data[21] if gst_data and len(gst_data) > 21 else "",
                            "port_of_loading": loading_port_details[0] if loading_port_details else "",
                            "place_of_delivery": place_of_delivery[0] if place_of_delivery else "",
                            "port_of_discharge": discharge_port_details[0] if discharge_port_details else "",
                            "country_of_destination": country_of_destination_name,
                            "delivery_terms": tpk_data[0] if tpk_data else "",
                            "items_grade": items_grade,
                            "pack_and_code": pack_and_code_data[0] if pack_and_code_data and len(pack_and_code_data) > 0 else "",
                            "payment_terms": payment_terms_data_export[0] if payment_terms_data_export and len(payment_terms_data_export) > 0 else "",
                            "credit_days": payment_terms_data_export[0] if payment_terms_data_export and len(payment_terms_data_export) > 0 else "",
                            "commission": sales_person_data[1] if sales_person_data and len(sales_person_data) > 1 else 0,
                            "shipment_by": gst_data[34] if gst_data and len(gst_data) > 34 else "",
                            "commission_on": main_rd.get("Posting No_ Series") or "",
                            "type_of_shipment": main_rd.get("Payment Method Code") or "",
                            "lc_no": tpk_data[2] if len(tpk_data) > 2 else None,
                            "lc_date": tpk_data[1] if len(tpk_data) > 1 else None,
                            # Before accessing tpk_data, check if it's None

                            "approver_email": email_row[1] if len(email_row) > 2 else "",
                            "creator_name": creator_name,
                            
                            # Remaining fields
                            "mode": "PO/LC/TEL/TLX/FAX/VERBAL/OTHER",
                            "TPK1_name": notify1_data[0] if notify1_data else "",
                            "TPK1_address": (notify1_data[1] + ", " + notify1_data[2]) if notify1_data and len(notify1_data) > 2 else "",
                            "TPK1_contact": notify1_data[3] if notify1_data and len(notify1_data) > 3 else "",
                            "TPK1_phone": notify1_data[4] if notify1_data and len(notify1_data) > 4 else "",
                            "TPK1_email": notify1_data[5] if notify1_data and len(notify1_data) > 5 else "",
                            "TPK1_fax": notify1_data[6] if notify1_data and len(notify1_data) > 6 else "",
                            "TPK2_name": notify2_data[0] if notify2_data else "",
                            "TPK2_address": (notify2_data[1] + ", " + notify2_data[2]) if notify2_data and len(notify2_data) > 2 else "",
                            "TPK2_contact": notify2_data[3] if notify2_data and len(notify2_data) > 3 else "",
                            "TPK2_phone": notify2_data[4] if notify2_data and len(notify2_data) > 4 else "",
                            "TPK2_email": notify2_data[5] if notify2_data and len(notify2_data) > 5 else "",
                            "TPK2_fax": notify2_data[6] if notify2_data and len(notify2_data) > 6 else "",
                            "consigner_phone": customer_data[4] if customer_data and len(customer_data) > 4 else "",
                            "consigner_email": customer_data[6] if customer_data and len(customer_data) > 6 else "",
                            "consigner_fax": customer_data[7] if customer_data and len(customer_data) > 7 else "",
                            "credit_limit_approval": customer_data[5] if customer_data and len(customer_data) > 5 else "",
                            # "shipping_bill": "SHIPMENT UNDER DUTY DRAWBACK@ 1%."
                            "shipping_bill": shipping_bill
                        }

                        # Add grade to all items
                        for item in items_data:
                            item['Grade'] = items_grade

                        print(f" Header Data : {format(header_data)}")
                        print(f" Line Items: {items_data}")

                        current_time = str(datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])[:255]

                        approver_email_id = email_row[1]

                        if approver_email_id and approver_email_id.strip():
                            # Updating email columns
                            update_query = f"""
                             UPDATE {SALES_ORDER_EMAIL_TABLE}
                             SET [Email Send] = 1,
                                 [Emaill Status] = 'sent for approval',
                                 [Timestamps] = ?
                             WHERE [No_] = ?
                             """
                            cursor.execute(update_query, (current_time, email_row[0]))
                            conn.commit()

                            # Send email
                            send_sales_approval_email(header_data, items_data)
                            print(f"✅ Successfully processed Export order: {no_}")
                        else:
                            print(f"❌ Invalid Approver Email ID for Order No: {email_row[0]}")

                except Exception as order_error:
                    print(f"❌ Error processing order {no_}: {str(order_error)}")
                    import traceback
                    traceback.print_exc()
                    # Continue to next order instead of stopping
                    continue

            print(f"\n{'='*60}")
            print("✅ Successfully processed all pending emails")
            print(f"{'='*60}")
        else:
            print("No pending email found in sales orders")
            
        # Check for unsubmitted records
        # status_zero_query = f"""
        #     SELECT pe.[No_]
        #     FROM {SALES_ORDER_EMAIL_TABLE} pe
        #     INNER JOIN {SALES_ORDER_MAIN} ost ON pe.[No_] = ost.[No_]
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
        #     print(f"Found {len(unsubmitted_records)} Sales order records not sent for approval.")
        #     print(f"{'='*60}")
        #     # Show only first 5 records
        #     for record in unsubmitted_records[:5]:
        #         print(f"  Sales Order No_: {record[0]}")
        #     if len(unsubmitted_records) > 5:
        #         print(f"  ... and {len(unsubmitted_records) - 5} more record(s)")
        #     print(f"{'='*60}\n")
            
    except pyodbc.Error as e:
        print(f"❌ Database error: {str(e)}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
    except Exception as e:
        print(f"❌ Error checking pending emails: {str(e)}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def send_sales_approval_email(data, line_items):
    try:
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")

        # Validate required SMTP settings
        if not all([smtp_server, smtp_user, smtp_password]):
            print("❌ Missing SMTP configuration")
            return False

        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_user
        recipient_email = data.get('approver_email')
        approver_name = data.get('approver_name', 'Approver')
        msg['To'] = recipient_email
        # msg['To'] = "divyeshparmar0909@gmail.com"  # Hardcoded for testing, replace with recipient_email in production
        base_url = os.getenv('BASE_URL', 'http://localhost:5000')
        req_id = data.get('no_', '')
        
        # Set subject based on req_id prefix
        if req_id.startswith('SE'):
            subject_text = f"Sales Export Order Approval Required - {req_id}"
        elif req_id.startswith('SD'):
            subject_text = f"Sales Domestic Order Approval Required - {req_id}"
        else:
            subject_text = f"Sales Order Approval Required - {req_id}"
        msg['Subject'] = subject_text
        encrypted_req_id = encrypt(str(req_id))

        # Validate email before proceeding
        if not recipient_email or not isinstance(recipient_email, str) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", recipient_email):
            print(f"❌ Invalid recipient email for document {req_id}: '{recipient_email}'")
            return False

        body_html = f"""
        <html>
        <body style='font-family: Arial, sans-serif;'>
        <p>Dear {approver_name},</p>
        <p>A new sales order requires your approval:</p>
        <table border='1' style='border-collapse: collapse; margin: 20px 0;'>
            <tr><td><strong>Document No:</strong></td><td>{data.get('no_', '')}</td></tr>
            <tr><td><strong>Order Date:</strong></td><td>{data.get('order_date', '')}</td></tr>
            <tr><td><strong>Customer:</strong></td><td>{data.get('sell_to_customer_name', '')}</td></tr>
            <tr><td><strong>Consignee:</strong></td><td>{data.get('consignee_name', '')}</td></tr>
        </table>
        <p>Please review the attached PDF and take action:</p>
        <div style='margin: 30px 0;'>
            <a href='{base_url}/sales-email-approve/{encrypted_req_id}' 
               style='background-color: #28a745; color: white; padding: 12px 24px; text-decoration: none; margin-right: 10px; border-radius: 4px;'>
               ✓ APPROVE
            </a>
            <a href='{base_url}/sales-email-reject/{encrypted_req_id}' 
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

        # Generate Purchase Order PDF
        from sales_order_pdf_generator import generate_sales_order_pdf_main
        from sales_order_export_pdf_generator import generate_export_sales_order_pdf

        try:
            if req_id.startswith("SD"):
                print("Using Normal Sales Order PDF Generator")
                pdf_buffer = generate_sales_order_pdf_main(
                    data,
                    line_items,
                    "sales_order.pdf"
                )
            elif req_id.startswith("SE"):
                print("Using Export Sales Order PDF Generator")
                pdf_bytes = generate_export_sales_order_pdf(
                    data,
                    line_items,
                    "assets/Export_SO_Template.html",
                    "sales_order.pdf"
                )
                pdf_buffer = io.BytesIO(pdf_bytes)
               
            else:
                 print("Using Default Sales Order PDF Generator")
                 pdf_buffer = generate_sales_order_pdf_main(
                     data,
                     line_items,
                     "sales_order.pdf"
                 )
                 

            if not pdf_buffer:
                print(f"❌ Failed to generate PDF for document {req_id}")
                return False

            # Attach PDF to email
            from email.mime.application import MIMEApplication
            pdf_attachment = MIMEApplication(pdf_buffer.getvalue(), _subtype='pdf')
            pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'Sales_Order_{req_id}.pdf')
            msg.attach(pdf_attachment)

        except Exception as e:
            print(f"❌ Error generating PDF for document {req_id}: {e}")
            return False

        # Send email
        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()
            print(f"✅ Approval email sent successfully for document {req_id}")

        except smtplib.SMTPException as e:
            print(f"❌ SMTP Error sending email for Document No {req_id}: {e}")
            return False


    except Exception as e:
        print(f"❌ Error occurred in send_sales_approval_email function: {e}")
        import traceback
        traceback.print_exc()
        return False

def send_sales_order_response_email_to_customer(request_id, response_status, status, timestamp, client_data, reason=None):
    """Send response email to creator after approval/rejection"""
    try:
        sql = f"""SELECT [Creator Mail ID] FROM {SALES_ORDER_EMAIL_TABLE} WHERE [No_] = ?"""
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
                UPDATE {SALES_ORDER_EMAIL_TABLE}
                SET [Response Mail Send] = ?
                WHERE [No_] = ?
            """
            with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, 1, request_id)
                conn.commit()

        except Exception as e:
            print("An error occurred:", e)
            return False
        
        print(f"✅ Response email sent to creator {creator_email} for document {request_id}")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to send response email to creator: {e}")
        return False

def get_sales_order_data_by_id(request_id):
    query = f"SELECT * FROM {SALES_ORDER_MAIN} WHERE [No_] = :request_id"
    params = {"request_id": str(request_id)}

    query_for_email_data =  f"SELECT * FROM {SALES_ORDER_EMAIL_TABLE} WHERE [No_] = :request_id"
    params = {"request_id": str(request_id)}

    engine = get_engine()
    with engine.connect() as connection:
        result_header = connection.execute(text(query), params).fetchone()
        result_email = connection.execute(text(query_for_email_data), params).fetchone()

    if result_header:
        # Convert results to dictionaries (if not None)
        header_data = dict(result_header._mapping) if result_header else None
        email_data = dict(result_email._mapping) if result_email else None
        return {
            "header_data": header_data,
            "email_data": email_data
            }
    
    return None

import pdfkit
from jinja2 import Template
from datetime import datetime
from typing import Dict, List
import io
import base64
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# Configure wkhtmltopdf path (adjust as needed for your system)
BASE_DIR = Path(__file__).resolve().parent
wkhtmltopdf_path = BASE_DIR / "wkhtmltox" / "wkhtmltopdf.exe"
config = pdfkit.configuration(wkhtmltopdf=str(wkhtmltopdf_path))

def get_company_logo_base64(logo_path: str = "assets/logo.jpg"):
    """Convert company logo to base64 string."""
    try:
        with open(logo_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode()
            return f"data:image/jpeg;base64,{encoded_string}"
    except FileNotFoundError:
        return ""

def get_seller_company_details():
    """Get fixed seller company details (Transpek Industry Limited)."""
    return {
        'seller_company_name': 'Transpek Industry Limited',
        'seller_company_address': 'At & Post Ekalbara, Tal. Padra, Dist. Vadodara - 391440',
        'seller_company_phone': '+91-2662-273724',
        'seller_company_email': 'marketing@transpek.com',
        'seller_company_website': 'www.transpek.com',
        'seller_company_gst_no': '24AAACT8639B1ZI',
        'seller_company_cin': 'L24110GJ1965PLC001478'
    }

def format_date(date_value):
    """Format date to DD-MM-YY format."""
    if not date_value:
        return ""
    
    # Handle string dates
    if isinstance(date_value, str):
        if date_value == '18-11-25':
            return date_value
        try:
            date_value = datetime.strptime(date_value, '%Y-%m-%d')
        except:
            return date_value
    
    # Handle datetime objects
    if isinstance(date_value, datetime):
        # Check for SQL Server default "no date" value (1753-01-01)
        if date_value.year == 1753:
            return ""
        
        # Check for dates before 1900 (strftime limitation on Windows)
        if date_value.year < 1900:
            return ""
        
        # Format valid dates
        return date_value.strftime('%d-%m-%y')
    
    return str(date_value)

def format_currency(amount):
    """Format currency with commas and 2 decimal places."""
    if amount is None:
        return "0.000"
    
    if isinstance(amount, Decimal):
        amount = float(amount)
    
    return f"{amount:,.3f}"

def format_max_3_decimals(value):
    """Format a number rounded to at most 3 decimal places (no trailing zeros)."""
    if value is None or value == "":
        return ""

    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))

    d = d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    formatted = f"{d:,.3f}".rstrip("0").rstrip(".")
    return formatted

def format_quantity(quantity):
    """Format quantity appropriately."""
    if quantity is None:
        return "0.00"
    
    if isinstance(quantity, Decimal):
        quantity = float(quantity)
    
    if quantity == int(quantity):
        return f"{int(quantity):,}"
    
    return f"{quantity:,.2f}"

def convert_number_to_words(amount: float) -> str:
    """Convert number to words in USD format."""
    if amount == 0:
        return "ZERO USD ONLY"
    
    ones = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"]
    teens = ["TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", 
             "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"]
    tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]
    
    dollars = int(amount)
    cents = int(round((amount - dollars) * 100))
    
    def convert_hundreds(n):
        result = ""
        if n >= 100:
            result += ones[n // 100] + " HUNDRED "
            n %= 100
        
        if n >= 20:
            result += tens[n // 10] + " "
            n %= 10
        elif n >= 10:
            result += teens[n - 10] + " "
            n = 0
        
        if n > 0:
            result += ones[n] + " "
        
        return result.strip()
    
    def convert_to_words(num):
        if num == 0:
            return ""
        
        billions = num // 1000000000
        num %= 1000000000
        
        millions = num // 1000000
        num %= 1000000
        
        thousands = num // 1000
        num %= 1000
        
        hundreds = num
        
        result = ""
        if billions:
            result += convert_hundreds(billions) + " BILLION "
        if millions:
            result += convert_hundreds(millions) + " MILLION "
        if thousands:
            result += convert_hundreds(thousands) + " THOUSAND "
        if hundreds:
            result += convert_hundreds(hundreds)
        
        return result.strip()
    
    dollar_words = convert_to_words(dollars)
    
    if dollar_words:
        dollar_part = f"{dollar_words} USD"
    else:
        dollar_part = "ZERO USD"
    
    if cents == 0:
        return f"{dollar_part} ONLY"
    else:
        cents_words = convert_to_words(cents)
        return f"{dollar_part} AND {cents_words} CENTS ONLY"

def prepare_export_so_template_data(header_data: Dict, line_data: List[Dict]) -> Dict:
    """Prepare data dictionary for Export Sales Order HTML template."""
    
    seller_company = get_seller_company_details()
    
    total_amount = 0.0
    prepared_line_items = []
    
    for index, item in enumerate(line_data, 1):
        item_quantity = float(item.get('Quantity', 0))
        item_unit_price = float(item.get('Unit Price', 0))
        item_amount = float(item.get('Amount', 0))
        
        prepared_item = {
            'sr_no': index,
            'product_code': item.get('No_', ''),
            'description': item.get('Description', ''),
            'grade': item.get('Grade', ''),
            'quantity': format_quantity(item_quantity),
            'unit_of_measure': item.get('Unit of Measure', 'KGS'),
            'unit_price': format_currency(item_unit_price),
            'amount': format_currency(item_amount)
        }
        
        prepared_line_items.append(prepared_item)
        total_amount += item_amount
    
    lc_date = header_data.get('lc_date')
    lc_date_formatted = format_date(lc_date)
    
    return {
        
        'date': format_date(header_data.get('order_date')),
        'order_date': format_date(header_data.get('so_origianl_order_date')),
        'sales_order_number': header_data.get('no_', ''),
        'agent_code': header_data.get('agent_code', ''),
        'agent_name': header_data.get('agent_name', ''),
        'order_no': header_data.get('order_no', ''),
        'mode': header_data.get('mode', 'PO/LC/TEL/TLX/FAX/VERBAL/OTHER'),
        
        'customer_name': header_data.get('sell_to_customer_name', ''),
        'customer_address_1': header_data.get('customer_address_1', ''),
        'customer_address_2': header_data.get('customer_address_2', ''),
        'customer_attn': header_data.get('customer_attn', ''),
        'customer_phone_no': header_data.get('customer_phone_no', ''),
        'customer_email': header_data.get('customer_email', ''),
        
        'consignee_name': header_data.get('consignee_name', ''),
        'consignee_address': header_data.get('consignee_address', ''),
        'consignee_attn': header_data.get('consignee_attn', ''),
        'consignee_phone': header_data.get('consigner_phone', ''),
        'consignee_email': header_data.get('consigner_email', ''),
        'consignee_fax': header_data.get('consigner_fax', ''),

        'creator_name': header_data.get('creator_name', ''),
        
        
        'currency': header_data.get('currency', 'USD'),
        'tentative_shipment_date': format_date(header_data.get('tentative_shipment_date')),
        'deliver_date': format_date(header_data.get('deliver_date')),
        'third_party_insepection': 'YES' if header_data.get('third_party_insepection', 0) == 1 else 'NO',
        'shipping_remakrs': header_data.get('shipping_remakrs', ''),
        'port_of_loading': header_data.get('port_of_loading', ''),
        'place_of_delivery': header_data.get('place_of_delivery', ''),
        'port_of_discharge': header_data.get('port_of_discharge', ''),
        'country_of_destination': header_data.get('country_of_destination', ''),
        'delivery_terms': header_data.get('delivery_terms', ''),
        'items_grade': header_data.get('items_grade', ''),
        'pack_and_code': header_data.get('pack_and_code', ''),
        'shipment_by': header_data.get('shipment_by', '') or 'NA',
        'type_of_shipment': header_data.get('type_of_shipment', ''),
        
        'payment_terms': header_data.get('payment_terms', ''),
        'credit_days': header_data.get('credit_days', ''),
        'credit_limit_approval': format_max_3_decimals(header_data.get('credit_limit_approval', '')),
        'lc_no': header_data.get('lc_no', ''),
        'lc_date': lc_date_formatted,
        
        'commission': format_currency(header_data.get('commission', 0)),
        'commission_on': header_data.get('commission_on') or 'NA',
        
        'TPK1_name': header_data.get('TPK1_name', ''),
        'TPK1_address': header_data.get('TPK1_address', ''),
        'TPK1_contact': header_data.get('TPK1_contact', ''),
        'TPK1_phone': header_data.get('TPK1_phone', ''),
        'TPK1_email': header_data.get('TPK1_email', ''),
        'TPK1_fax': header_data.get('TPK1_fax', ''),

        'TPK2_name': header_data.get('TPK2_name', ''),
        'TPK2_address': header_data.get('TPK2_address', ''),
        'TPK2_contact': header_data.get('TPK2_contact', ''),
        'TPK2_phone': header_data.get('TPK2_phone', ''),
        'TPK2_email': header_data.get('TPK2_email', ''),
        'TPK2_fax': header_data.get('TPK2_fax', ''),
        
        'shipping_bill': header_data.get('shipping_bill', ''),
        
        'line_items': prepared_line_items,
        
        'current_date': datetime.now().strftime('%d-%m-%Y'),
        'current_year': datetime.now().year
    }

def generate_export_sales_order_pdf(header_data: Dict, line_data: List[Dict],
                                   template_path: str = "assets/Export_SO_Template.html",
                                   output_filename: str = None):
    
    template_data = prepare_export_so_template_data(header_data, line_data)
    
    try:
        with open(template_path, 'r', encoding='utf-8-sig') as f:
            html_template = f.read()
    except UnicodeDecodeError:
        try:
            with open(template_path, 'r', encoding='utf-16') as f:
                html_template = f.read()
        except UnicodeDecodeError:
            with open(template_path, 'r', encoding='latin-1') as f:
                html_template = f.read()
    
    template = Template(html_template)
    rendered_html = template.render(**template_data)
    
    options = {
        'page-width': '210mm',
        'page-height': '297mm',
        'orientation': 'Portrait',
        'margin-top': '15mm',
        'margin-right': '10mm',
        'margin-bottom': '15mm',
        'margin-left': '10mm',
        'encoding': 'UTF-8',
        'no-outline': None,
        'enable-local-file-access': None,
        "disable-smart-shrinking": "",
        "print-media-type": "",
        "no-print-media-type": None,
        "quiet": ""
    }
    
    if output_filename:
        pdfkit.from_string(rendered_html, output_filename, configuration=config, options=options)
        print(f"Export Sales Order PDF saved to: {output_filename}")
        
        with open(output_filename, 'rb') as f:
            pdf_content = f.read()
    else:
        pdf_content = pdfkit.from_string(rendered_html, False, options=options, configuration=config)
    
    return pdf_content

def create_export_so_pdf_buffer(header_data: Dict, line_data: List[Dict], 
                                template_path: str = "assets/Export_SO_Template.html"):
    pdf_content = generate_export_sales_order_pdf(header_data, line_data, template_path)
    buffer = io.BytesIO()
    buffer.write(pdf_content)
    buffer.seek(0)
    return buffer

if __name__ == "__main__":
    header_data = {
        'date': '18-11-25', 
        'sales_order_number': 'SE/SO-25260002', 
        'agent_code': 'EAG001', 
        'agent_name': 'EXPORT DIRECT', 
        'customer_name': 'DUPONT SPECIALTY PRODUCTS USA, LLC', 
        'customer_address_1': 'ACCOUNTS PAYABLE DEPARTMENT', 
        'customer_address_2': 'P.O.BOX 80040,WILMINGTON,DELAWARE 19880-0040, USA.', 
        'customer_attn': 'DANIEL MENTRIKOSKI', 
        'customer_phone_no': '804-383-4364', 
        'customer_email': 'DAVID.E.DRAPER-1@DUPONT.COM', 
        'order_no': 'TEST PO', 
        'consignee_name': 'DUPONT SPECIALTY PRODUCTS USA, LLC', 
        'consignee_address': 'AFS SPRUANCE PLANT WAREHOUSE,', 
        'consignee_attn': 'DAVID DRAPER  ', 
        'currency': 'USD', 
        'tentative_shipment_date': datetime(2025, 11, 13, 0, 0), 
        'deliver_date': datetime(2025, 11, 13, 0, 0), 
        'third_party_insepection': 0, 
        'shipping_remakrs': 'NA', 
        'port_of_loading': 'SPRUANCE', 
        'place_of_delivery': 'SPRUANCE', 
        'port_of_discharge': 'NORFOLK', 
        'country_of_destination': 'US', 
        'delivery_terms': 'DAP SPRUANCE PLANT- RICHMOND', 
        'items_grade': 'EXPORT', 
        'pack_and_code': 'IS001', 
        'payment_terms': 'E80CRE', 
        'credit_days': 'E80CRE', 
        'commission': Decimal('7.70000000000000000000'), 
        'shipment_by': 'BY SEA', 
        'commission_on': '', 
        'type_of_shipment': 'ISO', 
        'lc_no': '', 
        'lc_date': datetime(1753, 1, 1, 0, 0), 
        'mode': 'PO/LC/TEL/TLX/FAX/VERBAL/OTHER', 
        'TPK1_name': 'MANUPORT LOGISTICS', 
        'TPK1_address': '305, SPRINGDALE, OPP. KASHI VIHAR APARTMENTS, NR. GITANJALI SCHOOL, VASNA ROAD, AHMEDABAD-380007, GUJARAT, INDIA', 
        'TPK1_contact': 'DE KRIS POTUMS', 
        'TPK1_phone': '+91 79 4002 1111', 
        'TPK1_email': 'K.POTUMS@MANUPORTLOGISTICS.BE', 
        'TPK1_fax': '32 3 204 95 97', 
        'consignee_phone': '33 3 44 77 52 23', 
        'consignee_email': 'SANDRA.MERCIER@SIIGROUP.COM',
        'consignee_fax' :  '33 3 44 77 52 23',
        'credit_limit_approval': '30,000,000 INR', 
        'shipping_bill': 'SHIPMENT UNDER DUTY DRAWBACK@ 1%.'
    }
    
    line_data = [
        {
            'No_': 'FG001006', 
            'Description': 'TERE PHTHALOYL CHLORIDE', 
            'Unit of Measure': 'KGS', 
            'Unit Price': Decimal('2.15300000000000000000'), 
            'Quantity': Decimal('200000.00000000000000000000'), 
            'Amount': Decimal('430600.00000000000000000000'), 
            'Grade': 'EXPORT'
        }
    ]
    
    try:
        pdf_buffer = generate_export_sales_order_pdf(
            header_data,
            line_data,
            "assets/Export_SO_Template.html",
            "Export_Sales_Order.pdf"
        )
        
        print("\n✅ Export Sales Order PDF generated successfully!")
        print(f"📄 File: Export_Sales_Order_SE-SO-25260002.pdf")
        print(f"💰 Total Amount: {format_currency(430600)} USD")
        print(f"📝 Amount in Words: {convert_number_to_words(430600)}")
        
    except Exception as e:
        print(f"❌ Error generating Export Sales Order PDF: {e}")
        print("\nMake sure you have:")
        print("1. Installed: pip install pdfkit jinja2")
        print("2. wkhtmltopdf installed on your system")
        print("3. HTML template file at assets/Export_SO_Template.html")
        print("4. Correct wkhtmltopdf path in config variable")

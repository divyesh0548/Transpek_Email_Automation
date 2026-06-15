import pdfkit
from jinja2 import Template
from datetime import datetime
from typing import Dict, List
import io
import os
import base64
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
    """
    Get fixed seller company details (Transpek Industry Limited).
    These are the company details that should appear in all sales orders.
    """
    return {
        'seller_company_name': 'Transpek Industry Limited',
        'seller_company_address': 'At & Post Ekalbara Tal. Padra, Dist. Vadodara',
        'seller_company_location': 'At & Post Ekalbara',
        'seller_company_pincode': '391440',
        'seller_company_state': 'Gujarat',
        'seller_company_state_code': '24',
        'seller_company_gst_no': '24AAACT8639B1ZI',
        'seller_company_country': 'IN'
    }

def get_signature_details():
    """
    Get fixed signature details for sales orders.
    These are the standard signatories for Transpek Industry Limited.
    """
    return {
        'reviewed_by_name': 'REGINA.FERNANDES',
        'reviewed_by_designation': ' ',
        'authorized_signatory_name': 'CHETAN.JOSHI',
        'authorized_signatory_designation': 'Authorised Signatory',
        'company_signatory_line': f'For Transpek Industry Limited'
    }

def get_standard_remarks():
    """
    Get standard remarks that appear in sales orders.
    These can be customized or made dynamic if needed.
    """
    return {
        'delivery_terms': 'FOB DESTINATION',
        'payment_terms': 'AS PER AGREEMENT',
        'validity': '30 DAYS FROM DATE OF QUOTATION',
        'delivery_note': 'DELIVERY AS PER SCHEDULE'
    }

def generate_sales_order_pdf(header_data: Dict, line_data: List[Dict], template_path: str = "assets/SO_Template.html", output_filename: str = None):
    """
    Generate Sales Order PDF using HTML template and pdfkit.
    
    Args:
        header_data: Dictionary containing sales order header information
        line_data: List of dictionaries containing line item information
        template_path: Path to the HTML template file
        output_filename: Optional filename to save PDF to disk
        
    Returns:
        bytes: PDF content as bytes
    """
    # Prepare template data
    template_data = prepare_sales_order_template_data(header_data, line_data)
    
    # Load HTML template
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()
    
    # Render template with data
    template = Template(html_template)
    rendered_html = template.render(**template_data)
    
    # Configure pdfkit options for better output
    options = {
    'page-width': '210mm',
    'page-height': '297mm',
        'orientation': 'Portrait',
        'margin-top': '15mm',
        'margin-right': '15mm',
        'margin-bottom': '15mm',
        'margin-left': '15mm',
        'encoding': 'UTF-8',
        'no-outline': None,
        'enable-local-file-access': None,
        "disable-smart-shrinking": "",
        "print-media-type": "",
        "no-print-media-type": None,
        "quiet": ""
    }
    
    # Generate PDF
    if output_filename:
        # Save to file
        pdfkit.from_string(rendered_html, output_filename, configuration=config, options=options)
        print(f"Sales Order PDF saved to: {output_filename}")
        
        # Also return the PDF content
        with open(output_filename, 'rb') as f:
            pdf_content = f.read()
    else:
        # Generate PDF in memory
        pdf_content = pdfkit.from_string(rendered_html, False, options=options, configuration=config)
    
    return pdf_content

def get_state_from_gst(gst_number: str):
    """
    Extracts the state code and state name from a GST number.
    
    Args:
        gst_number (str): GST Number (minimum 2 characters)
    
    Returns:
        tuple: (state_code, state_name), empty strings if invalid
    """
    gst_state_codes = {
        '01': 'Jammu & Kashmir',
        '02': 'Himachal Pradesh',
        '03': 'Punjab',
        '04': 'Chandigarh',
        '05': 'Uttarakhand',
        '06': 'Haryana',
        '07': 'Delhi',
        '08': 'Rajasthan',
        '09': 'Uttar Pradesh',
        '10': 'Bihar',
        '11': 'Sikkim',
        '12': 'Arunachal Pradesh',
        '13': 'Nagaland',
        '14': 'Manipur',
        '15': 'Mizoram',
        '16': 'Tripura',
        '17': 'Meghalaya',
        '18': 'Assam',
        '19': 'West Bengal',
        '20': 'Jharkhand',
        '21': 'Odisha',
        '22': 'Chhattisgarh',
        '23': 'Madhya Pradesh',
        '24': 'Gujarat',
        '25': 'Daman and Diu',
        '26': 'Dadra and Nagar Haveli',
        '27': 'Maharashtra',
        '28': 'Andhra Pradesh',
        '29': 'Karnataka',
        '30': 'Goa',
        '31': 'Lakshadweep',
        '32': 'Kerala',
        '33': 'Tamil Nadu',
        '34': 'Puducherry',
        '35': 'Andaman and Nicobar Islands',
        '36': 'Telangana',
        '37': 'Andhra Pradesh',
        '38': 'Ladakh'
    }
    
    if gst_number and len(gst_number) >= 2:
        state_code = gst_number[:2]
        state_name = gst_state_codes.get(state_code, '')
        return state_code, state_name
    else:
        return '', ''

def remove_time_from_datetime(datetime_input) -> str:
    try:
        # If it's already a datetime object, convert to string with date only
        if isinstance(datetime_input, datetime):
            return datetime_input.strftime('%Y-%m-%d')
        
        # If it's a string, split by space and return the date part
        if isinstance(datetime_input, str):
            if ' ' in datetime_input:
                return datetime_input.split(' ')[0]
            return datetime_input
        
        return str(datetime_input)
    except Exception as e:
        print(f"Error removing time: {e}")
        return str(datetime_input)



def prepare_sales_order_template_data(header_data: Dict, line_data: List[Dict]) -> Dict:
    """
    Prepare data dictionary for the Sales Order HTML template.
    """
    # Get fixed company details
    seller_company = get_seller_company_details()
    signature_details = get_signature_details()
    standard_remarks = get_standard_remarks()
    
    # Calculate totals and commission
    basic_total = 0.0
    total_commission_value = 0.0  # NEW: Track total commission
    
    # Calculate basic total and commission for each item
    for item in line_data:
        item_amount = float(item.get('Amount', 0))
        item_commission_percentage = float(item.get('commission') or 0)
        
        # Calculate commission value for this item
        item_commission_value = item_amount * (item_commission_percentage / 100)
        
        basic_total += item_amount
        total_commission_value += item_commission_value
    
    # Calculate subtotal (basic + commission)
    subtotal = basic_total
    
    # GST logic for domestic sales orders (SD):
    # - Inter-state -> IGST 18%
    # - Intra-state -> CGST 9% + SGST 9%
    inter_state_raw = header_data.get('Inter_state', False)
    if isinstance(inter_state_raw, str):
        inter_state = inter_state_raw.strip().lower() in ('1', 'true', 'yes')
    else:
        inter_state = bool(inter_state_raw)

    cgst_rate = 0.0
    sgst_rate = 0.0
    igst_rate = 0.0
    cgst_amount = 0.0
    sgst_amount = 0.0
    igst_amount = 0.0

    if inter_state:
        # Inter-state: IGST 18%
        igst_rate = 18.0
        igst_amount = subtotal * (igst_rate / 100)
    else:
        # Intra-state: CGST 9% + SGST 9%
        cgst_rate = 9.0
        sgst_rate = 9.0
        cgst_amount = subtotal * (cgst_rate / 100)
        sgst_amount = subtotal * (sgst_rate / 100)

    grand_total = subtotal + cgst_amount + sgst_amount + igst_amount
    
    # Prepare line items with proper formatting
    prepared_line_items = []
    for index, item in enumerate(line_data, 1):
        item_amount = float(item.get('Amount', 0))
        item_commission_percentage = float(item.get('commission') or 0)
        
        # Calculate commission value and final amount for this item
        item_commission_value = item_amount * (item_commission_percentage / 100)
        
        prepared_item = {
            'sr_no': index,
            'product_code': item.get('No_', ''),
            'description': item.get('Description', ''),
            'quantity': format_number(item.get('Quantity', 0)),
            'unit_of_measure': item.get('Unit of Measure', 'NOS'),
            'unit_price': format_currency(item.get('Unit Price', 0)),
            'commission': format_number(item_commission_percentage),
            'amount': format_currency(item_amount)  # MODIFIED: Show amount including commission
        }
        prepared_line_items.append(prepared_item)
        
    customer_gst_no = header_data.get('customer_gst_reg_no_', '')
    customer_state_code, customer_state_name = get_state_from_gst(customer_gst_no)

    conssignedd_gst_no = header_data.get('consignee_gst_reg_no_', '')
    consignee_state_code, consignee_state_name = get_state_from_gst(conssignedd_gst_no)
    
    return {
        # Header information
        'company_logo': get_company_logo_base64(),
        'so_no': header_data.get('no_', ''),
        'po_no': header_data.get('po_no', ''),
        'document_date': header_data.get('document_date', ''),
        'agent_name': header_data.get('agent_name', ''),
        # 'so_date': format_date_template(header_data.get('order_date', '')),
        'so_date': remove_time_from_datetime(header_data.get('order_date', '')),
        'shipping_agent': header_data.get('shipping_agent_code', ''),
        'shipment_method': header_data.get('shipment_method', ''),
        'packing_type': header_data.get('type_of_packing', ''),
        'payment_terms': header_data.get('payment_terms', ''),
        
        # Customer information (Sell To)
        'customer_name': header_data.get('sell_to_customer_name', ''),
        'customer_address': header_data.get('sell_to_address', ''),
        'customer_city': header_data.get('sell_to_city', ''),
        'customer_post_code': header_data.get('sell_to_post_code', ''),
        'customer_email': header_data.get('sell_to_e_mail', ''),
        'customer_gst_no': header_data.get('customer_gst_reg_no_', ''),
        'customer_state': customer_state_name,
        'customer_state_code': customer_state_code,
        
        # Consignee information
        'consignee_name': header_data.get('consignee_name', ''),
        'consignee_address': header_data.get('consignee_address', ''),
        'consignee_city': header_data.get('consignee_city', ''),
        'consignee_post_code': header_data.get('consignee_post_code', ''),
        'consignee_gst_no': header_data.get('consignee_gst_reg_no_', ''),
        'consignee_state' : consignee_state_name,
        'consignee_state_code': consignee_state_code,
        
        # Fixed Seller Company Details (Transpek Industry Limited)
        'seller_company_name': seller_company['seller_company_name'],
        'seller_company_address': seller_company['seller_company_address'],
        'seller_company_location': seller_company['seller_company_location'],
        'seller_company_pincode': seller_company['seller_company_pincode'],
        'seller_company_state': seller_company['seller_company_state'],
        'seller_company_state_code': seller_company['seller_company_state_code'],
        'seller_company_gst_no': seller_company['seller_company_gst_no'],
        'seller_company_country': seller_company['seller_company_country'],
        
        # Fixed Signature Details
        'reviewed_by_name': signature_details['reviewed_by_name'],
        'reviewed_by_designation': signature_details['reviewed_by_designation'],
        'authorized_signatory_name': signature_details['authorized_signatory_name'],
        'authorized_signatory_designation': signature_details['authorized_signatory_designation'],
        'company_signatory_line': signature_details['company_signatory_line'],
        
        # Fixed Standard Remarks
        'delivery_terms': standard_remarks['delivery_terms'],
        'payment_terms_note': standard_remarks['payment_terms'],
        'validity_note': standard_remarks['validity'],
        'delivery_note': standard_remarks['delivery_note'],
        
        # Line items
        'line_items': prepared_line_items,
        
        # Totals - MODIFIED: Added commission totals
        'basic_total': format_currency(basic_total),
        'total_commission_value': format_currency(total_commission_value),  # NEW: Total commission value
        'subtotal': format_currency(subtotal),  # NEW: Subtotal (basic + commission)
        'cgst_rate': cgst_rate,
        'cgst_amount': format_currency(cgst_amount),
        'sgst_rate': sgst_rate,
        'sgst_amount': format_currency(sgst_amount),
        'igst_rate': igst_rate,
        'igst_amount': format_currency(igst_amount),
        'show_igst': inter_state,
        'grand_total': format_currency(grand_total),
        'amount_in_words': convert_number_to_words(grand_total),
        
        # Additional fields
        'approver_email': header_data.get('approver_email', ''),
        'approver_name': header_data.get('approver_name', ''),
        'creator_name': header_data.get('creator_name', ''),
        'document_type_suffix': get_document_type_suffix(),
        'copy_type': 'CUSTOMER COPY',  # Can be made dynamic if needed
        
        # Current date for generation
        'current_date': datetime.now().strftime('%d-%m-%Y'),
        'current_year': datetime.now().year
    }


def get_state_code_from_gst(gst_number: str) -> str:
    """Extract state code from GST number (first 2 digits)."""
    if gst_number and len(gst_number) >= 2:
        return gst_number[:2]
    return ""

def get_document_type_suffix():
    """Get document type suffix as seen in the PDF template."""
    return '(TIL-EKB-SMD-FF-06)'  # Sales Order suffix

def format_date_template(date_str: str) -> str:
    """Format date as DD-MM-YYYY to match the PDF template."""
    if not date_str:
        return datetime.now().strftime('%d-%m-%Y')
    
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%d-%m-%Y')
    except:
        return datetime.now().strftime('%d-%m-%Y')

def format_currency(amount: float) -> str:
    """Format currency with 3 decimal places and comma separators."""
    return f"{float(amount):,.3f}"

def format_number(number: float) -> str:
    """Format number with appropriate decimal places."""
    if number == int(number):
        return str(int(number))
    return f"{float(number):.3f}"

def convert_number_to_words(amount: float) -> str:
    """Convert number to words in Indian format (enhanced version)."""
    if amount == 0:
        return "ZERO RUPEES ONLY"
    
    # Enhanced number to words conversion
    ones = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"]
    teens = ["TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"]
    tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]
    
    rupees = int(amount)
    paisa = int(round((amount - rupees) * 100))
    
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
        
        crores = num // 10000000
        num %= 10000000
        lakhs = num // 100000
        num %= 100000
        thousands = num // 1000
        num %= 1000
        hundreds = num
        
        result = ""
        if crores:
            result += convert_hundreds(crores) + " CRORE "
        if lakhs:
            result += convert_hundreds(lakhs) + " LAKH "
        if thousands:
            result += convert_hundreds(thousands) + " THOUSAND "
        if hundreds:
            result += convert_hundreds(hundreds)
        
        return result.strip()
    
    rupee_words = convert_to_words(rupees)
    
    if rupee_words:
        if rupees == 1:
            rupee_part = f"{rupee_words} RUPEE"
        else:
            rupee_part = f"{rupee_words} RUPEES"
    else:
        rupee_part = "ZERO RUPEES"
    
    if paisa == 0:
        return f"{rupee_part} AND ZERO PAISA ONLY"
    else:
        paisa_words = convert_to_words(paisa)
        return f"{rupee_part} AND {paisa_words} PAISA ONLY"

def create_so_pdf_buffer(header_data: Dict, line_data: List[Dict], template_path: str = "assets/SO_Template.html"):
    """
    Create Sales Order PDF as BytesIO buffer for Flask response.
    
    Returns:
        io.BytesIO: PDF buffer
    """
    pdf_content = generate_sales_order_pdf(header_data, line_data, template_path)
    buffer = io.BytesIO()
    buffer.write(pdf_content)
    buffer.seek(0)
    return buffer

def generate_sales_order_pdf_main(header_data: Dict, line_data: List[Dict], output_filename: str = None):
    # Use the HTML template approach
    template_path = "assets/SO_Template.html"
    
    if output_filename:
        # Generate and save to file
        pdf_content = generate_sales_order_pdf(header_data, line_data, template_path, output_filename)
        
        # Return buffer for additional use
        buffer = io.BytesIO()
        buffer.write(pdf_content)
        buffer.seek(0)
        return buffer
    else:
        # Generate in memory only
        return create_so_pdf_buffer(header_data, line_data, template_path)


# Example usage and testing
if __name__ == "__main__":
    # Test data based on your sample
    header_data = {
        'no_': 'SE/SQ-33462262',
        'po_no_': 'EXP/25-26/3441',
        'order_date': '2024-10-14T10:03',
        'shipping_agent_code': 'TRANSCON',
        'shipment_method': 'TAKEN-PART',
        'type_of_packing': 'By Tankar',
        'payment_terms': '30DAYS',
        'sell_to_customer_name': 'Dynamic Solutions Pvt Ltd',
        'sell_to_address': '397, Industrial Area, Hyderabad',
        'sell_to_city': 'Hyderabad',
        'sell_to_post_code': '500001',
        'customer_gst_reg_no_': '13ABCDE3333F1Z0',
        'sell_to_e_mail': 'divyesh.parmar@sharpandtannan.com',
        'consignee_name': 'Elite Trading Co',
        'consignee_address': '532, Commercial Zone, Bangalore',
        'consignee_city': 'Bangalore',
        'consignee_post_code': '560001',
        'consignee_gst_reg_no_': '27FGHIJ2858K1Z5',
        'consignee_state': 'Maharashtra',
        'consignee_state_code': 'MH',
        'approver_email': 'divyeshparmar0909@gmail.com',
        'approver_name': 'Divyesh Parmar'
    }
    
    line_data = [
        {
            'product_code': 'FG001110',
            'description': 'GTX 1650',
            'quantity': 50.0,
            'unit_of_measure': 'NOS',
            'unit_price': 10.0,
            'amount': 500.0,
            'commission': 10
        },
        {
            'product_code': 'FG001110',
            'description': 'Safety Gear D',
            'quantity': 50,
            'unit_of_measure': 'MT',
            'unit_price': 10,
            'amount': 500,
            'commission': 10
        }
    ]
    
    try:
        # Generate PDF
        pdf_buffer = generate_sales_order_pdf_main(
            header_data,
            line_data,
            "sales_order_sample.pdf"
        )
        
        total_value = sum(float(item['amount']) for item in line_data)
        print("Sales Order PDF generated successfully!")
        print(f"Total value: ₹{total_value:,.2f}")
        print("Template includes:")
        print("✅ Fixed Transpek Industry company details")
        print("✅ Fixed signature details (REGINA.FERNANDES & CHETAN.JOSHI)")
        print("✅ Customer and consignee information")
        print("✅ Product codes and commission data")
        print("✅ Dynamic CGST/SGST or IGST calculation")
        
    except Exception as e:
        print(f"Error generating Sales Order PDF: {e}")
        print("Make sure you have:")
        print("1. Installed: pip install pdfkit jinja2")
        print("2. wkhtmltopdf system package installed")
        print("3. HTML template file at assets/SO_Template.html")
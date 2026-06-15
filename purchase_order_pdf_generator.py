import pdfkit
from jinja2 import Template
from datetime import datetime, date
from typing import Dict, List, Optional, Union
import io
import os
import base64
from pathlib import Path

# Configure wkhtmltopdf path (adjust as needed for your system)
BASE_DIR = Path(__file__).resolve().parent
wkhtmltopdf_path = BASE_DIR / "wkhtmltox" / "wkhtmltopdf.exe"
config = pdfkit.configuration(wkhtmltopdf=str(wkhtmltopdf_path))

def get_company_logo_base64(logo_path: str = "assets/company_logo.jpg"):
    """Convert company logo to base64 string."""
    try:
        with open(logo_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode()
            return f"data:image/jpeg;base64,{encoded_string}"
    except FileNotFoundError:
        return ""

def get_buyer_company_details():
    """
    Get fixed buyer company details (Transpek Industry Limited).
    These are the company details that should appear in all purchase orders.
    """
    return {
        'buyer_company_name': 'Transpek Industry Limited',
        'buyer_company_address': 'At & Post Ekalbara Tal. Padra, Dist. Vadodara',
        'buyer_company_location': 'At & Post Ekalbara',
        'buyer_company_pincode': '391440',
        'buyer_company_state': 'Gujarat', 
        'buyer_company_state_code': '24',
        'buyer_company_gst_no': '24AAACT8639B1ZI',
        'buyer_company_country': 'IN'
    }

def get_signature_details():
    """
    Get fixed signature details for purchase orders.
    These are the standard signatories for Transpek Industry Limited.
    """
    return {
        'reviewed_by_designation': 'Reviewed By',
        'authorized_signatory_designation': 'Authorised Signatory',
        'company_signatory_line': f'For Transpek Industry Limited'
    }

def get_standard_remarks():
    """
    Get standard remarks that appear in purchase orders.
    These can be customized or made dynamic if needed.
    """
    return {
        'indent_no': 'MG/IN:24251644',
        'indent_date': '24.03.25',
        'department': 'TQM',
        'delivery_location': 'DELIVERY AT LILLERIA 1038',
        'formatted_remarks': 'INDENT NO:MG/IN:24251644  DATED:24.03.25 DEP: TQM'
    }

def generate_purchase_order_pdf(header_data: Dict, line_data: List[Dict], template_path: str = "assets/Purchase_Order_Template.html", output_filename: str = None):
    # Prepare template data
    template_data = prepare_Purchase_Order_Template_data(header_data, line_data)
    
    # Load HTML template
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()
    
    # Render template with data
    template = Template(html_template)
    rendered_html = template.render(**template_data)
    
    # Configure pdfkit options for better output
    options = {
    'page-width': '250mm',
    'page-height': '340mm',
        # 'orientation': 'landscape',
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
        print(f"Purchase Order PDF saved to: {output_filename}")
        
        # Also return the PDF content
        with open(output_filename, 'rb') as f:
            pdf_content = f.read()
    else:
        # Generate PDF in memory
        pdf_content = pdfkit.from_string(rendered_html, False, options=options, configuration=config)
    
    return pdf_content

def prepare_Purchase_Order_Template_data(header_data: Dict, line_data: List[Dict]) -> Dict:
    """
    Prepare data dictionary for the Purchase Order HTML template.
    """
    # Get fixed company details
    buyer_company = get_buyer_company_details()
    signature_details = get_signature_details()
    standard_remarks = get_standard_remarks()
    
    # Calculate totals
    basic_total = sum(float(item.get('Amount', 0)) for item in line_data)
    sgst_rate = 9.0  # 9% SGST
    cgst_rate = 9.0  # 9% CGST
    
    sgst_amount = basic_total * (sgst_rate / 100)
    cgst_amount = basic_total * (cgst_rate / 100)
    grand_total = basic_total + sgst_amount + cgst_amount
    
    # Prepare line items with proper formatting
    prepared_line_items = []
    for index, item in enumerate(line_data, 1):
        prepared_item = {
            'sr_no': index,
            'description': item.get('Description', ''),
            'quantity': format_number(item.get('Quantity', 0)),
            'unit_of_measure': item.get('Unit of Measure', 'NOS'),
            'direct_unit_cost': format_currency(item.get('Direct Unit Cost', 0)),
            'amount': format_currency(item.get('Amount', 0))
        }
        prepared_line_items.append(prepared_item)
    
    return {
        # Header information
        'company_logo': get_company_logo_base64(),
        'po_no': header_data.get('No_', ''),
        'po_date': format_date_template(header_data.get('Order Date', '')),
        'vendor_no': header_data.get('Buy-from Vendor No_', ''),
        'vendor_name': header_data.get('Buy-from Vendor Name', ''),
        'vendor_address': header_data.get('Buy-from Address', ''),
        'vendor_city': header_data.get('Buy-from City', ''),
        'vendor_post_code': header_data.get('Buy-from Post Code', ''),
        'vendor_state': header_data.get('State', ''),
        'vendor_gst_no': header_data.get('Vendor GST Reg_ No_', ''),
        'payment_terms': header_data.get('Payment Terms Code', ''),
        'creator_email': header_data.get('Creator Mail ID', ''),
        'approver_email': header_data.get('Approver Mail ID', ''),
        
        # Fixed Buyer Company Details (Transpek Industry Limited)
        'buyer_company_name': buyer_company['buyer_company_name'],
        'buyer_company_address': buyer_company['buyer_company_address'],
        'buyer_company_location': buyer_company['buyer_company_location'],
        'buyer_company_pincode': buyer_company['buyer_company_pincode'],
        'buyer_company_state': buyer_company['buyer_company_state'],
        'buyer_company_state_code': buyer_company['buyer_company_state_code'],
        'buyer_company_gst_no': buyer_company['buyer_company_gst_no'],
        'buyer_company_country': buyer_company['buyer_company_country'],
        
        # Ship To Address (same as buyer company)
        'ship_to_address': f"{buyer_company['buyer_company_address']} {buyer_company['buyer_company_pincode']}",
        'ship_to_gst': buyer_company['buyer_company_gst_no'],
        
        # Fixed Signature Details
        'reviewed_by_name': header_data.get('Creator Name', ''),
        'reviewed_by_designation': signature_details['reviewed_by_designation'],
        'authorized_signatory_name' : " ",
        'authorized_signatory_designation': signature_details['authorized_signatory_designation'],
        'company_signatory_line': signature_details['company_signatory_line'],
        
        # Fixed Standard Remarks
        'remarks_indent_no': standard_remarks['indent_no'],
        'remarks_indent_date': standard_remarks['indent_date'],
        'remarks_department': standard_remarks['department'],
        'remarks_delivery_location': standard_remarks['delivery_location'],
        'remarks_formatted': standard_remarks['formatted_remarks'],
        
        # Line items
        'line_items': prepared_line_items,
        
        # Totals
        'basic_total': format_currency(basic_total),
        'sgst_rate': sgst_rate,
        'sgst_amount': format_currency(sgst_amount),
        'cgst_rate': cgst_rate,
        'cgst_amount': format_currency(cgst_amount),
        'grand_total': format_currency(grand_total),
        'amount_in_words': convert_number_to_words(grand_total),
        
        # Additional template fields
        'document_type_suffix': get_document_type_suffix(),
        'copy_type': 'SUPPLIER COPY',  # Can be made dynamic if needed
        
        # Terms and conditions fields (can be made dynamic if needed)
        'delivery_terms': '',  # Usually empty as per PDF
        'delivery_instructions': '',  # Usually empty as per PDF
        'specification_no': '',  # Usually empty as per PDF
        'version_name': '',  # Usually empty as per PDF
        'version_date': '',  # Usually empty as per PDF
        
        # Current date for generation
        'current_date': datetime.now().strftime('%d-%m-%Y'),
        'current_year': datetime.now().year
    }

def get_document_type_suffix():
    """
    Get document type suffix as seen in the PDF template.
    """
    return '(TIL-EKB-MMD-FF-05)'

def format_date_template(date_val: Optional[Union[str, datetime, date]]) -> str:
    """Format date as DD-MM-YYYY to match the PDF template."""
    if date_val is None or date_val == '':
        return datetime.now().strftime('%d-%m-%Y')
    if isinstance(date_val, datetime):
        return date_val.strftime('%d-%m-%Y')
    if isinstance(date_val, date):
        return date_val.strftime('%d-%m-%Y')

    s = str(date_val).strip()
    if not s:
        return datetime.now().strftime('%d-%m-%Y')

    # SQL/JSON ISO variants (fromisoformat before 3.11 rejects many of these)
    iso_attempts = (
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    )
    for fmt in iso_attempts:
        try:
            return datetime.strptime(s, fmt).strftime('%d-%m-%Y')
        except ValueError:
            continue

    try:
        normalized = s.replace('Z', '+00:00') if s.endswith('Z') else s
        dt = datetime.fromisoformat(normalized)
        return dt.strftime('%d-%m-%Y')
    except ValueError:
        pass

    return datetime.now().strftime('%d-%m-%Y')

def format_currency(amount: float) -> str:
    """Format currency with 2 decimal places and comma separators."""
    return f"{float(amount):,.2f}"

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

def create_po_pdf_buffer(header_data: Dict, line_data: List[Dict], template_path: str = "assets/Purchase_Order_Template.html"):
    """
    Create Purchase Order PDF as BytesIO buffer for Flask response.
    
    Returns:
        io.BytesIO: PDF buffer
    """
    pdf_content = generate_purchase_order_pdf(header_data, line_data, template_path)
    buffer = io.BytesIO()
    buffer.write(pdf_content)
    buffer.seek(0)
    return buffer

def generate_purchase_order_pdf_main(header_data: Dict, line_data: List[Dict], output_filename: str = None):
    """
    Main function for Flask integration with fixed company details.
    
    Args:
        header_data: Your header data dictionary
        line_data: Your line data list
        output_filename: Optional filename to save to disk
    
    Returns:
        io.BytesIO: PDF buffer for Flask send_file or email attachment
    """
    # Use the HTML template approach
    template_path = "assets/Purchase_Order_Template.html"
    
    if output_filename:
        # Generate and save to file
        pdf_content = generate_purchase_order_pdf(header_data, line_data, template_path, output_filename)
        
        # Return buffer for additional use
        buffer = io.BytesIO()
        buffer.write(pdf_content)
        buffer.seek(0)
        return buffer
    else:
        # Generate in memory only
        return create_po_pdf_buffer(header_data, line_data, template_path)

def get_template_data_for_testing():
    """
    Get sample template data for testing purposes.
    This shows all the data that gets passed to the template.
    """
    sample_header = {
        'No_': 'MG/PO-25260001',
        'Order Date': '2025-10-03T08:37',
        'Buy-from Vendor No_': 'MA263',
        'Buy-from Vendor Name': 'MAITRI PRINTER & DESIGNERS',
        'Buy-from Address': 'PATEL FALIA, NR : JALARAM TEMPLE, SUBHANPURA, VADODARA',
        'Buy-from City': 'Vadodara',
        'Buy-from Post Code': '390007',
        'State': 'Gujarat',
        'Vendor GST Reg_ No_': '24ALNPP7919B1ZY',
        'Payment Terms Code': '30 Days Credit',
        'Approver Mail ID': 'approver@transpek.com',
        'Creator Mail ID': 'creator@transpek.com'
    }
    
    sample_lines = [
        {
            'description': 'Product label -4CEPC ( 4-chloro-3-ethyl1H-pyrazole - 5-carbonyl chloride ( HSN Code : 48211020 )',
            'quantity': 500.0,
            'unit_of_measure': 'NOS',
            'direct_unit_cost': 17.0,
            'amount': 8500.0
        },
        {
            'description': 'Product label- 2-Methyl-4-( trifluoromethyl)-1,3-thiazole-5-carbonyl chloride ( HSN Code : 48211020 )',
            'quantity': 500.0,
            'unit_of_measure': 'NOS',
            'direct_unit_cost': 17.0,
            'amount': 8500.0
        }
    ]
    
    return prepare_Purchase_Order_Template_data(sample_header, sample_lines)

def get_all_fixed_data():
    """
    Get all fixed data that will be included in every purchase order.
    Useful for debugging and understanding what data is automatically included.
    """
    return {
        'buyer_company': get_buyer_company_details(),
        'signatures': get_signature_details(),
        'remarks': get_standard_remarks()
    }

# Example usage and testing
if __name__ == "__main__":
    # Test data based on the PDF sample
    header_data = {
        'Document Type': 1,
        'No_': 'MG/PO-25260001',
        'Order Date': '2025-10-03T08:37',
        'Buy-from Vendor No_': 'MA263',
        'Buy-from Vendor Name': 'MAITRI PRINTER & DESIGNERS',
        'Buy-from Address': 'PATEL FALIA, NR : JALARAM TEMPLE, SUBHANPURA, VADODARA',
        'Buy-from City': 'Vadodara',
        'Buy-from Post Code': '390007',
        'State': 'Gujarat',
        'Vendor GST Reg_ No_': '24ALNPP7919B1ZY',
        'Payment Terms Code': '30 Days Credit',
        'Approver Mail ID': 'approver@transpek.com',
        'Creator Mail ID': 'creator@transpek.com'
    }
    
    line_data = [
        {
            'description': 'Product label -4CEPC ( 4-chloro-3-ethyl1H-pyrazole - 5-carbonyl chloride ( HSN Code : 48211020 )',
            'quantity': 500.0,
            'unit_of_measure': 'NOS',
            'direct_unit_cost': 17.0,
            'amount': 8500.0
        },
        {
            'description': 'Product label- 2-Methyl-4-( trifluoromethyl)-1,3-thiazole-5-carbonyl chloride ( HSN Code : 48211020 )',
            'quantity': 500.0,
            'unit_of_measure': 'NOS',
            'direct_unit_cost': 17.0,
            'amount': 8500.0
        }
    ]
    
    try:
        # Display all fixed data
        fixed_data = get_all_fixed_data()
        print("=== All Fixed Data (Automatically Included) ===")
        print("\n🏢 Buyer Company Details:")
        for key, value in fixed_data['buyer_company'].items():
            print(f"   {key}: {value}")
        
        print("\n✍️ Signature Details:")
        for key, value in fixed_data['signatures'].items():
            print(f"   {key}: {value}")
        
        print("\n📝 Standard Remarks:")
        for key, value in fixed_data['remarks'].items():
            print(f"   {key}: {value}")
        print()
        
        # Generate PDF
        pdf_buffer = generate_purchase_order_pdf_main(
            header_data,
            line_data,
            "purchase_order.pdf"
        )
        
        total_value = sum(float(item['amount']) for item in line_data)
        print("Purchase Order PDF generated successfully!")
        print(f"Total value: ₹{total_value:,.2f}")
        print("Template includes:")
        print("✅ Fixed Transpek Industry company details")
        print("✅ Fixed signature details (REGINA.FERNANDES & CHETAN.JOSHI)")
        print("✅ Fixed standard remarks section")
        
        # Show template data structure
        template_data = get_template_data_for_testing()
        print(f"\nTemplate includes {len(template_data)} total data fields")
        
    except Exception as e:
        print(f"Error generating Purchase Order PDF: {e}")
        print("Make sure you have:")
        print("1. Installed: pip install pdfkit jinja2")
        print("2. wkhtmltopdf system package installed")
        print("3. HTML template file at assets/Purchase_Order_Template.html")
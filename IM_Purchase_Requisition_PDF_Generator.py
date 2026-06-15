

import pdfkit
from jinja2 import Template
from datetime import datetime
from typing import Dict, List
import io
import os
from pathlib import Path

# Configure wkhtmltopdf path (adjust as needed for your system)
BASE_DIR = Path(__file__).resolve().parent
wkhtmltopdf_path = BASE_DIR / "wkhtmltox" / "wkhtmltopdf.exe"
config = pdfkit.configuration(wkhtmltopdf=str(wkhtmltopdf_path))

#converting logo into base64
import base64
def get_logo_base64():
    with open("assets/logo.jpg", "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    return f"data:image/jpeg;base64,{encoded_string}"

def generate_pdf_from_html(header_data: Dict, line_data: List[Dict], template_path: str = "assets/IM_Purchase_Template.html", output_filename: str = None):
    """
    Generate PDF using HTML template and pdfkit.
    
    Args:
        header_data: Dictionary containing requisition header information
        line_data: List of dictionaries containing line item information  
        template_path: Path to the HTML template file
        output_filename: Optional filename to save PDF to disk
        
    Returns:
        bytes: PDF content as bytes
    """
    
    # Prepare template data
    template_data = prepare_template_data(header_data, line_data)
    
    # Load HTML template
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()
    
    # Render template with data
    template = Template(html_template)
    rendered_html = template.render(**template_data)
    
    # Configure pdfkit options for better output
    options = {
        'page-width' : '300mm',
        'page-height': '300mm',
        'orientation': 'landscape',
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
        pdfkit.from_string(rendered_html, output_filename,  configuration=config, options=options)
        print(f"PDF saved to: {output_filename}")
        
        # Also return the PDF content
        with open(output_filename, 'rb') as f:
            pdf_content = f.read()
    else:
        # Generate PDF in memory
        pdf_content = pdfkit.from_string(rendered_html, False, options=options, configuration=config)
    
    return pdf_content

def format_date_simple(date_str: str) -> str:
    """Format date as DD/MM/YYYY."""
    if not date_str:
        return ''
    try:
        # Handle datetime strings with time component
        if ' ' in date_str:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except:
        return date_str

def format_date_template(timestamp):
    # Check if the timestamp is already a datetime object
    if isinstance(timestamp, datetime):
        dt_object = timestamp
    else:
        # Parse the timestamp string into a datetime object
        dt_object = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    
    # Convert the datetime object to the desired format
    formatted_date = dt_object.strftime("%d/%m/%Y")
    
    return formatted_date

def prepare_template_data(header_data: Dict, line_data: List[Dict]) -> Dict:
    """
    Prepare data dictionary for the HTML template.
    """
    
    # Calculate total value
    total_value = sum(float(item.get('Line Amount', 0)) for item in line_data)


    
    # Prepare line items with department name
    prepared_line_items = []
    for item in line_data:
        stock_value = float(item.get('Inventory', '0'))
        formatted_stock = f"{stock_value:.1f}" if stock_value % 1 != 0 else str(int(stock_value))
        prepared_item = {
            'item_code': item.get('No_', ''),
            'description': item.get('Description', ''),
            'dept_name': header_data.get('Indenting Department', ''),
            'quantity': item.get('Quantity', '0'),
            'uom': item.get('Unit of Measure', ''),
            'rate': item.get('Unit Cost', '0'),
            'value': item.get('Line Amount', '0'),
            'job_card_no': item.get('Job No_', ''),
            # 'stock_on_hand': item.get('Inventory', '0'),
            'stock_on_hand': formatted_stock,
            'delivery_date': format_date_simple(item.get('Expected Receipt Date', ''))
        }
        prepared_line_items.append(prepared_item)
    
    return {
        # Header information
        'logo' : header_data.get('logo_base64'),
        'reqn_no': header_data.get('No_', 'MG9962'),
        'reqn_date': format_date_template(header_data.get('Request Date', '')),
        'indenting_department': header_data.get('Indenting Department', 'FINANCE'),
        'approved_date_time': format_datetime_template(header_data.get('Request Date', '')),
        
        # Line items
        'line_items': prepared_line_items,
        
        # Totals
        'total_value': total_value,
        'amount_in_words': convert_to_words_with_paise(total_value),
        
        # Footer information
        'remarks': header_data.get('Reason', 'REPLACEMENT'),
        'prepared_by': header_data.get('Employee Name', 'EMPLOYEE NAME'),
        'approved_by': header_data.get('Approved By', '') or '-',
        'creator_email': header_data.get('Creator Mail ID', ''),
        'approver_email': header_data.get('Approver Mail ID', '')
    }

def format_datetime_template(date_str: str) -> str:
    """Format date and time as DD-MM-YYYY HH:MM:SS."""
    if not date_str:
        return datetime.now().strftime('%d-%m-%Y  %H:%M:%S')
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%d-%m-%Y  %H:%M:%S')
    except:
        return datetime.now().strftime('%d-%m-%Y  %H:%M:%S')

def format_date_simple_old(date_str: str) -> str:
    """Format date as DD/MM/YYYY."""
    if not date_str:
        return ''
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%d/%m/%Y')
    except:
        return date_str

def convert_number_to_words(n):
    # Lists for number to word mapping
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    thousands = ["", "Thousand", "Lakh", "Crore"]
    
    # Helper function to convert numbers less than 1000
    def convert_hundreds(n):
        if n == 0:
            return ""
        elif n < 20:
            return ones[n]
        elif n < 100:
            return tens[n // 10] + (" " + ones[n % 10] if n % 10 != 0 else "")
        else:
            return ones[n // 100] + " Hundred" + (" " + convert_hundreds(n % 100) if n % 100 != 0 else "")
    
    # Function to handle larger numbers like thousands, lakhs, and crores
    def number_to_words(n):
        if n == 0:
            return "Zero"
        
        result = []
        place = 0
        
        while n > 0:
            if n % 1000 != 0:
                result.append(convert_hundreds(n % 1000) + (" " + thousands[place] if thousands[place] else ""))
            n //= 1000
            place += 1
        
        return ' '.join(reversed(result)).strip()
    
    return number_to_words(n)

def convert_to_words_with_paise(n):
    integer_part = int(n)
    fractional_part = int((n - integer_part) * 100)
    
    # Convert the integer part
    words = convert_number_to_words(integer_part)
    
    # If there's a fractional part, convert it
    if fractional_part > 0:
        words += f" and {fractional_part} Paise"
    
    return words

# Example with decimal value
print(convert_to_words_with_paise(1234567.89))

def create_pdf_buffer(header_data: Dict, line_data: List[Dict], template_path: str = "assets/IM_Purchase_Template.html"):
    """
    Create PDF as BytesIO buffer for Flask response.
    
    Returns:
        io.BytesIO: PDF buffer
    """
    pdf_content = generate_pdf_from_html(header_data, line_data, template_path)
    
    buffer = io.BytesIO()
    buffer.write(pdf_content)
    buffer.seek(0)
    
    return buffer

# Flask integration function
def generate_purchase_requisition_pdf(header_data: Dict, line_data: List[Dict], output_filename: str = None):
    """
    Main function for Flask integration.
    
    Args:
        header_data: Your header data dictionary (email_data)
        line_data: Your line data list
        output_filename: Optional filename to save to disk
        
    Returns:
        io.BytesIO: PDF buffer for Flask send_file or email attachment
    """
    
    # Use the HTML template approach
    template_path = "assets/IM_Purchase_Template.html"
    
    if output_filename:
        # Generate and save to file
        pdf_content = generate_pdf_from_html(header_data, line_data, template_path, output_filename)
        
        # Return buffer for additional use
        buffer = io.BytesIO()
        buffer.write(pdf_content)
        buffer.seek(0)
        return buffer
    else:
        # Generate in memory only
        return create_pdf_buffer(header_data, line_data, template_path)

# Example usage
if __name__ == "__main__":
    # Test data
    line_data = [
        {'id': '1', 'item_code': '95', 'description': 'Test Item One Description', 'quantity': '1', 'uom': 'NOS', 'rate': '50.00', 'value': '50.00', 'job_card_no': '98', 'stock_on_hand': '3', 'delivery_date': '2025-09-30'},
        {'id': '2', 'item_code': '96', 'description': 'Test Item Two Description', 'quantity': '2', 'uom': 'KG', 'rate': '25.00', 'value': '50.00', 'job_card_no': '99', 'stock_on_hand': '2', 'delivery_date': '2025-10-01'},
        {'id': '2', 'item_code': '96', 'description': 'Test Item Two Description', 'quantity': '2', 'uom': 'KG', 'rate': '25.00', 'value': '50.00', 'job_card_no': '99', 'stock_on_hand': '2', 'delivery_date': '2025-10-01'},
        {'id': '2', 'item_code': '96', 'description': 'Test Item Two Description', 'quantity': '2', 'uom': 'KG', 'rate': '25.00', 'value': '50.00', 'job_card_no': '99', 'stock_on_hand': '2', 'delivery_date': '2025-10-01'}
    ]
    
    header_data = {
        'logo_base64': get_logo_base64(),
        'No_': 'MG9962',
        'Employee Name': 'EMPLOYEE 836',
        'Request Date': '2025-09-26 11:48:00',
        'Indenting Department': 'FINANCE',
        'Approved By': 'APPROVER12',
        'Creator Mail ID': 'divyesh.parmar@sharpandtannan.com',
        'Approver Mail ID': 'divyeshparmar0909@gmail.com',
        'Reason': 'REPLACEMENT'
    }
    
    try:
        # Generate PDF
        pdf_buffer = generate_purchase_requisition_pdf(
            header_data, 
            line_data, 
            "template.pdf"
        )
        
        total_value = sum(float(item['value']) for item in line_data)
        print("PDF generated successfully using HTML template!")
        print(f"Total value: {total_value:.2f}")
        
    except Exception as e:
        print(f"Error generating PDF: {e}")
        print("Make sure you have installed: pip install pdfkit jinja2")
        print("And wkhtmltopdf system package")
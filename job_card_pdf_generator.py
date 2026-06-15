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

def get_company_details():
    """
    Get fixed company details for Transpek Industry Limited.
    These are the company details that should appear in all job cards.
    """
    return {
        'company_name': 'Transpek Industry Limited',
        'location': 'Ekalbara',
        'document_code': '(TIL-EKB-MMD-FF-11)'
    }

def format_date_for_pdf(date_input):
    """Format date as DD-MM-YY to match the PDF template."""
    if isinstance(date_input, datetime):
        return date_input.strftime('%d-%m-%y')
    elif isinstance(date_input, str):
        try:
            dt = datetime.fromisoformat(date_input.replace('Z', '+00:00'))
            return dt.strftime('%d-%m-%y')
        except:
            return datetime.now().strftime('%d-%m-%y')
    else:
        return datetime.now().strftime('%d-%m-%y')

def format_currency(amount: float) -> str:
    """Format currency with 2 decimal places and comma separators."""
    return f"{float(amount):,.2f}"

def convert_number_to_words(amount: float) -> str:
    """Convert number to words in Indian format."""
    if amount == 0:
        return "ZERO RUPEES AND ZERO PAISA ONLY"
    
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

def load_html_template(template_path: str):
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template file not found: {template_path}")
    
    try:
        with open(template_path, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        raise Exception(f"Error reading template file {template_path}: {str(e)}")

def prepare_job_card_template_data(form_data: Dict, line_items: List[Dict]) -> Dict:
    company_details = get_company_details()
    
    # Calculate total cost
    # total_cost = sum(float(item.get('Recognized Costs Amount', 0)) for item in line_items)
    
    # # Prepare line items with proper formatting
    # prepared_line_items = []
    # for index, item in enumerate(line_items, 1):
    #     prepared_item = {
    #         'sr_no': index,
    #         'description': item.get('Description', ''),
    #         'job_task_no': item.get('Job Task No_', ''),
    #         'expected_cost': format_currency(item.get('Recognized Costs Amount', 0))
    #     }
    #     prepared_line_items.append(prepared_item)

    # Calculate total cost
    total_cost = sum(float(item.get('Total Cost (LCY)', 0) or 0) for item in line_items)
    
    # Prepare line items with proper formatting
    prepared_line_items = []
    for index, item in enumerate(line_items, 1):
        prepared_item = {
            'sr_no': index,
            'description': item.get('Description', ''),
            'job_task_no': item.get('Job Task No_', ''),
            'expected_cost': format_currency(item.get('Total Cost (LCY)', 0) or 0)
        }
        prepared_line_items.append(prepared_item)
    
    # Determine AOP/NON AOP text
    aop_text = "AOP" if form_data.get('aop_type', 0) == 1 else "NON AOP"
    
    return {
        # Company information
        'company_logo': get_company_logo_base64(),
        'company_name': company_details['company_name'],
        'location': company_details['location'],
        'document_code': company_details['document_code'],
        
        # Job Card header information
        'job_description': form_data.get('job_description', ''),
        'plant': form_data.get('plant_name', ''),
        'job_card_number': form_data.get('job_card_number', ''),
        'category': form_data.get('category', ''),
        'job_card_category': form_data.get('job_card_category', ''),
        'date_of_preparation': format_date_for_pdf(form_data.get('date_of_preparation')),
        'aop_type': aop_text,
        'department': form_data.get('department', ''),
        
        # Job Card details
        'objective': form_data.get('objective', ''),
        'expected_benefit': form_data.get('expected_benefit', ''),
        'time_required': form_data.get('time_required', ''),
        
        # Personnel information
        'prepared_by': form_data.get('prepared_by', ''),
        'checked_by': form_data.get('checked_by', ''),
        'approver_name': form_data.get('approver_name', ''),
        
        # Line items
        'line_items': prepared_line_items,
        
        # Totals
        'total_cost': format_currency(total_cost),
        'amount_in_words': convert_number_to_words(total_cost),
        
        # Current date for generation
        'current_date': datetime.now().strftime('%d-%m-%Y'),
        'current_year': datetime.now().year
    }

def generate_job_card_pdf(form_data: Dict, line_items: List[Dict], template_filename: str = "job_card_template.html", output_filename: str = None):
    # Construct template path in assets folder
    assets_folder = "assets"
    template_path = os.path.join(assets_folder, template_filename)
    
    # Check if assets folder exists
    if not os.path.exists(assets_folder):
        raise FileNotFoundError(f"Assets folder not found: {assets_folder}")
    
    # Prepare template data
    template_data = prepare_job_card_template_data(form_data, line_items)
    
    # Load HTML template from assets folder
    html_template = load_html_template(template_path)
    
    # Render template with data
    template = Template(html_template)
    rendered_html = template.render(**template_data)
    
    # Configure pdfkit options for better output
    options = {
    'page-width': '250mm',
    'page-height': '340mm',
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
        "quiet": ""
    }
    
    # Generate PDF
    if output_filename:
        # Save to file
        pdfkit.from_string(rendered_html, output_filename, configuration=config, options=options)
        print(f"Job Card PDF saved to: {output_filename}")
        
        # Also return the PDF content
        with open(output_filename, 'rb') as f:
            pdf_content = f.read()
    else:
        # Generate PDF in memory
        pdf_content = pdfkit.from_string(rendered_html, False, options=options, configuration=config)
    
    return pdf_content

def create_job_card_pdf_buffer(form_data: Dict, line_items: List[Dict], template_filename: str = "job_card_template.html"):
    pdf_content = generate_job_card_pdf(form_data, line_items, template_filename)
    buffer = io.BytesIO()
    buffer.write(pdf_content)
    buffer.seek(0)
    return buffer

def generate_job_card_pdf_main(form_data: Dict, line_items: List[Dict], template_filename: str = "job_card_template.html", output_filename: str = None):
    if output_filename:
        # Generate and save to file
        pdf_content = generate_job_card_pdf(form_data, line_items, template_filename, output_filename)
        
        # Return buffer for additional use
        buffer = io.BytesIO()
        buffer.write(pdf_content) 
        buffer.seek(0)
        return buffer
    else:
        # Generate in memory only
        return create_job_card_pdf_buffer(form_data, line_items, template_filename)

def flask_generate_job_card_pdf(form_data: Dict, line_items: List[Dict], template_filename: str = "job_card_template.html"):
    try:
        pdf_buffer = generate_job_card_pdf_main(form_data, line_items, template_filename)
        return pdf_buffer
    except Exception as e:
        print(f"Error generating Job Card PDF: {e}")
        raise e

# Example usage and testing
if __name__ == "__main__":
    # Sample data based on your provided form data
    sample_form_data = {
        'department': 'CLS-K1',
        'job_card_number': 'JCWIP-25980478',
        'aop_type': 0,
        'plant_name': 'PGCL', 
        'objective': 'IMPROVED EFFICIENCY',
        'prepared_by': 'Mike Miller',
        'creator_email': 'divyesh.parmar@sharpandtannan.com',
        'checked_by': 'Lisa Martinez',
        'approver_name': 'Emily Johnson',
        'approver_email': 'divyeshparmar0909@gmail.com',
        'date_of_preparation': datetime(2025, 3, 26, 0, 0),
        'expected_benefit': 'IMPROVED SAFETY STANDARDS',
        'time_required': '2 MONTHS',
        'job_description': '48" S.S. 316 CENTRIFUGE GMP MODEL FOR CLSK-2',
        'category': 'Capital WIP',
        'total_cost': 224398.08
    }

    sample_line_items = [
        {'job_task_no': '1', 'individual_job_description': 'JOBWORK', 'expected_cost': 59370.08},
        {'job_task_no': '2', 'individual_job_description': 'ELECTRICAL', 'expected_cost': 59959.75},
        {'job_task_no': '3', 'individual_job_description': 'ELECTRICAL', 'expected_cost': 65153.53},
        {'job_task_no': '4', 'individual_job_description': 'JOBWORK', 'expected_cost': 39914.72}
    ]
    
    try:
        # Generate PDF using template from assets folder
        pdf_buffer = generate_job_card_pdf_main(
            sample_form_data,
            sample_line_items,
            "Job_Card_Template.html",  # Template file in assets folder
            "job_card_sample.pdf"     # Output file
        )
        
        total_value = sum(float(item['expected_cost']) for item in sample_line_items)
        print("Job Card PDF generated successfully!")
        print(f"Total value: ₹{total_value:,.2f}")
        print("\nSetup Information:")
        print("✅ HTML template loaded from assets folder")
        print("✅ No embedded HTML code in Python file")
        print("✅ Template path: assets/job_card_template.html")
        print("✅ Company logo path: assets/company_logo.jpg")
        print("\nFeatures:")
        print("✅ Fixed Transpek Industry company details")
        print("✅ Job card header information")
        print("✅ Personnel information (Prepared By, Checked By, Approved By)")
        print("✅ Line items with expected costs")
        print("✅ Amount in words conversion")
        print("✅ Approval sections matching sample PDF")
        
        print("\nRequired folder structure:")
        print("project/")
        print("├── job_card_pdf_generator.py")
        print("├── assets/")
        print("│   ├── job_card_template.html")
        print("│   └── company_logo.jpg (optional)")
        print("└── generated_pdfs/")
        
    except Exception as e:
        print(f"Error generating Job Card PDF: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure you have: pip install pdfkit jinja2")
        print("2. Install wkhtmltopdf system package")
        print("3. Update the wkhtmltopdf path in the config variable")
        print("4. Create 'assets' folder in the same directory")
        print("5. Place 'job_card_template.html' file in assets folder")
        print("6. Optionally place company logo in assets folder")
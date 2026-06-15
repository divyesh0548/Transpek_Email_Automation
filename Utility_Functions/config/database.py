from sqlalchemy import create_engine
import os
import pyodbc
from sqlalchemy import text



def get_engine():
    """Get database engine"""
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise ValueError("DATABASE_URL not set in environment")
    return create_engine(conn_str)


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


def setup_database():
    try:
        with pyodbc.connect(get_odbc_connection_string()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            print("✅ Database connection successful")
            return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False
    

def row_to_dict(cursor, row):
    """Convert database row to dictionary for easier column access"""
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))

def get_table_schema(table_name):
    """Get the schema of a table"""
    try:
        # Clean table name
        clean_table_name = table_name.replace('[', '').replace(']', '')
        if '.' in clean_table_name:
            parts = clean_table_name.split('.')
            schema_name = parts[0]
            pure_table_name = parts[1]
        else:
            # If no schema specified, use dbo
            schema_name = 'dbo'
            pure_table_name = clean_table_name
        
        # Query to get column information
        query = text("""
        SELECT 
            c.name AS column_name, 
            t.name AS data_type,
            c.max_length,
            c.precision,
            c.scale,
            c.is_nullable
        FROM sys.columns c
        JOIN sys.types t ON c.user_type_id = t.user_type_id
        JOIN sys.tables tbl ON c.object_id = tbl.object_id
        JOIN sys.schemas s ON tbl.schema_id = s.schema_id
        WHERE tbl.name = :table_name
        ORDER BY c.column_id
        """)
        
        engine = get_engine()
        with engine.connect() as connection:
            result = connection.execute(query, {"table_name": pure_table_name}).fetchall()
            
            if not result:
                # Try alternate approach using INFORMATION_SCHEMA
                alt_query = text("""
                SELECT 
                    COLUMN_NAME as column_name,
                    DATA_TYPE as data_type,
                    CHARACTER_MAXIMUM_LENGTH as max_length,
                    NUMERIC_PRECISION as precision,
                    NUMERIC_SCALE as scale,
                    CASE WHEN IS_NULLABLE = 'YES' THEN 1 ELSE 0 END as is_nullable
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = :table_name
                ORDER BY ORDINAL_POSITION
                """)
                result = connection.execute(alt_query, {"table_name": pure_table_name}).fetchall()
            
            return result
    except Exception as e:
        print(f"Error getting table schema: {e}")
        return []


def get_state_info(state_abbreviation):
    
    # Dictionary of all Indian states and union territories
    indian_states = {
        'AN': {'name': 'Andaman and Nicobar Islands', 'code': 35},
        'AP': {'name': 'Andhra Pradesh', 'code': 37},
        'AR': {'name': 'Arunachal Pradesh', 'code': 12},
        'AS': {'name': 'Assam', 'code': 18},
        'BR': {'name': 'Bihar', 'code': 10},
        'CH': {'name': 'Chandigarh', 'code': 4},
        'CG': {'name': 'Chhattisgarh', 'code': 22},
        'DN': {'name': 'Dadra and Nagar Haveli and Daman and Diu', 'code': 26},
        'DL': {'name': 'Delhi', 'code': 7},
        'GA': {'name': 'Goa', 'code': 30},
        'GJ': {'name': 'Gujarat', 'code': 24},
        'HR': {'name': 'Haryana', 'code': 6},
        'HP': {'name': 'Himachal Pradesh', 'code': 2},
        'JK': {'name': 'Jammu and Kashmir', 'code': 1},
        'JH': {'name': 'Jharkhand', 'code': 20},
        'KA': {'name': 'Karnataka', 'code': 29},
        'KL': {'name': 'Kerala', 'code': 32},
        'LA': {'name': 'Ladakh', 'code': 38},
        'LD': {'name': 'Lakshadweep', 'code': 31},
        'MP': {'name': 'Madhya Pradesh', 'code': 23},
        'MH': {'name': 'Maharashtra', 'code': 27},
        'MN': {'name': 'Manipur', 'code': 14},
        'ML': {'name': 'Meghalaya', 'code': 17},
        'MZ': {'name': 'Mizoram', 'code': 15},
        'NL': {'name': 'Nagaland', 'code': 13},
        'OD': {'name': 'Odisha', 'code': 21},
        'PY': {'name': 'Puducherry', 'code': 34},
        'PB': {'name': 'Punjab', 'code': 3},
        'RJ': {'name': 'Rajasthan', 'code': 8},
        'SK': {'name': 'Sikkim', 'code': 11},
        'TN': {'name': 'Tamil Nadu', 'code': 33},
        'TS': {'name': 'Telangana', 'code': 36},
        'TR': {'name': 'Tripura', 'code': 16},
        'UP': {'name': 'Uttar Pradesh', 'code': 9},
        'UK': {'name': 'Uttarakhand', 'code': 5},
        'WB': {'name': 'West Bengal', 'code': 19}
    }
    
    # Convert to uppercase for case-insensitive lookup
    state_abbr = state_abbreviation.upper().strip()
    
    if state_abbr in indian_states:
        return {
            'state_name': indian_states[state_abbr]['name'],
            'state_code': indian_states[state_abbr]['code'],
            'abbreviation': state_abbr
        }
    else:
        return None


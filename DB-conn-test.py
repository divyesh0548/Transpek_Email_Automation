import pyodbc
from Utility_Functions.config.db_utils import get_odbc_connection_string

def test_db_connection():
    try:
        conn_string = get_odbc_connection_string()

        with pyodbc.connect(conn_string, timeout=5) as conn:
            cursor = conn.cursor()

            # Simple test query
            cursor.execute("SELECT 1")

            result = cursor.fetchone()

            if result and result[0] == 1:
                print("Database connection successful ✅")
            else:
                print("Connection established but test query failed ❌")

    except pyodbc.Error as e:
        print("Database connection failed ❌")
        print("Error:", e)


if __name__ == "__main__":
    test_db_connection()
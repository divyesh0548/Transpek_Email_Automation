import re

import pyodbc
from dotenv import load_dotenv

from load_secrets import load_secrets
from Utility_Functions.config.constants import SALES_ORDER_MAIN
from Utility_Functions.config.database import get_odbc_connection_string


FILTER_BY_DOCUMENT_TYPE = True


def extract_prefix(order_number):
    if not order_number:
        return None

    match = re.match(r"[A-Za-z]+", str(order_number).strip())
    return match.group(0).upper() if match else None


def main():
    load_dotenv()
    load_secrets()

    where_clauses = [
        "[No_] IS NOT NULL",
        "LTRIM(RTRIM([No_])) <> ''",
    ]
    if FILTER_BY_DOCUMENT_TYPE:
        where_clauses.append("[Document Type] = 1")

    query = f"""
        SELECT DISTINCT [No_]
        FROM {SALES_ORDER_MAIN}
        WHERE {" AND ".join(where_clauses)}
    """

    with pyodbc.connect(get_odbc_connection_string(), timeout=30) as connection:
        cursor = connection.cursor()
        cursor.execute(query)
        order_numbers = [row[0] for row in cursor.fetchall()]

    prefixes = set()
    for order_number in order_numbers:
        prefix = extract_prefix(order_number)
        if prefix:
            prefixes.add(prefix)

    prefixes = sorted(prefixes)

    print(f"Unique sales order prefixes ({len(prefixes)}):")
    for prefix in prefixes:
        print(prefix)


if __name__ == "__main__":
    main()

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
import os

def load_csv_to_snowflake(csv_path: str, table_name: str):
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
    )

    df = pd.read_csv(csv_path)

    # Snowflake wants uppercase column names
    df.columns = [c.upper() for c in df.columns]

    success, nchunks, nrows, _ = write_pandas(conn, df, table_name.upper(), auto_create_table=True)
    print(f"Loaded {nrows} rows into {table_name}")
    conn.close()

if __name__ == "__main__":
    load_csv_to_snowflake("mlb_data.csv", "MLB_STATS")  # adjust filenames as needed
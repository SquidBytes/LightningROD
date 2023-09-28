import psycopg2
from psycopg2 import sql
from config import psql_host, psql_database, psql_user, psql_password


# Define the database connection parameters
db_params = {
    'host': psql_host,
    'database': psql_database,
    'user': psql_user,
    'password': psql_password,
}

# Define the table creation SQL statement
table_creation_sql = """
    CREATE TABLE IF NOT EXISTS energyTransferLogs (
        id SERIAL PRIMARY KEY,
        device_id VARCHAR(255),
        charger_type VARCHAR(255),
        energy_consumed NUMERIC,
        time_stamp TIMESTAMP,
        target_soc INTEGER,
        plug_in_time TIMESTAMP,
        plug_out_time TIMESTAMP,
        total_plugged_in_time INTEGER,
        plug_in_dte NUMERIC,
        total_distance_added NUMERIC,
        power_min NUMERIC,
        power_max NUMERIC,
        power_average NUMERIC,
        weighted_average NUMERIC,
        soc_first INTEGER,
        soc_last INTEGER,
        soc_difference INTEGER,
        energy_transfer_begin TIMESTAMP,
        energy_transfer_end TIMESTAMP,
        total_time INTEGER,
        location_name VARCHAR(255),
        city VARCHAR(255),
        state VARCHAR(255),
        country VARCHAR(255),
        postal_code VARCHAR(255),
        latitude NUMERIC,
        longitude NUMERIC,
        cost NUMERIC
    )
"""

# Connect to the PostgreSQL server
try:
    conn = psycopg2.connect(**db_params)
    cursor = conn.cursor()

    # Create the database 'charge_logs'
    cursor.execute("CREATE DATABASE charge_logs")

    # Switch to the 'charge_logs' database
    conn.close()
    db_params['database'] = 'charge_logs'
    conn = psycopg2.connect(**db_params)
    cursor = conn.cursor()

    # Create the 'energyTransferLogs' table
    cursor.execute(table_creation_sql)
    conn.commit()

    print("Database and table created successfully!")

except Exception as e:
    print(f"Error: {e}")

finally:
    if conn:
        conn.close()

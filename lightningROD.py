import psycopg2
import json
import os
from fordpass_new import Vehicle
from config import fp_username, fp_password, fp_vin, fp_region, fp_token
from config import psql_database, psql_host, psql_password, psql_user
from config import idb_bucket, idb_org, idb_token, idb_url
from config import homeCostkWh, workCostkWh, otherCostkWh

def downloadChargeLog():

    my_vehicle = Vehicle(fp_username, fp_password, fp_vin, fp_region, True, fp_token)
    log_data = my_vehicle.charge_log()

    os.chdir('/root/config/custom_components/fordpass/')

    try:
        # Load the existing JSON data if the file exists
        with open('charge_logs.json', 'r') as file:
            existing_data = json.load(file)
    except FileNotFoundError:
        # If the file doesn't exist, initialize with an empty list
        existing_data = []

    # Extract unique identifiers (IDs) from existing data
    existing_ids = set(entry.get("id") for entry in existing_data)

    # Filter the new data to keep only entries with unique IDs not in existing data
    new_data = [entry for entry in log_data if entry.get("id") not in existing_ids]

    # Append the new data to the existing data
    existing_data.extend(new_data)

    # Write the updated JSON data to the file
    with open('charge_logs.json', 'w') as file:
        json.dump(existing_data, file, indent=4)

def insertPsql():
    os.chdir('/root/config/custom_components/fordpass/')
    conn = psycopg2.connect(
        host=psql_host,
        database=psql_database,
        user=psql_user,
        password=psql_password)

    # Create a cursor object to interact with the database
    cursor = conn.cursor()

    with open('charge_logs.json', 'r') as json_file:
        data = json.load(json_file)

    for item in data:
        # Check if the ID already exists in the database
        cursor.execute("SELECT COUNT(*) FROM energyTransferLogs WHERE id = %s", (item['id'],))
        count = cursor.fetchone()[0]

        # Calculate the cost based on kWh costs from const.py
        cost = calculateCost(item["location"]["name"], item["energyConsumed"])

        # If the ID does not exist, insert the new data
        if count == 0:
            sql_query = """
                INSERT INTO energyTransferLogs (
                    id, 
                    device_id, 
                    charger_type, 
                    energy_consumed, 
                    time_stamp,
                    target_soc,
                    plug_in_time, 
                    plug_out_time, 
                    total_plugged_in_time, 
                    plug_in_dte, 
                    total_distance_added,
                    power_min, 
                    power_max, 
                    power_average, 
                    weighted_average,
                    soc_first, 
                    soc_last, 
                    soc_difference,
                    energy_transfer_begin, 
                    energy_transfer_end, 
                    total_time,
                    location_name,
                    city,
                    state,
                    country,
                    postal_code,
                    latitude,
                    longitude,
                    cost
                ) VALUES (
                    %s, %s, %s, %s, %s, 
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """
            values = (
                item['id'], 
                item['deviceId'], 
                item['chargerType'], 
                item['energyConsumed'], 
                item['timeStamp'],
                item['targetSoc'],
                item['plugDetails']['plugInTime'], 
                item['plugDetails']['plugOutTime'], 
                item['plugDetails']['totalPluggedInTime'],
                item['plugDetails']['plugInDte'], 
                item['plugDetails']['totalDistanceAdded'],
                item['power']['min'], 
                item['power']['max'], 
                item['power']['average'], 
                item['power']['weightedAverage'],
                item['stateOfCharge']['firstSOC'], 
                item['stateOfCharge']['lastSOC'], 
                item['stateOfCharge']['socDifference'],
                item['energyTransferDuration']['begin'], 
                item['energyTransferDuration']['end'], 
                item['energyTransferDuration']['totalTime'],
                item['location']['name'],
                item['location']['address']['city'],
                item['location']['address']['state'],
                item['location']['address']['country'],
                item['location']['address']['postalCode'],
                item['location']['latitude'],
                item['location']['longitude'],
                cost
            )

            cursor.execute(sql_query, values)

    # Commit and close the database connection
    conn.commit()
    cursor.close()
    conn.close()

def calculateCost(locationName, energyConsumed):
    # Implement your cost calculation logic here
    if locationName == 'Work':
        cost = workCostkWh * energyConsumed
    elif locationName == 'Home':
        cost = homeCostkWh * energyConsumed
    else:
        cost = otherCostkWh * energyConsumed
    return cost

if __name__ == "__main__":
    os.chdir('/root/config/custom_components/fordpass/')
    downloadChargeLog()
    insertPsql()

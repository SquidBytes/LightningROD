import json
import requests
import sys
import os
import re
from datetime import datetime

def get_autonomic_token(ford_access_token):
    url = "https://accounts.autonomic.ai/v1/auth/oidc/token"
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded"
    }
    data = {
        "subject_token": ford_access_token,
        "subject_issuer": "fordpass",
        "client_id": "fordpass-prod",
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt"
    }

    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        autonomic_token_data = response.json()
        return autonomic_token_data

    except requests.exceptions.HTTPError as errh:
        print(f"HTTP Error: {errh}")
        print(f"Trying refresh token")
        get_autonomic_token(ford_refresh_token)
    except requests.exceptions.ConnectionError as errc:
        print(f"Error Connecting: {errc}")
        sys.exit()
    except requests.exceptions.Timeout as errt:
        print(f"Timeout Error: {errt}")
        sys.exit()
    except requests.exceptions.RequestException as err:
        print(f"Something went wrong: {err}")
        sys.exit()


def get_vehicle_status(vin, access_token):
    BASE_URL = "https://api.autonomic.ai/"
    endpoint = f"v1beta/telemetry/sources/fordpass/vehicles/{vin}:query"
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",  # Replace 'your_autonom_token' with the actual Autonomic API token
        "Content-Type": "application/json",
        "accept": "*/*"
    }
    redactionItems = ["lat", "lon", "vehicleId", "vin", "latitude", "longitude"]

    try:
        response = requests.post(url, headers=headers, json={})
        response.raise_for_status()  # Raise HTTPError for bad requests (4xx and 5xx status codes)

        # Parse the JSON response
        vehicle_status_data = response.json()

        # Redact sensitive information
        redact_json(vehicle_status_data, redactionItems)
        return vehicle_status_data

    except requests.exceptions.HTTPError as errh:
        print(f"HTTP Error: {errh}")
    except requests.exceptions.ConnectionError as errc:
        print(f"Error Connecting: {errc}")
    except requests.exceptions.Timeout as errt:
        print(f"Timeout Error: {errt}")
    except requests.exceptions.RequestException as err:
        print(f"Something went wrong: {err}")
            
if __name__ == "__main__":
    fordPassDir = "/config/custom_components/fordpass"
    existingfordToken = "*_fordpass_token.txt"
    if os.path.isfile(os.path.join(fordPassDir, existingfordToken)):
        with open(os.path.join(fordPassDir, existingfordToken), 'r') as file:
            fp_token_data = json.load(file)
        fpToken = fp_token_data['access_token']
        fpRefresh = fp_token_data['refresh_token']
    else:
        print(f"Error finding FordPass token text file: {os.path.join(fordPassDir, existingfordToken)}")
        sys.exit()

    ##### Implement VIN grabbing
    if fp_vin == "":
        print("Please enter your VIN into the python script")
        sys.exit()

    ###### See if I can implement Vehicle name grabbing
    vehicleName = "Lightning"

    # Exchange Fordpass token for Autonomic Token
    autoToken = get_autonomic_token(fpToken)
    vehicleStatus = get_vehicle_status(fp_vin, autoToken["access_token"])

    current_datetime = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    fileName = os.path.join(fordPassDir, f"{vehicleName}_{current_datetime}.json")

    # Write the updated JSON data to the file
    with open(fileName, 'w') as file:
        json.dump(vehicle_status, file, indent=4)
    print("done")
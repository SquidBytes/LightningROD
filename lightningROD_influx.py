import influxdb_client
import json
import os
import glob
import sys
import requests
from datetime import datetime, timedelta
from config import idb_bucket, idb_org, idb_token, idb_url
# from config import homeCostkWh, workCostkWh, otherCostkWh


class LightningROD:
    def __init__(self, host, token, org, bucket):
        self.bucket = bucket
        self.org = org
        self.host = host
        self.token = token

        self.client = influxdb_client.InfluxDBClient(host, token=token, org=org, bucket=bucket)

    def readData(self, measurement, time):
        formatted_time = datetime.strptime(time, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        start_time = (datetime.strptime(formatted_time, "%Y-%m-%dT%H:%M:%S.%fZ") - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        stop_time = (datetime.strptime(formatted_time, "%Y-%m-%dT%H:%M:%S.%fZ") + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        query = f'from(bucket: "{self.bucket}") |> range(start: {start_time}, stop: {stop_time}) |> filter(fn: (r) => r._measurement == "{measurement}")'

        results = self.client.query_api().query(org=self.org, query=query)
        print(results)
        data = []
        for table in results:
            for record in table.records:
                data.append(record)

        return data

    def writeData(self, measurement, fields, time):
        point = influxdb_client.Point(measurement)
        for field, value in fields.items():
            point.field(field, value)
        point.time(time)

        self.client.write_api().write(bucket=self.bucket, record=[point])

    def checkFordPassToken():
        fordPassDir = "/config/custom_components/fordpass"
        existingfordToken = os.path.join(fordPassDir, "*_fordpass_token.txt")
        userToken = glob.glob(existingfordToken)
        
        if userToken:
            for userTokenMatch in userToken:
                with open(userTokenMatch, 'r') as file:
                    fp_token_data = json.load(file)
                return fp_token_data
        else:
            print(f"Error finding FordPass token text file: {existingfordToken}, {userToken}")
            sys.exit()

    def get_autonomic_token(fp_token_data):
        if isinstance(fp_token_data, dict):
            fordAccessToken = fp_token_data["access_token"]
            fordRefreshToken = fp_token_data["refresh_token"]
        url = "https://accounts.autonomic.ai/v1/auth/oidc/token"
        headers = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded"
        }
        data = {
            "subject_token": fordAccessToken,
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
            get_autonomic_token(fordRefreshToken)
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

        try:
            response = requests.post(url, headers=headers, json={})
            response.raise_for_status()  # Raise HTTPError for bad requests (4xx and 5xx status codes)

            # Parse the JSON response
            vehicle_status_data = response.json()
            return vehicle_status_data

        except requests.exceptions.HTTPError as errh:
            print(f"HTTP Error: {errh}")
        except requests.exceptions.ConnectionError as errc:
            print(f"Error Connecting: {errc}")
        except requests.exceptions.Timeout as errt:
            print(f"Timeout Error: {errt}")
        except requests.exceptions.RequestException as err:
            print(f"Something went wrong: {err}")

    def logThis(self, status, logMe):
        vicMetrics = status["metrics"]
        systemOfMeasure = vicMetrics["displaySystemOfMeasure"]["value"]
        if logMe in vicMetrics:
            if isinstance(vicMetrics[logMe]["value"], int):
                if "Range" or "Distance" in logMe and systemOfMeasure == "IMPERIAL":
                    print("CONVERT")
                    fields = self.convertMiKm(vicMetrics[logMe]["value"])
                elif "Temp" in logMe and systemOfMeasure == "IMPERIAL":
                    fields = self.convertCF(vicMetrics[logMe]["value"])
                else:
                    fields = vicMetrics[logMe]["value"]
            else:
                fields = vicMetrics[logMe]["value"]
            time = vicMetrics[logMe]["updateTime"]
            # Check if the measurement exists in InfluxDB.

            existingData = self.readData(measurement=logMe, time=time)
            if len(existingData) == 0:
                # The measurement does not exist, so write it to InfluxDB.
                self.writeData(logMe, fields, time)
 
    def convertMiKm(self, value):
        return round(float(value) / 1.60934)

    def convertCF(self, value):
        return round(float(value * 9/5) + 32)





if __name__ == "__main__":

    lightningLog = LightningROD(idb_url, token=idb_token, org=idb_org, bucket=idb_bucket)
    # fp_vin = ""

    # autonomic_token = get_autonomic_token(fpToken)
    # vehicle_status = get_vehicle_status(fp_vin, autonomic_token["access_token"])

    currentDir = os.path.dirname(os.path.realpath(__file__))
    testJson = os.path.join(currentDir, 'test.json')
    with open(testJson, "r") as testJsonData:
        data = json.load(testJsonData)
    current_datetime = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    itemsToLog = {
        "ambientTemp",
        "outsideTemperature",
        "tripDistanceAccumulated",
        "odometer",
        "speed",
        "xevPlugChargerStatus",
        "xevBatteryCapacity",
        "xevBatteryMaximumRange",
        "xevBatteryStateOfCharge",
        "xevBatteryPerformanceStatus,",
        "tripXevBatteryRangeRegenerated",
        "tripXevBatteryChargeRegenerated",
        "xevBatteryEnergyRemaining",
        "xevBatteryChargeDisplayStatus",
        "xevChargeStationPowerType",
        "xevChargeStationCommunicationStatus",
        "tripXevBatteryDistanceAccumulated",
        "xevBatteryTemperature",
        "xevBatteryChargerCurrentOutput",
        "xevBatteryChargerVoltageOutput",
        "xevBatteryActualStateOfCharge",
        "xevBatteryIoCurrent",
        "xevBatteryVoltage",
        "xevTractionMotorCurrent",
        "xevTractionMotorVoltage"
    }

    for item in itemsToLog:
        lightningLog.logThis(data, item)

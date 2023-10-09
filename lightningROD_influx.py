import json
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from myconfig import idb_bucket, idb_org, idb_token, idb_url
from myconfig import homeCostkWh, workCostkWh, otherCostkWh

class InfluxDBWriter:
    def __init__(self, token, org, bucket, url=idb_url):
        self.client = InfluxDBClient(url=url, token=token)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()
        self.org = org
        self.bucket = bucket

    def write(self, data):
        self.write_api.write(bucket=self.bucket, org=self.org, record=data)

    def query(self, flux_query):
        return self.query_api.query_data_frame(org=self.org, query=flux_query)

def calculateCost(locationName, energyConsumed):
    # Implement your cost calculation logic here
    if locationName == 'Work':
        cost = workCostkWh * energyConsumed
    elif locationName == 'Home':
        cost = homeCostkWh * energyConsumed
    else:
        cost = otherCostkWh * energyConsumed
    return cost

def main():
    # Open the JSON file
    with open("charge_logs.json", "r") as f:
        charge_logs = json.load(f)

    # Create an InfluxDB writer
    influxdb_writer = InfluxDBWriter(token=idb_token, org=idb_org, bucket=idb_bucket)

    flux_query = f'from(bucket: "{influxdb_writer.bucket}") |> range(start: -1d) |> filter(fn: (r) => r._measurement == "lightningrod")'
    query_result = influxdb_writer.query(flux_query)

    # Iterate over the charge logs
    for charge_log in charge_logs:
        # Check if the log entry already exists in InfluxDB
        if charge_log not in query_result.to_dict(orient='records'):
            data = {
                "measurement": "lightningrod",
                "tags": {
                    "deviceId": charge_log["deviceId"],
                    "eventType": charge_log["eventType"],
                    "chargerType": charge_log["chargerType"],
                },
                "time": charge_log["timeStamp"],
                "fields": {
                    "energyConsumed": charge_log["energyConsumed"],
                    "timeStamp": charge_log["timeStamp"],
                    "preferredChargeAmount": charge_log["preferredChargeAmount"],
                    "targetSoc": charge_log["targetSoc"],
                    "totalPluggedInTime": charge_log["plugDetails"]["totalPluggedInTime"],
                    "plugInDte": charge_log["plugDetails"]["plugInDte"],
                    "totalDistanceAdded": charge_log["plugDetails"]["totalDistanceAdded"],
                    "minPower": charge_log["power"]["min"],
                    "maxPower": charge_log["power"]["max"],
                    "medianPower": charge_log["power"]["median"],
                    "averagePower": charge_log["power"]["average"],
                    "weightedAveragePower": charge_log["power"]["weightedAverage"],
                    "firstSoc": charge_log["stateOfCharge"]["firstSOC"],
                    "lastSoc": charge_log["stateOfCharge"]["lastSOC"],
                    "socDifference": charge_log["stateOfCharge"]["socDifference"],
                    "energyTransferDuration": charge_log["energyTransferDuration"]["totalTime"],
                    "locationId": charge_log["location"]["id"],
                    "locationType": charge_log["location"]["type"],
                    "locationName": charge_log["location"]["name"],
                    "locationAddress": charge_log["location"]["address"]["address1"],
                    "locationCity": charge_log["location"]["address"]["city"],
                    "locationState": charge_log["location"]["address"]["state"],
                    "locationCountry": charge_log["location"]["address"]["country"],
                    "locationPostalCode": charge_log["location"]["address"]["postalCode"],
                    "locationGeoHash": charge_log["location"]["geoHash"],
                    "locationLatitude": charge_log["location"]["latitude"],
                    "locationLongitude": charge_log["location"]["longitude"],
                    "locationTimeZoneOffset": charge_log["location"]["timeZoneOffset"],
                    "locationNetwork": charge_log["location"]["network"],
                    "cost": calculateCost(charge_log["location"]["name"], charge_log["energyConsumed"]),
                },
            }

            try:
                influxdb_writer.write(data)
                print(f"Data written to InfluxDB for ID: {charge_log['id']}")
            except Exception as e:
                print(f"Error writing data to InfluxDB: {e}")

if __name__ == "__main__":
    main()
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from config import (
    homeCostkWh,
    workCostkWh,
    otherCostkWh
)

class InfluxDBHandler:
    def __init__(self, url, token, org, bucket):
        if url.startswith('http://'):
            self.url = url
        else:
            self.url = f"http://{url}"
        self.token = token
        self.org = org
        self.bucket = bucket
        self.client = self.connect()

    def connect(self):
        client = InfluxDBClient(
            url=f"{self.url}:8086",
            token=self.token,
            org=self.org,
        )
        return client
    
    def getCost(self, energyConsumed, locationName):
        if locationName == 'Work':
            cost = energyConsumed * workCostkWh
        elif locationName == 'Home':
            cost = energyConsumed * homeCostkWh
        else:
            cost = energyConsumed * otherCostkWh
        return cost
    def write_charge_logs_to_influxdb(self, charge_logs):
        write_api = self.client.write_api(write_options=SYNCHRONOUS)

        points = []  # Create an empty list to store points

        for log in charge_logs:
            point = Point("charge_log") \
                .tag("id", log["id"]) \
                .field("deviceId", log["deviceId"]) \
                .field("eventType", log["eventType"]) \
                .field("chargerType", log["chargerType"]) \
                .field("energyConsumed", log["energyConsumed"]) \
                .field("cost", self.getCost(energyConsumed=log["energyConsumed"], locationName=log["location"]["name"])) \
                .field("timeStamp", log["timeStamp"]) \
                .field("totalPluggedInTime", log["plugDetails"]["totalPluggedInTime"]) \
                .field("totalDistanceAdded", log["plugDetails"]["totalDistanceAdded"]) \
                .field("powerMin", log["power"]["min"]) \
                .field("powerMax", log["power"]["max"]) \
                .field("powerMedian", log["power"]["median"]) \
                .field("powerAverage", log["power"]["average"]) \
                .field("powerWeightedAverage", log["power"]["weightedAverage"]) \
                .field("firstSOC", log["stateOfCharge"]["firstSOC"]) \
                .field("lastSOC", log["stateOfCharge"]["lastSOC"]) \
                .field("socDifference", log["stateOfCharge"]["socDifference"]) \
                .field("energyTransferBegin", log["energyTransferDuration"]["begin"]) \
                .field("energyTransferEnd", log["energyTransferDuration"]["end"]) \
                .field("energyTransferTotalTime", log["energyTransferDuration"]["totalTime"]) \
                .field("locationName", log["location"]["name"]) \
                .field("locationAddress", log["location"]["address"]["address1"]) \
                .field("locationAddressCity", log["location"]["address"]["city"]) \
                .field("locationAddressState", log["location"]["address"]["state"]) \
                .field("locationAddressCountry", log["location"]["address"]["country"]) \
                .field("locationAddressPostal", log["location"]["address"]["postalCode"]) \
                .field("locationGeoHash", log["location"]["geoHash"]) \
                .field("locationLat", log["location"]["latitude"]) \
                .field("locationLong", log["location"]["longitude"]) \

            # Add timestamp to the point
            point = point.time(log["timeStamp"])
            points.append(point)  # Add the point to the list

        # Write the list of points to InfluxDB
        write_api.write(bucket=self.bucket, org=self.org, record=points)
    def close(self):
        self.client.close()

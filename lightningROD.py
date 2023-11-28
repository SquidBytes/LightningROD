import json
import pathlib
import os
from auth import FordPassAuthenticator, FordPassChargeLogsDownloader
from influx import InfluxDBHandler
from config import influx_url, influx_org, influx_bucket, influx_token
from config import fordpass_username, fordpass_password, fordpass_vin, fordpass_region


if __name__ == "__main__":
    lightningRDir = pathlib.Path(__file__).parent.resolve()
    authenticator = FordPassAuthenticator(fordpass_username, fordpass_password, fordpass_vin, fordpass_region)
    energyLogs = FordPassChargeLogsDownloader(authenticator)
    authenticator.auth()
    energyLogs.download_charge_logs()

    influx_handler = InfluxDBHandler(
        url=influx_url,
        token=influx_token,
        org=influx_org,
        bucket=influx_bucket,
    )

    lightningRLogs = os.path.join(lightningRDir, "charge_logs.json")

    with open(lightningRLogs, 'r') as file:
        charge_logs = json.load(file)

    influx_handler.write_charge_logs_to_influxdb(charge_logs)
    influx_handler.close()

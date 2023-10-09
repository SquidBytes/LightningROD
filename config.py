# config.py

# Ford Pass Username
fp_username = "your_username"
# Ford Pass Password
fp_password = "your_password"
fp_region = "North America & Canada"
        # "UK&Europe"
        # "Australia"
        # "North America & Canada"
# Token .txt file from fordpass-ha after setup
fp_token = 'token.txt'
# Vehicle VIN to log
fp_vin = 'your_vin'

# Postgresql info
psql_host = "postgresql_host_ip"
psql_database= "charging_logs"
psql_user= "postgresql_username"
psql_password= "postgresql_password"

# InfluxDB info
idb_token = "API Token"
idb_org = "ord"
idb_url = "ip"
idb_bucket="lightningrod"

# Cost per kWh
homeCostkWh = 0.104550
workCostkWh = 0.00
otherCostkWh = 0.40

## Can be expanded on, examples
# eaCostkWh = 0.0
# chargepointCostkWh = 0.0
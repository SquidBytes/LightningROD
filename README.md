# LightningROD (Record of Docks)
Lightning Record of Docks (logs) for your F-150 Lightning!

## Currently Working On
Migrating from Postgresql to InfluxDB. 
 - This should make it easier for new users to setup
 - HomeAssistant talks to InfluxDB easily
This would allow logs to be put into the database from HomeAssistant sensors. Which, depending on the sensors the user has, could expand the dashboard to include Trips taken or other information from various sensors.

 - I am also working on the `fordpass-ha` integration to implement charging sensors, and charging actions (Stop Charging, Start Charging, etc).
This is why it makes the most sense to move to InfluxDB. This would make LightningROD mainly just be a collection of Grafana dashboards.
However, if someone is not running HomeAssistant I will maintain the script downloading charge logs.

I DO NOT recommend setting this up currently.

## Info
TO be clear. This is nothing special or new. It is how I store, and view, my charge logs.
I am hoping to make it an easy setup for new users and work with others to expand this.

This is my first public repo. I am not a developer. 
This was created in my spare time because I wasn't happy with FordPass Charge Logs. 

I am using it for my F-150 Lightning, but it *should* work with other Ford EV's like the Mustang Mach-E.

Currently, these scripts are piggybacking off of the fordpass-ha integration.
I am using these scripts, in conjunction with HomeAssistant, to automate logging my docks (logs) into a self hosted database (postgresql).
I am then using Grafana to make all that data pretty.

These *should* be able to integrate into different setups, but its probably best to use as a template. 
I did my best to make this as new user friendly as I know, and have the ability to.

I am happy to collab with others to expand this!

<a href="https://www.buymeacoffee.com/SquidBytes" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>

## Account Warning / Information
- It is known that Ford *may* lock out the account associated with a user using the API for "Unauthorized API/Third Party Access"
- It is recommended to use a secondary account for any API Queries
- Users have also reported being locked out for emails containing "+" in their email. Info [Here](https://github.com/itchannel/fordpass-ha#account-warning-sep-2023)

## Credit 
- https://github.com/itchannel - API Authentication code, Home Assistant Integration.
- TeslaMate - Inspiration

## Requirements
My setup is as following:
- Home Media Server running Unraid
	- postgresql15 docker
	- Grafana docker
	- HomeAssistan OS VM
		- HACS integration in HomeAssistant
		- fordpass-ha integration by [itchannel](https://github.com/itchannel )

## Goals
- Easy all in one self hosted install
- Easy log and display from HomeAssistant sensors (InfluxDB, Postgresql, etc)
- ProPower Onboard Logs
- "DrivingScore" Logs
- OBD2 Logging Integration (example: Torque for Hass)

## Getting Started

Please check [Currently Working On](https://github.com/SquidBytes/LightningROD#currently-working-on) before installing anything

Setup and configure the fordpass-ha integration
Install `Postgresql15` (I used a docker on Unraid)
Install `Grafana` (I used a docker on Unraid)

Input your details into config.py
```python
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

# Cost per kWh
home_cost = 0.104550
work_cost = 0.00
other_cost = 0.40
```

Run **create_database.py** to create the database
```python
python3 create_database.py
```

## Install

Place:
```python
config.py
fordpass_new.py
lightningROD.py
```
Into the fordpass-ha directory:
```/root/config/custom_components/fordpass```

Yes, overwrite the existing `fordpass_new.py`
This version contains the function I'm calling to download the charge logs.
This might change later as `fordpass-ha` gets updates.

## Running
From HomeAssistant open a terminal and run

```python
python3 ~/config/custom_components/fordpass/lightningROD.py
```

## Optional Automation

Create, or update your `shellcommand.yaml`:
```yaml
lightningrod: "python /root/config/custom_components/fordpass/lightningrod.py"
```

Create a `charging_status` sensor
```yaml
{% if state_attr('sensor.fordpass_elveh', 'Charging Status') == 'ChargingAC' %}
Charging
{% elif state_attr('sensor.fordpass_elveh', 'Charging Status') == 'ChargeTargetReached' %}
Charging Complete
{% else %}
NOT Charging
{% endif %}
```

Create, or update, your `automations.yaml`
This automation triggers the script to run 2 hours after the charging state changes
```yaml
  trigger:
  - platform: state
    entity_id:
    - sensor.charging_status
    to: NOT Charging
    for:
      hours: 0
      minutes: 0
      seconds: 0
    from: Charging
  condition:
  - condition: state
    entity_id: sensor.charging_status
    state: NOT Charging
    for:
      hours: 2
      minutes: 0
      seconds: 0
  action:
  - service: shell_command.lightningrod
    data: {}
run
  mode: single
```

## Grafana
- Setup your postgresql database as a connection
- Create any graph you want
- I have included my [Grafana Dashbaord Here](https://github.com/SquidBytes/LightningROD/tree/main/grafana)

## Screenshot
LightningROD Dashboard
![Alt text](/assets/LightningROD.png?raw=true "LightningROD Dashboard")


## Disclaimer

Using these scripts could result in your account being locked out.

I'm not very good with python, sql, or yaml.

I hope you have a great day.


# LightningROD (Record of Docks)
Lightning Record of Docks (logs) for your F-150 Lightning!

> [!IMPORTANT]
> # THIS IS CURRENTLY NOT WORKING
> I have been busy with other things and haven't had any time to work on this.
> I've also been working on the `fordpass-ha` repo.
> Ford has also release their FordConnect API.
> Hopefully I'll be able to get this back into a working state sometime.
> 


<!-- ## Currently Working On
 - New Grafana dashboard since the move to InfluxDB
 - HomeAssistant automations
 - I am also working on the `fordpass-ha` integration to implement charging sensors, and charging actions (Stop Charging, Start Charging, etc).

## Info
TO be clear. This is nothing special or new. It is how I store, and view, my charge logs.
I am hoping to make it an easy setup for new users and work with others to expand this.

This is my first public repo. I am not a developer. 
This was created in my spare time because I wasn't happy with FordPass Charge Logs. 

I am using it for my F-150 Lightning, but it *should* work with other Ford EV's like the Mustang Mach-E.

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
	- InfluxDB v2.7.3 docker
	- Grafana docker
	- HomeAssistan OS VM
		- HACS integration in HomeAssistant
		- `fordpass-ha` integration by [itchannel](https://github.com/itchannel )

## Goals
- Easy all in one self hosted install
- Easy log and display from HomeAssistant sensors (InfluxDB, Postgresql, etc)
- ProPower Onboard Logs
- "DrivingScore" Logs
- OBD2 Logging Integration (example: Torque for Hass)

## Getting Started

Please check [Currently Working On](https://github.com/SquidBytes/LightningROD#currently-working-on) before installing anything

- Install `InfluxDB v2.7.3` (I used a docker on Unraid)
- Install `Grafana` (I used a docker on Unraid)

Input your details into config.py
```python
# config.py
# Ford Pass Username
fordpass_username = "your_username"
# Ford Pass Password
fordpass_password = "your_password"
fordpass_region = "North America & Canada"
        # "UK&Europe"
        # "Australia"
        # "North America & Canada"
# Vehicle VIN to log
fordpass_vin = 'your_vin'

# InfluxDB info
influx_token = "your_API_token"
influx_org = "org"
influx_url = "API Token"
influx_bucket="lightningrod"

# Cost per kWh
homeCostkWh = 0.104550
workCostkWh = 0.00
otherCostkWh = 0.40

## Can be expanded on, examples
# eaCostkWh = 0.0
# chargepointCostkWh = 0.0
```

## Install

Place:
```python
config.py
auth.py
influx.py
lightningROD.py
```
Into the a directory of your choosing:

## Running
From a terminal, in your directory, run

```python
python3 lightningROD.py
```
a `charge_logs.json` file will be created with your logs, they will also be added to InfluxDB
## Optional Automation

Work in progress

## Grafana
Work in progress since move to InfluxDB

## Screenshot
LightningROD Dashboard
![Alt text](/assets/LightningROD.png?raw=true "LightningROD Dashboard") -->


## Disclaimer

Using these scripts could result in your account being locked out.

I'm not very good with python, sql, or yaml.

I hope you have a great day.


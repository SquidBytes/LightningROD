"""Fordpass API Library"""
import hashlib
import json
import os
import random
import re
import string
import time
from base64 import urlsafe_b64encode
import requests

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

defaultHeaders = {
    "Accept": "*/*",
    "Accept-Language": "en-us",
    "User-Agent": "FordPass/23 CFNetwork/1408.0.4 Darwin/22.5.0",
    "Accept-Encoding": "gzip, deflate, br",
}

apiHeaders = {
    **defaultHeaders,
    "Content-Type": "application/json",
}

region_lookup = {
    "UK&Europe": "1E8C7794-FF5F-49BC-9596-A1E0C86C5B19",
    "Australia": "5C80A6BB-CF0D-4A30-BDBF-FC804B5C1A98",
    "North America & Canada": "71A3AD0A-CF46-4CCF-B473-FC7FE5BC4592",
}

BASE_URL = "https://usapi.cv.ford.com/api"
GUARD_URL = "https://api.mps.ford.com/api"
SSO_URL = "https://sso.ci.ford.com"

session = requests.Session()

class FordPassAuthenticator:
    # Represents a Ford vehicle, with methods for status and issuing commands

    def __init__(
        self, username, password, vin, region, save_token=False, config_location=""
    ):
        self.username = username
        self.password = password
        self.save_token = save_token
        self.region = region_lookup[region]
        self.region2 = region
        self.vin = vin
        self.token = None
        self.expires = None
        self.expires_at = None
        self.refresh_token = None
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        if config_location == "":
            self.token_location = "custom_components/fordpass/fordpass_token.txt"
        else:
            self.token_location = config_location

    def base64_url_encode(self, data):
        """Encode string to base64"""
        return urlsafe_b64encode(data).rstrip(b'=')

    def generate_hash(self, code):
        """Generate hash for login"""
        hashengine = hashlib.sha256()
        hashengine.update(code.encode('utf-8'))
        return self.base64_url_encode(hashengine.digest()).decode('utf-8')

    def auth(self):
        """New Authentication System """
        # Auth Step1
        headers = {
            **defaultHeaders,
            'Content-Type': 'application/json',
        }
        code1 = ''.join(random.choice(string.ascii_lowercase) for i in range(43))
        code_verifier = self.generate_hash(code1)
        url1 = f"{SSO_URL}/v1.0/endpoint/default/authorize?redirect_uri=fordapp://userauthorized&response_type=code&scope=openid&max_age=3600&client_id=9fb503e0-715b-47e8-adfd-ad4b7770f73b&code_challenge={code_verifier}&code_challenge_method=S256"
        response = session.get(
            url1,
            headers=headers,
        )

        test = re.findall('data-ibm-login-url="(.*)"\s', response.text)[0]
        next_url = SSO_URL + test

        # Auth Step2
        headers = {
            **defaultHeaders,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "operation": "verify",
            "login-form-type": "password",
            "username": self.username,
            "password": self.password

        }
        response = session.post(
            next_url,
            headers=headers,
            data=data,
            allow_redirects=False
        )

        if response.status_code == 302:
            next_url = response.headers["Location"]
        else:
            response.raise_for_status()

        # Auth Step3
        headers = {
            **defaultHeaders,
            'Content-Type': 'application/json',
        }

        response = session.get(
            next_url,
            headers=headers,
            allow_redirects=False
        )

        if response.status_code == 302:
            next_url = response.headers["Location"]
            query = requests.utils.urlparse(next_url).query
            params = dict(x.split('=') for x in query.split('&'))
            code = params["code"]
            grant_id = params["grant_id"]
        else:
            response.raise_for_status()

        # Auth Step4
        headers = {
            **defaultHeaders,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "client_id": "9fb503e0-715b-47e8-adfd-ad4b7770f73b",
            "grant_type": "authorization_code",
            "redirect_uri": 'fordapp://userauthorized',
            "grant_id": grant_id,
            "code": code,
            "code_verifier": code1
        }

        response = session.post(
            f"{SSO_URL}/oidc/endpoint/default/token",
            headers=headers,
            data=data

        )

        if response.status_code == 200:
            result = response.json()
            if result["access_token"]:
                access_token = result["access_token"]
        else:
            response.raise_for_status()

        # Auth Step5
        data = {"ciToken": access_token}
        headers = {**apiHeaders, "Application-Id": self.region}
        response = session.post(
            f"{GUARD_URL}/token/v2/cat-with-ci-access-token",
            data=json.dumps(data),
            headers=headers,
        )

        if response.status_code == 200:
            result = response.json()

            self.token = result["access_token"]
            self.refresh_token = result["refresh_token"]
            self.expires_at = time.time() + result["expires_in"]
            if self.save_token:
                result["expiry_date"] = time.time() + result["expires_in"]
                self.write_token(result)
            session.cookies.clear()
            return True
        response.raise_for_status()
        return False

    def refresh_token_func(self, token):
        """Refresh token if still valid"""
        data = {"refresh_token": token["refresh_token"]}
        headers = {**apiHeaders, "Application-Id": self.region}

        response = session.post(
            f"{GUARD_URL}/token/v2/cat-with-refresh-token",
            data=json.dumps(data),
            headers=headers,
        )
        if response.status_code == 200:
            result = response.json()
            if self.save_token:
                result["expiry_date"] = time.time() + result["expires_in"]
                self.write_token(result)
            self.token = result["access_token"]
            self.refresh_token = result["refresh_token"]
            self.expires_at = time.time() + result["expires_in"]
        if response.status_code == 401:
            self.auth()

    def __acquire_token(self):
        # Fetch and refresh token as needed
        # If file exists read in token file and check it's valid
        if self.save_token:
            if os.path.isfile(self.token_location):
                data = self.read_token()
            else:
                data = {}
                data["access_token"] = self.token
                data["refresh_token"] = self.refresh_token
                data["expiry_date"] = self.expires_at
        else:
            data = {}
            data["access_token"] = self.token
            data["refresh_token"] = self.refresh_token
            data["expiry_date"] = self.expires_at
        self.token = data["access_token"]
        self.expires_at = data["expiry_date"]
        if self.expires_at:
            if time.time() >= self.expires_at:
                self.refresh_token_func(data)
                # self.auth()
        if self.token is None:
            # No existing token exists so refreshing library
            self.auth()

    def write_token(self, token):
        """Save token to file for reuse"""
        with open(self.token_location, "w", encoding="utf-8") as outfile:
            token["expiry_date"] = time.time() + token["expires_in"]
            json.dump(token, outfile)

    def read_token(self):
        """Read saved token from file"""
        try:
            with open(self.token_location, encoding="utf-8") as token_file:
                token = json.load(token_file)
                return token
        except ValueError:
            self.auth()
            with open(self.token_location, encoding="utf-8") as token_file:
                token = json.load(token_file)
                return token


    def vehicles(self):
        """Get vehicle list from account"""
        self.__acquire_token()

        if self.region2 == "Australia":
            countryheader = "AUS"
        elif self.region2 == "North America & Canada":
            countryheader = "USA"
        elif self.region2 == "UK&Europe":
            countryheader = "GBR"
        else:
            countryheader = "USA"
        headers = {
            **apiHeaders,
            "Auth-Token": self.token,
            "Application-Id": self.region,
            "Countrycode": countryheader,
            "Locale": "EN-US"
        }

        data = {
            "dashboardRefreshRequest": "All"
        }
        response = session.post(
            f"{GUARD_URL}/expdashboard/v1/details/",
            headers=headers,
            data=json.dumps(data)
        )
        if response.status_code == 207:
            result = response.json()

            return result
        response.raise_for_status()
        return None


    def charge_log(self):
        """Get Charge logs from account"""
        self.__acquire_token()

        headers = {
            **apiHeaders,
            "Auth-Token": self.token,
            "Application-Id": self.region
        }

        response = session.get(
            f"{GUARD_URL}/electrification/experiences/v1/devices/{self.vin}/energy-transfer-logs/",
            headers=headers)
        
        if response.status_code == 200:
            result = response.json()
            return result["energyTransferLogs"]

        response.raise_for_status()
        return None
    

class FordPassChargeLogsDownloader:
    def __init__(self, vehicle, save_token=False):
        self.vehicle = vehicle
        self.save_token = save_token

    def download_charge_logs(self):
        my_vehicle = self.vehicle
        log_data = my_vehicle.charge_log()

        try:
            # Load the existing JSON data if the file exists
            with open("charge_logs.json", "r") as file:
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
        with open("charge_logs.json", "w") as file:
            json.dump(existing_data, file, indent=4)


if __name__ == '__main__':
    fp_username = 'xryanm@gmail.com'
    fp_password = 'sVLZf#^^opThY4k!%ka'
    fp_vin = '1FTVW1EL6PWG05841'
    fp_region = 'North America & Canada'
    fp_token = 'xryanm@gmail.com_fordpass_token.txt'

    auth = FordPassAuthenticator(fp_username, fp_password, fp_vin, fp_region)
    logs = FordPassChargeLogsDownloader(auth)
    auth.auth()
    logs.download_charge_logs()

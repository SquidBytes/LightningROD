"""Fordpass API Library"""
import hashlib
import json
import os
import pathlib
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

loginHeaders = {
    "Accept": "*/*",
    "Accept-Language": "en-us",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
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

locale_lookup = {
    "UK&Europe": "EN-GB",
    "Australia": "EN-AU",
    "North America & Canada": "EN-US",
}

locale_short_lookup = {
    "UK&Europe": "GB",
    "Australia": "AUS",
    "North America & Canada": "USA",
}

BASE_URL = "https://usapi.cv.ford.com/api"
GUARD_URL = "https://api.mps.ford.com/api"
SSO_URL = "https://sso.ci.ford.com"
FORD_LOGIN_URL = "https://login.ford.com"

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
        self.country_code = locale_lookup[region]
        self.short_code = locale_short_lookup[region]
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

        # Run Step 1 auth
        access_tokens = self.auth2_step1()

        if access_tokens is None:
            self.errors += 1
            if self.errors <= 10:
                self.auth()
            else:
                raise Exception("Step 1 has reached error limit")

        # Run Step 5 auth
        #success = self.auth_step5(access_tokens)
        success = self.auth2_step2(access_tokens)
        if success is False:
            self.errors += 1
            if self.errors <= 10:
                self.auth()
            else:
                raise Exception("Step 2 has reached error limit")
        else:
            self.errors = 0
            return True
        return False

    def auth2_step1(self):
        """Auth2 step 1 obtain tokens"""
        headers = {
            **loginHeaders,
        }
        code1 = ''.join(random.choice(string.ascii_lowercase) for i in range(43))
        code_verifier = self.generate_hash(code1)
        step1_session = requests.session()
        step1_url = f"{FORD_LOGIN_URL}/4566605f-43a7-400a-946e-89cc9fdb0bd7/B2C_1A_SignInSignUp_{self.country_code}/oauth2/v2.0/authorize?redirect_uri=fordapp://userauthorized&response_type=code&max_age=3600&scope=%2009852200-05fd-41f6-8c21-d36d3497dc64%20openid&client_id=09852200-05fd-41f6-8c21-d36d3497dc64&code_challenge={code_verifier}&code_challenge_method=S256&ui_locales={self.country_code}&language_code={self.country_code}&country_code={self.short_code}&ford_application_id=5C80A6BB-CF0D-4A30-BDBF-FC804B5C1A98"

        step1get = step1_session.get(
            step1_url,
            headers=headers,
        )

        step1get.raise_for_status()

        #_LOGGER.debug(step1_session.text)
        pattern = r'var SETTINGS = (\{[^;]*\});'
        #_LOGGER.debug(step1get.text)
        match = re.search(pattern, step1get.text)
        transId = None
        csrfToken = None
        if match:
            settings = match.group(1)
            settings_json = json.loads(settings)
            transId = settings_json["transId"]
            csrfToken = settings_json["csrf"]
        data = {
            "request_type": "RESPONSE",
            "signInName": self.username,
            "password": self.password,
        }
        urlp = f"{FORD_LOGIN_URL}/4566605f-43a7-400a-946e-89cc9fdb0bd7/B2C_1A_SignInSignUp_{self.country_code}/SelfAsserted?tx={transId}&p=B2C_1A_SignInSignUp_en-AU"
        headers = {
            **loginHeaders,
            "Origin": "https://login.ford.com",
            "Referer": step1_url,
            "X-Csrf-Token": csrfToken
        }
        step1post = step1_session.post(
            urlp,
            headers=headers,
            data=data
        )
        step1post.raise_for_status()
        cookie_dict = step1_session.cookies.get_dict()


        step1pt2 = step1_session.get(
            f"{FORD_LOGIN_URL}/4566605f-43a7-400a-946e-89cc9fdb0bd7/B2C_1A_SignInSignUp_{self.country_code}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrfToken}",
            headers=headers,
            allow_redirects=False,
        )
        step1pt2.raise_for_status()

        test = step1pt2.headers["Location"]

        code_new = test.replace("fordapp://userauthorized/?code=","")

        data = {
            "client_id" : "09852200-05fd-41f6-8c21-d36d3497dc64",
            "grant_type": "authorization_code",
            "code_verifier": code1,
            "code": code_new,
            "redirect_uri": "fordapp://userauthorized"

        }

        step1pt3 = step1_session.post(
            f"{FORD_LOGIN_URL}/4566605f-43a7-400a-946e-89cc9fdb0bd7/B2C_1A_SignInSignUp_{self.country_code}/oauth2/v2.0/token",
            headers=headers,
            data=data
        )
        step1pt3.raise_for_status()

        tokens = step1pt3.json()
        if tokens:
            if self.auth2_step2(tokens):
                return tokens
        else:
            print('wrong')

    def auth2_step2(self, result):

        data = {"idpToken": result["access_token"]}
        headers = {**apiHeaders, "Application-Id": self.region}
        response = session.post(
            f"{GUARD_URL}/token/v2/cat-with-b2c-access-token",
            data=json.dumps(data),
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()
        self.token = result["access_token"]
        self.refresh_token = result["refresh_token"]
        self.expires_at = time.time() + result["expires_in"]
        if self.save_token:
            result["expiry_date"] = time.time() + result["expires_in"]

            self.write_token(result)
        session.cookies.clear()
        return True

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
            self.auth2_step1()

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
    def __init__(self, vehicle, log_location="", save_token=False):
        self.vehicle = vehicle
        self.save_token = save_token
        if log_location == "":
            lightningRDir = pathlib.Path(__file__).parent.resolve()
            lightningRLogs = os.path.join(lightningRDir, "charge_logs.json")
            self.log_location = lightningRLogs
        else:
            self.log_location = log_location
            

    def download_charge_logs(self):
        my_vehicle = self.vehicle
        log_data = my_vehicle.charge_log()

        try:
            # Load the existing JSON data if the file exists
            with open(self.log_location, "r") as file:
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
        with open(self.log_location, "w") as file:
            json.dump(existing_data, file, indent=4)


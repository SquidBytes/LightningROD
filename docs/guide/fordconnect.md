# FordConnect

Read your vehicle's data straight from Ford's API, without Home Assistant. Setup takes
about 15 minutes, plus waiting time on Ford's side.

You never enter your Ford password into LightningROD.

---

## 1. Create a Ford developer account

1. Go to <https://developer.ford.com/>, select **Sign in → Register**, and follow the
   steps. Two-factor authentication is required.
2. Wait until the account is **verified and active**. This can take a few days.
3. Make sure your profile name uses only letters, numbers and spaces. Other characters
   break the registration form later.

## 2. Register an application

1. Open the portal for your region (only North America has been tested):
   - North America: <https://developer.ford.com/developer-na>
   - European Union: <https://developer.ford.com/developer-eu>
   - United Kingdom: <https://developer.ford.com/developer-uk>
2. Open [FordConnect Query](https://developer.ford.com/apis/fordconnect-query) and
   select **Get Started**.
3. Tick **"This app is for personal use"**.
4. Fill in the name, description and purpose using letters, numbers, spaces and
   underscores only. For example: `LightningROD`, `Self-hosted vehicle analytics`,
   `Stats for my vehicle`.
5. Tick **all** vehicle types.
6. Under **Authorization Code → redirect URIs**, paste the address from LightningROD:
   open **Settings → Data Sources → FordConnect** and click **Copy**. It looks like
   `http://localhost:8000/auth/ford/callback`.
   - The port is LightningROD's **web** port, the one in your browser's address bar.
   - It must match exactly: no trailing slash, no `https`.
   - Ford rejects private addresses such as `192.168.x.x`, which is why it uses
     `localhost`.
   - Already registered an app for another LightningROD install? Reuse it: put that
     app's registered address in LightningROD's redirect field instead. The port
     doesn't need to match this install.
7. Tick **All Available Data Categories**, complete the captcha and submit.
8. Copy the **Client ID** and **Client Secret**. The secret is shown **only once**.

!!! note "Wait a couple of hours"
    New credentials take time to go live at Ford. Until then, connecting fails with
    `404 credential not found`.

## 3. Connect the vehicle

1. In **Settings → Data Sources → FordConnect**, paste the Client ID and Client Secret
   and save.
2. Click **Connect Vehicle**, open the Ford link it shows, sign in and pick your
   vehicle. One connection covers one vehicle.
3. Ford then sends you back to LightningROD:
   - **Browser on the same machine as LightningROD:** it says connected. You're done.
   - **Browser on another device:** you land on an error page ("can't reach this
     site"). This is expected. Copy the **whole** address from the address bar and
     paste it into **Address after sign-in** in LightningROD, then click **Finish**.

Do this within **5 minutes**. The code in that address expires after that; if it does,
click **Connect Vehicle** again.

Data starts arriving within a minute.

---

## Troubleshooting

| Message or symptom | Fix |
|---|---|
| `404 credential not found` | Credentials not live yet, or registered on the wrong regional portal. Wait, then check the portal. |
| "AADB2C90006: The redirect URI … is not registered" | LightningROD's redirect field doesn't match your Ford app. Paste the exact registered address into it, save, and connect again. |
| No Client ID / Secret after submitting | Ford rejected the redirect URI. Re-copy it from LightningROD: no trailing slash, no private IP. |
| "That's the sign-in link, not the address you landed on" | Copy the address you ended up on **after** approving, not the Ford link. |
| "That code was already used" or "code expired" | Click **Connect Vehicle** again. |
| "Ford doesn't recognise your Client ID yet" | Credentials still going live. Wait a couple of hours. |
| "Your Client Secret doesn't match" | Re-check both values. If the secret is lost, generate a new one in the Ford portal. |
| "…encryption key is missing or changed" | Restore the `LR_SECRET_KEY` you used before, or re-enter the credentials. |
| Connected, but no new data for hours | Normal while parked: the vehicle stops reporting. The card shows the age of the newest reading. |
| "Re-authorization required" | Ford's permission lasts 90 days. Click **Connect Vehicle** again. Your history is kept. |

---

## Good to know

- **No history before you connect.** Ford's API only reports the vehicle's current
  state.
- **No driving scores or cabin temperature.** Ford's API doesn't provide them. If you
  also run Home Assistant, the same drive or charge from both sources is combined into
  one record.
- **Charging locations arrive as coordinates.** Name them once in the review queue.
  Locations with a saved charge schedule in the vehicle arrive with that name.
- **Updates come about once a minute while driving or charging**, and every 5 minutes
  when parked.
- **Credentials are stored in your database.** Set `LR_SECRET_KEY` to encrypt them;
  see [Configuration](../getting-started/configuration.md).

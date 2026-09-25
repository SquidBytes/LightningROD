# FordConnect

Connect your Ford vehicle directly through Ford's official API, without Home Assistant.

---

## What you are setting up

LightningROD reads your vehicle's data from Ford's official API. To do that it needs
Ford's permission — and Ford grants permission to *applications*, not to people.

So there are two steps: you register an application with Ford (once, ~10 minutes),
then you tell that application it may read your vehicle (once, ~1 minute).

You will never type your Ford password into LightningROD. That is the entire point of
what follows.

---

## How this works (skip if you don't care)

Three parties: **you**, **Ford**, and **LightningROD**.

Ford doesn't hand out application credentials to projects like LightningROD, so **you
register your own application** with Ford and paste its credentials into LightningROD.
Think of the Client ID and Client Secret as a badge that says "this software is allowed
to ask Ford for data." The badge alone grants nothing — it just identifies the asker.

Then comes the permission step:

```
1. LightningROD builds a Ford sign-in link and shows it to you
2. You open the link and sign in --- at ford.com, not in LightningROD
3. Ford asks: "allow this application to read this vehicle?"  You pick a vehicle
4. Ford sends your browser to a "redirect" address, with a one-time code attached
5. LightningROD trades that code (plus its secret) for an access token
6. From then on, LightningROD uses the token to read your data
```

Your password only ever touches step 2, on Ford's own site. The one-time code in step 4
is useless without the Client Secret, which never leaves your server.

**The redirect address** in step 4 is just "where should Ford send the browser when
it's done". Ford insists you register it in advance, so that nobody can trick Ford into
sending *your* code to *their* server.

---

## Why there's an awkward step

Ford only accepts two kinds of redirect address:

- `localhost` — the machine the browser is running on
- a public internet address

It **rejects** private network addresses like `http://192.168.1.50:8000` or
`http://tower.local` at registration time.

That's a problem, because that is exactly where LightningROD lives. And LightningROD
ships with no login and no HTTPS on purpose — it is a self-hosted tool, and we
don't want to encourage anyone to expose it to the internet.

So we use `localhost`. Which works perfectly — **if your browser is on the same machine
as LightningROD.** If it isn't, Ford sends your browser to *your laptop's* localhost,
where nothing is listening, and you get an error page.

That error page is not a failure. **The code you need is in the address bar.** You copy
it over by hand. That's the whole workaround, and it takes about five seconds.

---

## Part 1 — Register an application with Ford

1. Create a Ford developer account at <https://developer.ford.com/> and make sure it is
   **verified and active**. MFA is required.
2. Check your profile name contains only letters, numbers and spaces. Special
   characters break the registration form later, with an unhelpful error.
3. Open the portal for your region — this link matters, using the wrong one wastes a
   registration:
   - North America: <https://developer.ford.com/developer-na>
   - European Union: <https://developer.ford.com/developer-eu>
   - United Kingdom: <https://developer.ford.com/developer-uk>
4. Tick **"Yes — this app is for personal use."**
5. Application name, description and purpose: stick to letters, numbers, spaces and
   underscores.
6. Tick **all** vehicle types.
7. Under **Authorization Code → redirect URIs**, enter the address LightningROD shows
   you.

   > **Get this from LightningROD, don't type it from memory.** Open
   > **Settings → Data Sources → FordConnect** in another tab. It displays the exact
   > address to register, with a **Copy** button, worked out from the address you're
   > using to reach LightningROD right now:
   >
   > ```
   > http://localhost:8000/auth/ford/callback          [ Copy ]
   > ```
   >
   > The number is the **web port** — the one in your browser's address bar when you
   > use LightningROD. Not your database port. If LightningROD's guess looks wrong
   > (you're behind a reverse proxy, or you mapped an unusual Docker port), edit the
   > field on that screen first, then copy it.

   It must match character for character — no trailing slash, no `https`.
8. Tick **All Available Data Categories**, complete the captcha, and submit.
9. Copy the **Client ID** and **Client Secret**. *The secret is shown once.* Save it
   somewhere before leaving the page.

> **New credentials do not work instantly.** Ford deploys them across its systems on a
> delay. Wait a couple of hours before the next part. If you see
> `404 credential not found`, the application hasn't finished deploying — wait longer,
> and re-check that you used the right regional portal in step 3.

> Ford suggests creating a **separate FordPass account** and inviting it as a driver on
> your vehicle, so that API activity can't interfere with your main account.

---

## Part 2 — Connect the vehicle

1. In LightningROD, open **Settings → Data Sources → FordConnect**.
2. Paste your **Client ID** and **Client Secret**. Save.
3. Click **Connect Vehicle**. LightningROD shows a Ford sign-in link.
4. Open that link, sign in to Ford, and pick the vehicle you want.

   **One connection = one vehicle.** For a second vehicle, run **Connect Vehicle**
   again after this finishes.

5. What you see next depends on where your browser is:

   **Browser on the same machine as LightningROD** — you land back in LightningROD and
   it says connected. You're done; skip step 6.

   **Browser anywhere else** — you get a browser error page: "can't reach this site",
   "connection refused", or similar. **This is expected.** Look at the address bar:

   ```
   http://localhost:8000/auth/ford/callback?code=eyJhbGciOi...&state=a3f9c2e81b4d7e02
   ```

   **Copy the whole thing**, from `http://` to the very end. Not just the `code=` part.
   LightningROD checks what you paste and tells you what's wrong if it isn't right.

6. Select the **entire** address, copy it, and paste it into the **"Paste the address
   here"** box in LightningROD. Click **Finish**.

Done. LightningROD stores an access token and starts reading data within a minute.

> **Do it promptly.** The code expires about **five minutes** after Ford issues it. If
> you get "code expired", just click **Connect Vehicle** again — nothing is broken.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `404 credential not found` | Credentials haven't finished deploying at Ford, or you registered on the wrong regional portal. Wait, then re-check the portal |
| Ford never showed a Client ID / Secret | The registration form rejected your redirect URI. Re-check step 7 — no trailing slash, no private IP like `192.168.x.x` |
| "That's the sign-in link, not the address you landed on" | You copied the Ford link LightningROD gave you, instead of the address you ended up at *after* approving |
| "That code was already used" | Each code works once. Click **Connect Vehicle** for a fresh one |
| "Ford doesn't recognise your Client ID yet" | Credentials still deploying at Ford — wait a couple of hours |
| "Your Client Secret doesn't match" | Re-check both values. The secret is shown only once at registration; if you lost it, generate a new one |
| "code expired" in LightningROD | Took longer than ~5 minutes. Click **Connect Vehicle** again |
| Connected, but no data for hours | Normal. A parked vehicle stops reporting; readings can be **6+ hours old**. The Data Sources page shows the age of the newest reading |
| "Re-authorization required" after ~3 months | Ford's permission lasts **90 days**. Click **Connect Vehicle** again. Your history is kept |

### What LightningROD stores

Your Client ID, Client Secret and Ford tokens are stored in your own database, on
your own server, and are never sent anywhere except Ford. They are encrypted there
when `LR_SECRET_KEY` is set — see [Configuration](../getting-started/configuration.md).

---

## Known limits of the Ford connection

Compared to the Home Assistant data source:

- **Charging history starts the day you connect.** Ford's API has no way to look
  backwards. Existing history from another source is kept, and you can import a CSV.
- **Charging locations arrive as coordinates**, not names. Name them once in the review
  queue and LightningROD remembers. Locations you've saved a charge schedule for come
  through with your own names already attached.
- **No driving scores and no cabin temperature.** Ford's API doesn't publish them.
- **Readings update roughly every minute while driving or charging**, and can go quiet
  for hours when parked. That is the vehicle, not LightningROD — polling more often
  returns the same values and risks hitting Ford's rate limit.

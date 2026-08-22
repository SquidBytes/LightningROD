# Review Queue

The Review Queue (`/review`) is where you check the networks and locations LightningROD has collected. These may come from Home Assistant, a CSV import, or manual entry. It lives under **System** in the sidebar, next to Settings.

!!! tip "See it live"
    Explore this page in the [interactive demo](https://lightningrod.dev/demo).

## Why a Review Queue?

LightningROD creates network and location records as it ingests data. A new charger in your HA history, an unfamiliar location from `ha-fordpass`, or a CSV row with a new network will all show up here as **unverified** until you review them.

Verified entries are the ones LightningROD will match against later, use for cost calculations, and show first in pickers. Unverified entries still work, but this page helps you clean them up before they pile up.

## Layout

The page has two top-level tabs:

| Tab | Contents |
|-----|----------|
| **Pending** (badge shows count) | Unverified networks and locations that need attention |
| **Approved** | Read-only tree of everything that's already verified |

The Pending tab has two sub-tabs:

| Sub-tab | Shows |
|---------|-------|
| **Networks** | Unverified `EVChargingNetwork` rows |
| **Locations** | Unverified `EVLocationLookup` rows |

Each sub-tab has its own count badge. The top-level Pending badge adds them together.

## Pending → Networks

Each row shows the network name, color badge, session count, and source (`manual`, `import`, `hass`, etc.). Per-row actions:

| Action | What it does |
|--------|--------------|
| **Verify** | Marks the network verified and moves it to the Approved tree. |
| **Edit** | Opens the same network editor used in Settings → Networks. |
| **Merge** | Choose another network to combine it with, with a preview of what will move. |
| **Delete** | Removes the network. Sessions previously pointing at it become network-less. |

Use Merge when you have near-duplicates like `ChargePoint`, `chargepoint`, and `Charge Point`. Pick the main row as the target and fold the others into it.

## Pending → Locations

Locations are the more common pending case, since each new charger position can create a new location row. Each row shows the location name, address or coordinates, the linked network, and session count. Per-row actions:

| Action | What it does |
|--------|--------------|
| **Verify** | Marks the location verified. It does not need a network first. |
| **Edit** | Opens the full edit form. |
| **Associate** | Sets the network only, without changing verification status. |
| **Promote** | Creates a new network from the location name, links them, and verifies both. |
| **Merge** | Combines the location with another one after showing the impact. |
| **Delete** | Removes the location. |

Once a location is verified, its name, type and network are what new charging
sessions record — not the name your vehicle reports. Renaming a location in the
vehicle will not retag sessions that match a location you have already approved.

### Verify vs. Associate vs. Promote

These three actions overlap, and the right choice depends on what you are modeling:

- **Verify** — the data is already correct. Keep the current network or leave it blank.
- **Associate** — the data is right, but the network is wrong or missing. Pick the right network without marking it verified.
- **Promote** — the location is the network. One click creates the network, links them, and verifies both.

## Approved Tab

A read-only tree of verified networks with their verified locations nested underneath. Click a network row to expand or collapse its locations. Locations without a network are grouped under a **No Network** node at the bottom.

This view is the reference list LightningROD uses for matching later imports and for showing the first choices in session pickers. You can still open the network or location editor here when you need to make changes.

## Search, Filter, Sort

Each Pending sub-tab has a search box and a sort dropdown (by name or session count). The default filter on Pending tabs is **unverified**. Switch to verified or all from Settings → Networks.

## Recommended workflow

1. After your first HA backfill or CSV import, open Review Queue. Pending will usually be high.
2. Work the **Networks** sub-tab first. Verify the obvious ones, merge duplicates, and delete junk.
3. Switch to **Locations**. Verify the ones already linked to the right network. For network-less rows, choose Associate for an existing network or Promote for a new one.
4. Check the Pending badge now and then. It will grow whenever you charge somewhere new.

!!! tip "Group editing sessions vs. cleaning up reference data"
    Review Queue is for the reference data itself. To move existing sessions to different networks or locations, use Group Edit on the [Charging Sessions](sessions.md#group-edit) page.

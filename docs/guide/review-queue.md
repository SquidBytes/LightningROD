# Review Queue

The Review Queue (`/review`) is where you verify, edit, merge, and clean up the **networks and locations** that LightningROD has collected — whether they were created automatically from Home Assistant ingestion, brought in by a CSV import, or added manually. It lives under **System** in the sidebar, next to Settings.

![review-queue](../assets/images/lr_review_queue.gif)

## Why a Review Queue?

LightningROD auto-creates network and location records as it ingests data. A new charger that shows up in your HA history, an unfamiliar location reported by `ha-fordpass`, a CSV row referencing a network you've never used — each of these spawns a row that is marked **unverified** until you confirm it.

Verification is what tells the app "yes, this is a real network/location, treat it as canonical." Verified entries are what auto-detection matches against on future ingests, what cost calculations consider stable, and what you'll see surfaced in pickers across the app. Unverified entries still work, but they're flagged here so you can clean them up before they accumulate.

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

Each sub-tab has its own count badge. The top-level Pending badge sums both.

## Pending → Networks

Each row shows the network name, color badge, session count, and source (where the row came from — `manual`, `import`, `hass`, etc.). Per-row actions:

| Action | What it does |
|--------|--------------|
| **Verify** | Marks the network verified and moves it to the Approved tree. |
| **Edit** | Opens the same network edit modal used in Settings → Networks (details, locations, subscription tabs). |
| **Merge** | Pick another network as the merge target; previews how many sessions and locations will be re-pointed before you commit. |
| **Delete** | Removes the network. Sessions previously pointing at it become network-less. |

Use Merge when ingestion has produced near-duplicates ("ChargePoint" / "chargepoint" / "Charge Point") — pick the canonical row as the target, and the others fold into it.

## Pending → Locations

Locations are the more common pending case, since each new charger position can spawn a new location row. Each row shows location name, address/coords, the network it's linked to (or "No Network"), and session count. Per-row actions:

| Action | What it does |
|--------|--------------|
| **Verify** | Marks the location verified. Does *not* require a network to be set first. |
| **Edit** | Full edit form (name, type, address, coords, cost override, notes, network). |
| **Associate** | Lightweight network picker — sets `network_id` only, without changing verified state. Use this when you just want to attach an orphan location to a network and keep reviewing. |
| **Promote** | Creates a brand-new network whose name matches the location, links the location to it, and verifies both in one shot. Use this for "this place is its own thing" — e.g., your home, a workplace, a hotel L2 that isn't part of a public network. |
| **Merge** | Same as networks: pick a target location, preview affected sessions, commit. |
| **Delete** | Removes the location. |

### Verify vs. Associate vs. Promote

These three actions overlap and the right pick depends on what you're modeling:

- **Verify** — the data is correct as-is. Keep the network it's already linked to (or leave it network-less).
- **Associate** — the data is right but the network field is wrong or empty. Pick the right network. Don't claim it's verified yet.
- **Promote** — this location *is* the network. One click creates the network, links, and verifies.

## Approved Tab

A read-only tree of verified networks with their verified locations nested underneath. Click a network row to expand/collapse its locations. Locations that have no network are grouped under a synthetic **No Network** node at the bottom of the tree.

This view is the canonical reference data — what auto-detection on future ingestion will match against, and what session pickers display first. You can still launch the network or location edit modal from here when you need to change something, but day-to-day the Approved tab is just a "what does the app know about?" overview.

## Search, Filter, Sort

Each Pending sub-tab has a search box (matches name) and a sort dropdown (by name or session count). The default filter on Pending tabs is **unverified** — switching to verified or all is done from Settings → Networks, not the Review Queue, which is intentionally focused on cleanup.

## Recommended workflow

1. After your first HA backfill or CSV import, open Review Queue. Pending will be high.
2. Work the **Networks** sub-tab first — verify the obvious ones, merge near-duplicates, delete junk.
3. Switch to **Locations**. Verify the ones that are already pointed at the right network. For network-less rows, decide between Associate (existing network) or Promote (new dedicated network).
4. Keep an eye on the Pending badge over time — every time you charge somewhere new, expect a small bump.

!!! tip "Group editing sessions vs. cleaning up reference data"
    Review Queue is for the underlying **reference data** (networks and locations themselves). To re-point existing sessions onto different networks/locations, use the bulk Group Edit on the [Charging Sessions](sessions.md#group-edit) page.

"""Manual trigger CLI for ad-hoc event injection into the HA simulator.

Usage::

    # Auto mode: start simulator, inject 10 random events, stop
    python -m tests.test_ha_sim.trigger --auto

    # Inject specific events into a running simulator
    python -m tests.test_ha_sim.trigger --host localhost --port 8765 charge
    python -m tests.test_ha_sim.trigger --host localhost --port 8765 trip
    python -m tests.test_ha_sim.trigger --host localhost --port 8765 battery
    python -m tests.test_ha_sim.trigger --host localhost --port 8765 gps
    python -m tests.test_ha_sim.trigger --host localhost --port 8765 random
"""

import argparse
import asyncio
import json
import random
import sys

import websockets

from tests.test_ha_sim.simulator import (
    HASimulator,
    make_battery_event,
    make_charging_session_event,
    make_gps_event,
    make_temperature_event,
    make_trip_event,
)

# Simple command protocol for sending trigger commands to the simulator
# over a raw WebSocket connection. The simulator doesn't natively support
# "inject" commands over its HA protocol, so in manual mode we connect
# as a normal client, authenticate, and then the trigger sends events
# by directly calling inject_event on the simulator object (auto mode)
# or by connecting to an auxiliary control socket.

_EVENT_GENERATORS = {
    "charge": lambda: make_charging_session_event(
        energy_kwh=round(random.uniform(5.0, 60.0), 1),
        charge_type=random.choice(["DC_FAST", "AC_LEVEL_2", "AC_BASIC"]),
        network_name=random.choice(
            ["Electrify America", "ChargePoint", "EVgo", "Tesla Supercharger"]
        ),
        start_soc=round(random.uniform(5.0, 40.0), 0),
        end_soc=round(random.uniform(60.0, 100.0), 0),
    ),
    "trip": lambda: make_trip_event(
        distance_miles=round(random.uniform(2.0, 80.0), 1),
        duration_minutes=round(random.uniform(5.0, 120.0), 0),
        efficiency=round(random.uniform(2.0, 4.5), 1),
    ),
    "battery": lambda: make_battery_event(
        soc=round(random.uniform(10.0, 100.0), 0),
        battery_range_miles=round(random.uniform(30.0, 280.0), 0),
    ),
    "gps": lambda: make_gps_event(
        lat=round(random.uniform(25.0, 48.0), 6),
        lon=round(random.uniform(-124.0, -70.0), 6),
    ),
    "temperature": lambda: make_temperature_event(
        temp_f=round(random.uniform(20.0, 110.0), 1),
    ),
}


def _random_event() -> tuple[str, dict]:
    """Generate a random event of any type."""
    event_type = random.choice(list(_EVENT_GENERATORS.keys()))
    return _EVENT_GENERATORS[event_type]()


async def _run_auto_mode() -> None:
    """Start a simulator, run 10 random events, then stop."""
    sim = HASimulator()
    await sim.start()
    print(f"Simulator started on {sim.ws_url}")

    events = [_random_event() for _ in range(10)]
    print(f"Injecting {len(events)} random events...")

    for i, (entity_id, new_state) in enumerate(events, 1):
        await sim.inject_event(entity_id, new_state)
        slug = entity_id.split("_", 2)[-1] if "_" in entity_id else entity_id
        print(f"  [{i}/10] {slug}")
        await asyncio.sleep(0.3)

    # Brief pause for dispatch
    await asyncio.sleep(0.5)
    await sim.stop()
    print("Done. Simulator stopped.")


async def _inject_to_running(host: str, port: int, event_type: str) -> None:
    """Connect to a running simulator and inject an event.

    Connects as a normal HA client, authenticates, subscribes, then
    prints any events received. The event must be injected externally
    (e.g., via another process that has access to the simulator object).

    For CLI use, this primarily serves as a connectivity test.
    """
    uri = f"ws://{host}:{port}"
    print(f"Connecting to {uri}...")

    async with websockets.connect(uri) as ws:
        # Auth handshake
        auth_required = json.loads(await ws.recv())
        print(f"  Received: {auth_required['type']}")

        await ws.send(json.dumps({
            "type": "auth",
            "access_token": "test-token-valid",
        }))
        auth_resp = json.loads(await ws.recv())
        print(f"  Auth: {auth_resp['type']}")

        if auth_resp["type"] != "auth_ok":
            print("Authentication failed!")
            return

        # Generate the event to display what would be injected
        if event_type == "random":
            entity_id, new_state = _random_event()
        elif event_type in _EVENT_GENERATORS:
            entity_id, new_state = _EVENT_GENERATORS[event_type]()
        else:
            print(f"Unknown event type: {event_type}")
            return

        slug = entity_id.split("_", 2)[-1] if "_" in entity_id else entity_id
        print(f"  Event: {slug}")
        print(f"  Entity: {entity_id}")
        print(f"  State keys: {list(new_state.get('attributes', {}).keys())}")
        print("Connected and authenticated. Event shown above would need to be")
        print("injected via the simulator object (e.g., sim.inject_event()).")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="HA Simulator manual trigger for ad-hoc event injection",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Simulator host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Simulator port (default: 8765)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Start simulator, inject 10 random events, then stop",
    )
    parser.add_argument(
        "event_type",
        nargs="?",
        choices=["charge", "trip", "battery", "gps", "temperature", "random"],
        help="Type of event to inject",
    )

    args = parser.parse_args()

    if args.auto:
        asyncio.run(_run_auto_mode())
    elif args.event_type:
        asyncio.run(_inject_to_running(args.host, args.port, args.event_type))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

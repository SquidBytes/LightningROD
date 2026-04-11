"""Generate correlated trip sample CSV from charging sessions.

Analyzes gaps between charging sessions to derive realistic trips.
Trips happen BETWEEN charges — SOC drops indicate driving happened.

Usage:
    cd app-public && python scripts/generate_trip_csv.py
    Output: data/trip_metrics_sample.csv
"""

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)  # Reproducible

EST = timezone(timedelta(hours=-5))
EDT = timezone(timedelta(hours=-4))

# Baltimore seasonal temps (°F) - monthly averages [low, high]
MONTHLY_TEMPS = {
    1: (28, 42), 2: (30, 45), 3: (37, 55), 4: (46, 65), 5: (56, 75),
    6: (65, 84), 7: (70, 89), 8: (69, 87), 9: (62, 80), 10: (49, 68),
    11: (39, 57), 12: (31, 46),
}


def seasonal_temp(dt: datetime) -> float:
    """Realistic ambient temp for Baltimore area."""
    low, high = MONTHLY_TEMPS[dt.month]
    hour = dt.hour
    # Cooler in morning/evening, warmer midday
    if 6 <= hour <= 9:
        base = low + (high - low) * 0.2
    elif 10 <= hour <= 15:
        base = low + (high - low) * 0.7
    elif 16 <= hour <= 19:
        base = low + (high - low) * 0.5
    else:
        base = low + (high - low) * 0.1
    return round(base + random.uniform(-5, 5), 1)


def efficiency_for_temp(temp_f: float, trip_type: str) -> float:
    """F-150 Lightning efficiency varies heavily with temperature.

    Cold weather = cabin heating = lower efficiency.
    Highway = slightly lower than city due to aero on a truck.
    """
    # Base efficiency curve by temp
    if temp_f < 20:
        base = 1.4
    elif temp_f < 32:
        base = 1.7
    elif temp_f < 45:
        base = 2.0
    elif temp_f < 60:
        base = 2.3
    elif temp_f < 75:
        base = 2.5
    else:
        base = 2.6

    # Trip type adjustments
    if trip_type == "highway":
        base *= 0.85  # Highway speeds hurt truck aero
    elif trip_type == "commute":
        base *= 1.0
    elif trip_type == "errand":
        base *= 1.05  # Short, slower trips

    return round(base + random.uniform(-0.2, 0.2), 2)


def driving_scores(trip_type: str) -> dict:
    """Generate correlated driving scores."""
    if trip_type == "commute":
        # Regular commute, consistent driving
        overall = random.randint(72, 92)
        speed = random.randint(70, 95)
        accel = random.randint(65, 90)
        decel = random.randint(70, 95)
    elif trip_type == "highway":
        # Highway: high speed score, moderate accel/decel
        overall = random.randint(65, 85)
        speed = random.randint(55, 80)  # Highway speed penalizes
        accel = random.randint(70, 90)
        decel = random.randint(75, 95)
    else:  # errand
        overall = random.randint(75, 95)
        speed = random.randint(80, 98)
        accel = random.randint(60, 88)
        decel = random.randint(65, 90)
    return {
        "driving_score": overall,
        "speed_score": speed,
        "acceleration_score": accel,
        "deceleration_score": decel,
    }


def load_sessions(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def derive_trips(sessions: list[dict]) -> list[dict]:
    """Derive trips from gaps between charging sessions.

    Logic: When SOC drops between end of one session and start of next,
    driving happened. The miles_added on the next charge approximates
    the trip distance.
    """
    trips = []

    # Sort sessions by start time
    sorted_sessions = sorted(sessions, key=lambda s: s["session_start_utc"])

    for i in range(1, len(sorted_sessions)):
        prev = sorted_sessions[i - 1]
        curr = sorted_sessions[i]

        prev_end_ts = parse_ts(prev["session_end_utc"])
        curr_start_ts = parse_ts(curr["session_start_utc"])

        prev_end_soc = float(prev.get("end_soc") or 0)
        curr_start_soc = float(curr.get("start_soc") or 0)
        soc_drop = prev_end_soc - curr_start_soc

        # Skip if no driving happened (SOC didn't drop meaningfully)
        if soc_drop < 1.5:
            continue

        # Skip if gap is too short for a real trip
        gap_hours = (curr_start_ts - prev_end_ts).total_seconds() / 3600
        if gap_hours < 0.3:
            continue

        # Determine trip type from locations
        prev_loc = prev.get("location_name", "")
        curr_loc = curr.get("location_name", "")

        if "Home" in prev_loc and "Work" in curr_loc:
            trip_type = "commute"
            distance = round(random.uniform(12, 16), 1)  # ~14 mi commute
            duration = round(random.uniform(25, 40), 0)
        elif "Work" in prev_loc and "Home" in curr_loc:
            trip_type = "commute"
            distance = round(random.uniform(12, 16), 1)
            duration = round(random.uniform(25, 40), 0)
        elif any(kw in curr_loc for kw in ("BP Pulse", "Shell Recharge", "Electrify", "Tesla Supercharger")):
            # Highway trip TO a DC fast charger
            trip_type = "highway"
            # Distance based on SOC drop (98 kWh battery, ~2.1 mi/kWh avg)
            energy_used = soc_drop / 100 * 98
            distance = round(energy_used * random.uniform(1.8, 2.3), 1)
            duration = round(distance / random.uniform(45, 60) * 60, 0)  # 45-60 mph avg
        elif any(kw in prev_loc for kw in ("BP Pulse", "Shell Recharge", "Electrify", "Tesla Supercharger")):
            # Highway trip FROM a DC fast charger (going home)
            trip_type = "highway"
            energy_used = soc_drop / 100 * 98
            distance = round(energy_used * random.uniform(1.8, 2.3), 1)
            duration = round(distance / random.uniform(45, 60) * 60, 0)
        elif gap_hours > 48:
            # Long gap, probably multiple short errands
            trip_type = "errand"
            distance = round(random.uniform(8, 25), 1)
            duration = round(random.uniform(15, 45), 0)
        else:
            # Regular errand/daily driving
            trip_type = "errand"
            # Use miles_added from next charge as approximation
            miles_added = float(curr.get("miles_added") or 0)
            if miles_added > 1:
                distance = round(miles_added * random.uniform(0.9, 1.3), 1)
            else:
                distance = round(soc_drop / 100 * 98 * random.uniform(1.9, 2.4), 1)
            duration = round(distance / random.uniform(20, 35) * 60, 0)

        # Clamp duration to reasonable range
        duration = max(5, min(duration, 180))

        # Trip timing: starts sometime after prev charge ends, ends before next charge
        # Typically 1-3 hours before the next charge starts (you drive, then plug in)
        trip_end = curr_start_ts - timedelta(minutes=random.randint(5, 60))
        trip_start = trip_end - timedelta(minutes=int(duration))

        # Ensure trip_start is after prev charge ended
        if trip_start < prev_end_ts:
            trip_start = prev_end_ts + timedelta(minutes=random.randint(30, 120))
            trip_end = trip_start + timedelta(minutes=int(duration))

        ambient = seasonal_temp(trip_start)
        eff = efficiency_for_temp(ambient, trip_type)
        energy = round(distance / eff, 2) if eff > 0 else round(distance / 2.0, 2)
        scores = driving_scores(trip_type)

        # Regen: typically 5-15% of distance
        regen = round(distance * random.uniform(0.03, 0.15), 2)

        # Cabin temp (HVAC target ~70°F, varies slightly)
        cabin = round(random.uniform(68, 73), 1)

        # Electrical efficiency (similar to mi/kWh but from electrical perspective)
        elec_eff = round(eff * random.uniform(0.92, 1.02), 2)

        # Brake torque (higher for aggressive decel, lower for smooth)
        brake_torque = round(random.uniform(40, 200), 1)

        trips.append({
            "device_id": "demo_lightning_showcase",
            "start_time": trip_start.isoformat(),
            "end_time": trip_end.isoformat(),
            "recorded_at": trip_end.isoformat(),
            "distance": distance,
            "duration": duration,
            "energy_consumed": energy,
            "efficiency": eff,
            "range_regenerated": regen,
            "ambient_temp": ambient,
            "cabin_temp": cabin,
            "outside_air_temp": round(ambient + random.uniform(-3, 3), 1),
            **scores,
            "electrical_efficiency": elec_eff,
            "brake_torque": brake_torque,
            "is_complete": "True",
            "source_system": "sample_generator",
        })

    # Also add some standalone errands on days without charges
    # (weekends, short grocery runs, etc.)
    existing_dates = {parse_ts(t["start_time"]).date() for t in trips}

    # Add ~15 extra errand trips scattered through the date range
    if trips:
        first_date = min(parse_ts(t["start_time"]) for t in trips)
        last_date = max(parse_ts(t["end_time"]) for t in trips)
        total_days = (last_date - first_date).days

        extra_count = 0
        for _ in range(200):  # attempts
            if extra_count >= 15:
                break
            day_offset = random.randint(0, total_days)
            trip_date = (first_date + timedelta(days=day_offset)).replace(
                hour=random.choice([9, 10, 11, 13, 14, 15, 16]),
                minute=random.randint(0, 59),
            )
            if trip_date.date() in existing_dates:
                continue

            existing_dates.add(trip_date.date())
            distance = round(random.uniform(3, 12), 1)
            duration = round(distance / random.uniform(15, 25) * 60, 0)
            duration = max(5, min(duration, 60))
            trip_end = trip_date + timedelta(minutes=int(duration))
            ambient = seasonal_temp(trip_date)
            eff = efficiency_for_temp(ambient, "errand")
            energy = round(distance / eff, 2) if eff > 0 else round(distance / 2.0, 2)
            scores = driving_scores("errand")
            regen = round(distance * random.uniform(0.03, 0.12), 1)
            cabin = round(random.uniform(68, 73), 1)
            elec_eff = round(eff * random.uniform(0.92, 1.02), 2)
            brake_torque = round(random.uniform(40, 150), 1)

            trips.append({
                "device_id": "demo_lightning_showcase",
                "start_time": trip_date.isoformat(),
                "end_time": trip_end.isoformat(),
                "recorded_at": trip_end.isoformat(),
                "distance": distance,
                "duration": duration,
                "energy_consumed": energy,
                "efficiency": eff,
                "range_regenerated": regen,
                "ambient_temp": ambient,
                "cabin_temp": cabin,
                "outside_air_temp": round(ambient + random.uniform(-3, 3), 1),
                **scores,
                "electrical_efficiency": elec_eff,
                "brake_torque": brake_torque,
                "is_complete": "True",
                "source_system": "sample_generator",
            })
            extra_count += 1

    # Sort by start_time
    trips.sort(key=lambda t: t["start_time"])
    return trips


def write_csv(trips: list[dict], out_path: str):
    fieldnames = [
        "device_id", "start_time", "end_time", "recorded_at",
        "distance", "duration", "energy_consumed", "efficiency",
        "range_regenerated", "ambient_temp", "cabin_temp", "outside_air_temp",
        "driving_score", "speed_score", "acceleration_score", "deceleration_score",
        "electrical_efficiency", "brake_torque", "is_complete", "source_system",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trips)
    print(f"Wrote {len(trips)} trips to {out_path}")


def main():
    data_dir = Path("data")
    sessions = load_sessions(str(data_dir / "charging_sessions_sample.csv"))
    trips = derive_trips(sessions)
    write_csv(trips, str(data_dir / "trip_metrics_sample.csv"))

    # Stats
    distances = [t["distance"] for t in trips]
    efficiencies = [t["efficiency"] for t in trips]
    print("\nStats:")
    print(f"  Total trips: {len(trips)}")
    print(f"  Date range: {trips[0]['start_time'][:10]} to {trips[-1]['end_time'][:10]}")
    print(f"  Total distance: {sum(distances):.1f} mi")
    print(f"  Avg distance: {sum(distances)/len(distances):.1f} mi")
    print(f"  Avg efficiency: {sum(efficiencies)/len(efficiencies):.2f} mi/kWh")
    print(f"  Min/Max efficiency: {min(efficiencies):.2f} / {max(efficiencies):.2f}")


if __name__ == "__main__":
    main()

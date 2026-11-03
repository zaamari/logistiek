"""Koppelt invoer, geodata en de rekenkern; berekent beide scenario's en vergelijkt ze."""
from __future__ import annotations

import datetime as dt
import math
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from . import geo
from .solver import LOODS_LOC, START_LOC, Order, Params, solve

WINDOW_LENGTH = 120     # minuten, voorgesteld tijdvenster
WINDOW_STEP = 30        # vensters beginnen op hele of halve uren


class PlanError(ValueError):
    pass


def parse_time(value) -> int | None:
    """'8', '8:30', '08.30', 0.354 (Excel), datetime.time -> seconden sinds middernacht."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        value = value.time()
    if isinstance(value, dt.time):
        return value.hour * 3600 + value.minute * 60
    if isinstance(value, (int, float)) and 0 <= value < 1:
        return int(round(value * 24 * 60)) * 60
    match = re.fullmatch(r"\s*(\d{1,2})(?:[:.u](\d{2}))?\s*", str(value))
    if not match:
        raise PlanError(f"Onbekende tijd: '{value}' (gebruik bijv. 08:30)")
    hours, minutes = int(match.group(1)), int(match.group(2) or 0)
    if hours > 23 or minutes > 59:
        raise PlanError(f"Onbekende tijd: '{value}'")
    return hours * 3600 + minutes * 60


def normalize_split(value) -> str:
    text = str(value or "").strip().lower()
    if text in ("ja", "j", "yes", "y", "1", "true", "x"):
        return "ja"
    if text in ("nee", "n", "no", "0", "false"):
        return "nee"
    return "?"


def _params(settings: dict) -> Params:
    return Params(
        capacity=int(settings.get("capacity", 6)),
        load_time=int(float(settings.get("load_minutes", 30)) * 60),
        unload_first=int(float(settings.get("unload_first", 7)) * 60),
        unload_extra=int(float(settings.get("unload_extra", 3)) * 60),
        first_arrival=parse_time(settings.get("first_arrival") or "08:00"),
        time_limit=int(settings.get("time_limit", 5)),
    )


def _minutes(seconds: int | None) -> int | None:
    return None if seconds is None else int(round(seconds / 60))


def _maps_url(addresses: list[str]) -> str:
    query = {"api": 1, "travelmode": "driving", "origin": addresses[0], "destination": addresses[-1]}
    if len(addresses) > 2:
        query["waypoints"] = "|".join(addresses[1:-1])
    return "https://www.google.com/maps/dir/?" + urlencode(query)


def plan(orders_in: list[dict], settings: dict) -> dict:
    """Planning voor één dag, of met `settings["days"]` (bijv. ["zaterdag", "zondag"]) verdeeld over
    meerdere dagen. Een bestelling met `day` gaat dan verplicht op die dag."""
    p = _params(settings)
    day_names = [str(d).strip().lower() for d in (settings.get("days") or [])] or [None]
    if len(day_names) > 3 or len(set(day_names)) != len(day_names):
        raise PlanError("Kies één, twee of drie verschillende dagen.")
    start_address = (settings.get("start_address") or "").strip()
    loods_address = (settings.get("loods_address") or "").strip()
    if not start_address or not loods_address:
        raise PlanError("Vul eerst het start- en loodsadres in bij Instellingen.")

    orders: list[Order] = []
    known: list[dict | None] = []   # coördinaten die al bekend zijn (Houtenhak stuurt ze mee), anders opzoeken
    for i, row in enumerate(orders_in):
        address = (row.get("address") or "").strip()
        if not address:
            continue
        try:
            crates = int(row.get("crates") or 0)
        except ValueError:
            raise PlanError(f"Rij {i + 1}: aantal kratten is geen getal.")
        if crates < 1:
            raise PlanError(f"Rij {i + 1} ({row.get('name') or address}): minimaal 1 krat.")
        ws, we = parse_time(row.get("window_start")), parse_time(row.get("window_end"))
        if ws is not None and we is not None and we <= ws:
            raise PlanError(f"Rij {i + 1}: tijdvenster eindigt vóór het begint.")
        day = str(row.get("day") or "").strip().lower() or None
        if day is not None and day not in day_names:
            raise PlanError(f"Rij {i + 1} ({row.get('name') or address}): dag '{day}' hoort niet bij deze planning.")
        orders.append(Order(
            id=str(row.get("id") or i), name=(row.get("name") or "").strip() or address,
            address=address, crates=crates, split=normalize_split(row.get("split")),
            window_start=ws, window_end=we, loc=len(orders) + 2,
            day=day_names.index(day) if day is not None and len(day_names) > 1 else None,
        ))
        lat, lon = row.get("lat"), row.get("lon")
        valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon))
        known.append({"lat": float(lat), "lon": float(lon), "label": address} if valid else None)
    if not orders:
        raise PlanError("Er zijn geen bestellingen met een adres.")

    addresses = [start_address, loods_address] + [o.address for o in orders]
    given = [None, None] + known
    with ThreadPoolExecutor(max_workers=6) as pool:
        points = list(pool.map(lambda a, k: k or geo.geocode(a), addresses, given))
    problems = []
    for address, pt in zip(addresses, points):
        if pt is None:
            problems.append(f"'{address}' niet gevonden")
        elif "guess" in pt:
            problems.append(f"'{address}' niet gevonden (bedoel je {pt['guess']}?)")
    if problems:
        raise PlanError("Controleer de adressen (straat + huisnummer + plaats of postcode): " + "; ".join(problems))
    travel, source = geo.travel_matrix(points)

    warnings = []
    if source != "osrm":
        warnings.append("Routedienst niet bereikbaar: reistijden zijn geschat op basis van afstand.")
    for o in orders:
        if o.crates > p.capacity:
            warnings.append(f"{o.name} heeft {o.crates} kratten (meer dan {p.capacity}) en wordt altijd gesplitst.")

    ids_yes = {o.id for o in orders if o.split == "ja"}
    ids_maybe = {o.id for o in orders if o.split != "nee"}
    days = len(day_names)
    nosplit = solve(orders, travel, p, ids_yes, days=days)
    same = ids_yes == ids_maybe
    split = nosplit if same else solve(orders, travel, p, ids_maybe, initial=[d["sequence"] for d in nosplit], days=days)
    # Vergelijken op de werktijd van alle dagen samen (vanaf de eerste klant tot terug in de loods).
    total = lambda plans: sum(d["finish"] - p.first_arrival for d in plans if d["finish"] is not None)
    if total(split) >= total(nosplit):
        split = nosplit

    saving = total(nosplit) - total(split)
    by_id = {o.id: o for o in orders}
    split_orders = [oid for d in split for oid in d["split_orders"]]
    ask = [oid for oid in split_orders if oid not in ids_yes and by_id[oid].crates <= p.capacity]
    if same:
        message = "Bij alle bestellingen is al bekend of splitsen mag; er is één planning."
    elif not ask:
        message = "Splitsen levert niets op. Je hoeft niets aan klanten te vragen."
    else:
        names = ", ".join(by_id[oid].name for oid in ask)
        message = f"Met splitsen ben je {_minutes(saving)} min eerder klaar. Vraag aan: {names}."

    return {
        "source": source,
        "warnings": warnings,
        "points": {
            "start": {**points[0], "address": start_address},
            "loods": {**points[1], "address": loods_address},
        },
        "orders": [{
            "id": o.id, "name": o.name, "address": o.address, "crates": o.crates, "split": o.split,
            "label": points[o.loc]["label"], "lat": points[o.loc]["lat"], "lon": points[o.loc]["lon"],
        } for o in orders],
        "scenarios": {
            "nosplit": _scenario(nosplit, day_names, orders, points, p, start_address, loods_address),
            "split": _scenario(split, day_names, orders, points, p, start_address, loods_address),
        },
        "compare": {
            "same": same or split is nosplit,
            "saving": _minutes(saving),
            "reloads_saved": sum(d["reloads"] for d in nosplit) - sum(d["reloads"] for d in split),
            "ask": ask,
            "message": message,
        },
    }


def _scenario(plans: list[dict], day_names: list, orders, points, p, start_address, loods_address) -> dict:
    """Eén dag: de planning zelf (zoals altijd). Meerdere dagen: {"days": [planning per dag, met "day"]}."""
    presented = [_present(d, orders, points, p, start_address, loods_address) for d in plans]
    if len(presented) == 1:
        return presented[0]
    return {"days": [{"day": name, **d} for name, d in zip(day_names, presented)]}


def _suggest_window(arrive: int, earliest: int) -> tuple[int, int]:
    step = WINDOW_STEP * 60
    start = max(earliest, (arrive - WINDOW_LENGTH * 60 // 2) // step * step)
    return start, start + WINDOW_LENGTH * 60


def _present(result: dict, orders: list[Order], points: list[dict], p: Params,
             start_address: str, loods_address: str) -> dict:
    trips = []
    seen: set[int] = set()
    for nr, trip in enumerate(result["trips"], 1):
        origin_loc = START_LOC if trip["from"] == "start" else LOODS_LOC
        stops = []
        for s in trip["stops"]:
            o = orders[s["order"]]
            if o.window_start is not None or o.window_end is not None:
                window, suggested = [_minutes(o.window_start), _minutes(o.window_end)], False
            elif s["order"] not in seen:
                window, suggested = list(map(_minutes, _suggest_window(s["arrive"], p.first_arrival))), True
            else:
                window, suggested = None, False
            seen.add(s["order"])
            stops.append({
                "id": o.id, "name": o.name, "address": o.address, "crates": s["crates"],
                "partial": s["crates"] < o.crates, "of": o.crates,
                "arrive": _minutes(s["arrive"]), "leave": _minutes(s["leave"]),
                "wait": _minutes(s["wait"]), "drive": _minutes(s["drive"]),
                "late": s["late"], "window": window, "suggested": suggested,
            })
        route_locs = [origin_loc] + [orders[s["order"]].loc for s in trip["stops"]] + [LOODS_LOC]
        route_addresses = [start_address if trip["from"] == "start" else loods_address] \
            + [orders[s["order"]].address for s in trip["stops"]] + [loods_address]
        trips.append({
            "nr": nr, "from": trip["from"], "depart": _minutes(trip["depart"]),
            "arrive_loods": _minutes(trip["arrive_loods"]), "load": trip["load"], "stops": stops,
            "maps_url": _maps_url(route_addresses),
            "geometry": [points[i] for i in route_locs],
        })
    with ThreadPoolExecutor(max_workers=6) as pool:
        for trip, line in zip(trips, pool.map(geo.route_geometry, [t["geometry"] for t in trips])):
            trip["geometry"] = line
    return {
        "trips": trips,
        "departure": _minutes(result["departure"]),
        "first_arrival": trips[0]["stops"][0]["arrive"] if trips and trips[0]["stops"] else None,
        "finish": _minutes(result["finish"]),
        "work_time": _minutes(result["work_time"]),
        "drive_time": _minutes(result["drive_time"]),
        "reloads": result["reloads"],
        "split_orders": result["split_orders"],
        "late": result["late"],
        "min_loads": math.ceil(sum(t["load"] for t in trips) / p.capacity),
    }

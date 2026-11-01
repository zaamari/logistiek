"""Rekenkern: routeplanning met maximaal `capacity` kratten per rit.

Model (Google OR-Tools, één voertuig):
- knoop 0 = startlocatie (je vertrekt met een volle lading)
- knoop 1 = loods (eindpunt van de dag)
- klantknopen: één knoop per bestelling, of één knoop per krat als
  splitsen mag (zo kan een bestelling over meerdere ritten verdeeld worden)
- laadknopen: optionele bezoeken aan de loods om bij te laden

Alle tijden zijn in seconden sinds middernacht.
De rit van start naar de eerste klant telt niet mee in de werktijd: je
bent om `first_arrival` bij de eerste klant en vertrekt zo vroeg als nodig.
Daardoor kiest de planner bij voorkeur een verre klant als eerste stop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

START_LOC = 0
LOODS_LOC = 1

LATE_PENALTY = 1000   # kosten per seconde te laat binnen een tijdvenster
SPAN_WEIGHT = 100     # kosten per seconde werktijd (eerste klant -> loods)


@dataclass
class Order:
    id: str
    name: str
    address: str
    crates: int
    split: str = "?"                 # "ja" | "nee" | "?"
    window_start: int | None = None
    window_end: int | None = None
    loc: int = 0                     # index in de reistijdmatrix


@dataclass
class Params:
    capacity: int = 6
    load_time: int = 30 * 60
    unload_first: int = 7 * 60
    unload_extra: int = 3 * 60
    first_arrival: int = 8 * 3600
    time_limit: int = 5


def unload_time(crates: int, p: Params) -> int:
    return p.unload_first + p.unload_extra * (crates - 1)


class _Model:
    def __init__(self, orders: list[Order], travel: list[list[int]], p: Params, split_ids: set[str]):
        self.orders, self.travel, self.p = orders, travel, p
        self.kind: list[str] = []
        self.loc: list[int] = []
        self.order: list[int | None] = []
        self.demand: list[int] = []
        self.unit: list[bool] = []
        self.order_nodes: list[list[int]] = []
        self.reload_nodes: list[int] = []

        self._add("start", START_LOC)
        self._add("end", LOODS_LOC)
        for oi, o in enumerate(orders):
            if o.id in split_ids or o.crates > p.capacity:
                nodes = [self._add("cust", o.loc, oi, 1, True) for _ in range(o.crates)]
            else:
                nodes = [self._add("cust", o.loc, oi, o.crates, False)]
            self.order_nodes.append(nodes)

        n_cust = len(self.kind) - 2
        total = sum(o.crates for o in orders)
        n_reload = min(n_cust, math.ceil(total / p.capacity) + len(orders))
        self.reload_nodes = [self._add("reload", LOODS_LOC, demand=-p.capacity) for _ in range(n_reload)]

        n = len(self.kind)
        self.time = [[0] * n for _ in range(n)]
        self.cost = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                drive = travel[self.loc[i]][self.loc[j]]
                service = self._service_out(i, j)
                self.time[i][j] = service + (0 if self.kind[i] == "start" else drive)
                self.cost[i][j] = service + drive

    def _add(self, kind, loc, order=None, demand=0, unit=False) -> int:
        self.kind.append(kind)
        self.loc.append(loc)
        self.order.append(order)
        self.demand.append(demand)
        self.unit.append(unit)
        return len(self.kind) - 1

    def _service_out(self, i: int, j: int) -> int:
        """Tijd op knoop i voordat je naar j rijdt.

        Bij losse kratten van dezelfde bestelling achter elkaar kost elke
        volgende krat `unload_extra`; de laatste krat draagt `unload_first`,
        zodat een stop met n kratten samen unload_time(n) duurt.
        """
        kind = self.kind[i]
        if kind == "reload":
            return self.p.load_time
        if kind != "cust":
            return 0
        if self.unit[i]:
            if self.unit[j] and self.order[j] == self.order[i]:
                return self.p.unload_extra
            return self.p.unload_first
        return unload_time(self.demand[i], self.p)

    def sequence_to_nodes(self, sequence: list[tuple]) -> list[int] | None:
        """Zet een eerdere planning (stops/laadmomenten) om naar knopen van dit model."""
        used = {oi: 0 for oi in range(len(self.orders))}
        reloads = iter(self.reload_nodes)
        nodes = []
        for item in sequence:
            if item[0] == "reload":
                node = next(reloads, None)
                if node is None:
                    return None
                nodes.append(node)
                continue
            _, oi, crates = item
            available = self.order_nodes[oi]
            if self.unit[available[0]]:
                take = available[used[oi]:used[oi] + crates]
                if len(take) != crates:
                    return None
                used[oi] += crates
                nodes.extend(take)
            else:
                if used[oi] or crates != self.demand[available[0]]:
                    return None
                used[oi] = 1
                nodes.append(available[0])
        return nodes


def solve(orders: list[Order], travel: list[list[int]], p: Params, split_ids: set[str],
          initial: list[tuple] | None = None) -> dict:
    if not orders:
        raise ValueError("Geen bestellingen om te plannen.")
    m = _Model(orders, travel, p, split_ids)
    n = len(m.kind)

    manager = pywrapcp.RoutingIndexManager(n, 1, [0], [1])
    routing = pywrapcp.RoutingModel(manager)

    def cost_cb(a, b):
        return m.cost[manager.IndexToNode(a)][manager.IndexToNode(b)]

    def time_cb(a, b):
        return m.time[manager.IndexToNode(a)][manager.IndexToNode(b)]

    def demand_cb(a):
        return m.demand[manager.IndexToNode(a)]

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(cost_cb))

    routing.AddDimension(routing.RegisterTransitCallback(time_cb), 24 * 3600, 48 * 3600, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    time_dim.CumulVar(routing.Start(0)).SetRange(p.first_arrival, p.first_arrival)
    time_dim.SetSpanCostCoefficientForAllVehicles(SPAN_WEIGHT)

    # Laadruimte: cumul = kratten afgeleverd sinds laatste keer laden (max capacity).
    # Een laadknoop trekt `capacity` af; de slack maakt bijladen van een halflege bus mogelijk.
    routing.AddDimension(routing.RegisterUnaryTransitCallback(demand_cb), p.capacity, p.capacity, True, "Load")
    load_dim = routing.GetDimensionOrDie("Load")
    load_dim.SlackVar(routing.Start(0)).SetValue(0)

    for node in range(2, n):
        index = manager.NodeToIndex(node)
        if m.kind[node] == "reload":
            routing.AddDisjunction([index], 0)
            continue
        load_dim.SlackVar(index).SetValue(0)
        o = orders[m.order[node]]
        if o.window_start is not None:
            time_dim.CumulVar(index).SetMin(o.window_start)
        if o.window_end is not None:
            time_dim.SetCumulVarSoftUpperBound(index, o.window_end, LATE_PENALTY)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(p.time_limit)

    solution = None
    if initial:
        nodes = m.sequence_to_nodes(initial)
        if nodes:
            routing.CloseModelWithParameters(params)
            start = routing.ReadAssignmentFromRoutes([[manager.NodeToIndex(x) for x in nodes]], True)
            if start:
                solution = routing.SolveFromAssignmentWithParameters(start, params)
    if solution is None:
        solution = routing.SolveWithParameters(params)
    if solution is None:
        raise RuntimeError("Geen planning gevonden.")

    nodes = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        nodes.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))
    return _itinerary(m, nodes[1:])


def _itinerary(m: _Model, nodes: list[int]) -> dict:
    """Bouw ritten en tijden op uit de knoopvolgorde (zo vroeg mogelijk rijden)."""
    p, orders, travel = m.p, m.orders, m.travel

    # Opeenvolgende kratten van dezelfde bestelling = één stop.
    sequence: list[tuple] = []
    for node in nodes:
        if m.kind[node] == "reload":
            sequence.append(("reload",))
        elif sequence and sequence[-1][0] == "stop" and sequence[-1][1] == m.order[node]:
            sequence[-1] = ("stop", m.order[node], sequence[-1][2] + m.demand[node])
        else:
            sequence.append(("stop", m.order[node], m.demand[node]))
    # Laadmoment direct na start of dubbel laden heeft geen zin.
    cleaned = []
    for item in sequence:
        if item[0] == "reload" and (not cleaned or cleaned[-1][0] == "reload"):
            continue
        cleaned.append(item)
    while cleaned and cleaned[-1][0] == "reload":
        cleaned.pop()
    sequence = cleaned

    trips = [{"from": "start", "depart": None, "stops": []}]
    prev_loc = START_LOC
    t = None
    departure = None
    drive_total = 0
    late = []
    for item in sequence + [("end",)]:
        loc = orders[item[1]].loc if item[0] == "stop" else LOODS_LOC
        drive = travel[prev_loc][loc]
        drive_total += drive
        window_start = orders[item[1]].window_start if item[0] == "stop" else None
        if t is None:
            arrive = max(p.first_arrival, window_start or 0)
            departure = arrive - drive
            trips[0]["depart"] = departure
        else:
            arrive = t + drive
        wait = 0
        if window_start is not None and arrive < window_start:
            wait = window_start - arrive
            arrive = window_start

        if item[0] == "stop":
            o = orders[item[1]]
            done = arrive + unload_time(item[2], p)
            is_late = o.window_end is not None and arrive > o.window_end
            if is_late:
                late.append(o.id)
            trips[-1]["stops"].append({
                "order": item[1], "crates": item[2], "arrive": arrive, "leave": done,
                "wait": wait, "drive": drive, "late": is_late,
            })
            t = done
        elif item[0] == "reload":
            trips[-1]["arrive_loods"] = arrive
            t = arrive + p.load_time
            trips.append({"from": "loods", "depart": t, "stops": []})
        else:
            trips[-1]["arrive_loods"] = arrive
            t = arrive
        prev_loc = loc

    stops_per_order: dict[int, int] = {}
    for trip in trips:
        trip["load"] = sum(s["crates"] for s in trip["stops"])
        for s in trip["stops"]:
            stops_per_order[s["order"]] = stops_per_order.get(s["order"], 0) + 1

    return {
        "trips": trips,
        "sequence": sequence,
        "departure": departure,
        "finish": t,
        "work_time": t - departure,
        "drive_time": drive_total,
        "reloads": len(trips) - 1,
        "split_orders": [orders[oi].id for oi, k in stops_per_order.items() if k > 1],
        "late": late,
    }

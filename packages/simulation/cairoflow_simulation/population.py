"""Identity of the vehicles a SUMO route file runs (ADR-033, schema "population-v1").

Two runs may be compared seed by seed only if they ran the same vehicles. A route file lists
vehicle types and vehicles; the one attribute that legitimately differs between the seeds of a
replay is each vehicle's `speedFactor`, drawn per seed (`trace_demand.generate_trace_demand`).
So two hashes describe a route file:

- `population_sha256`: the vehicles themselves, `speedFactor` excluded. Equal across the seeds of
  a trace (replay) baseline; for a stochastic demand (`demand.generate_demand`) departures are
  drawn per seed too, so each seed has its own population and nothing here claims otherwise.
- `speed_factors_sha256`: the excluded draws alone, so the two together cover the vehicle table.

The route file's own SHA-256 (`RunRecord.demand_sha256`) covers both, byte for byte.

The canonical object is `[types, vehicles]`. `types` is every `vType`'s attributes as sorted
(key, value) pairs, sorted. `vehicles` is, per `vehicle`, (`depart` parsed as a float, `id`, the
other attributes as sorted pairs without `speedFactor`, the child route's `edges`), sorted by
(depart, id). It is hashed as JSON with sorted keys and no spaces. This is the definition the G0
spike used, so hashes computed here equal the ones recorded for the four timeline members.
`depart` is parsed only to order the vehicles; its text is hashed as written with the other
attributes, so "1.0" and "1.00" are different populations (the generators always write one
decimal).

A hash that ignored part of a file would call different vehicle sets equal, so only the shape the
generators write is accepted: a root holding `vType` elements without children and `vehicle`
elements with unique ids and exactly one child, a `route` whose only attribute is `edges`. Anything
else (a `route` attribute, a top-level `route` or distribution, a `stop`, a `param`, a repeating
route, a trip, a flow) is refused, not skipped.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from cairoflow_contracts.pairing import POPULATION_VERSION

__all__ = ["POPULATION_VERSION", "PopulationError", "population_sha256", "speed_factors_sha256"]
EXCLUDED_ATTRIBUTE = "speedFactor"
_ALLOWED = ("vType", "vehicle")


class PopulationError(ValueError):
    """A route file has no population that can be identified."""


def _sha256_json(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vehicles(root: ET.Element, source: str) -> list[ET.Element]:
    """The root's vehicles, once the whole file is known to be in the accepted shape."""
    for child in root:
        if child.tag not in _ALLOWED:
            raise PopulationError(
                f"{source}: a <{child.tag}> element has no population identity here"
            )
    for vehicle_type in root.findall("vType"):
        if len(vehicle_type):
            raise PopulationError(f"{source}: <vType {vehicle_type.get('id')}> has child elements")
    vehicles = root.findall("vehicle")
    if not vehicles:
        raise PopulationError(f"{source}: no <vehicle> element")
    ids = [v.get("id") for v in vehicles]
    if any(i is None for i in ids):
        raise PopulationError(f"{source}: a vehicle has no id")
    if len(set(ids)) != len(ids):
        raise PopulationError(f"{source}: vehicle ids are not unique")
    for vehicle in vehicles:
        children = list(vehicle)
        if "route" in vehicle.attrib or len(children) != 1 or children[0].tag != "route":
            raise PopulationError(
                f"{source}: vehicle {vehicle.get('id')} needs exactly one <route> child"
            )
        if set(children[0].attrib) != {"edges"} or len(children[0]):
            raise PopulationError(
                f"{source}: the route of vehicle {vehicle.get('id')} may only have edges"
            )
    return vehicles


def _root(route_file: Path) -> ET.Element:
    try:
        return ET.parse(route_file).getroot()
    except ET.ParseError as error:
        raise PopulationError(f"{route_file}: not well-formed XML ({error})") from error


def population_sha256(route_file: Path) -> str:
    """SHA-256 of the file's vehicle types and vehicles, `speedFactor` excluded (see the module)."""
    root = _root(route_file)
    types = sorted(tuple(sorted(t.attrib.items())) for t in root.findall("vType"))
    vehicles = []
    for vehicle in _vehicles(root, str(route_file)):
        attributes = tuple(
            sorted((k, v) for k, v in vehicle.attrib.items() if k != EXCLUDED_ATTRIBUTE)
        )
        try:
            depart = float(vehicle.get("depart", 0))
        except ValueError as error:
            raise PopulationError(
                f"{route_file}: vehicle {vehicle.get('id')} has depart {vehicle.get('depart')!r}"
            ) from error
        edges = vehicle.find("route").get("edges")
        vehicles.append((depart, vehicle.get("id"), attributes, edges))
    vehicles.sort(key=lambda row: (row[0], row[1]))
    return _sha256_json([types, vehicles])


def speed_factors_sha256(route_file: Path) -> str:
    """SHA-256 of the file's (vehicle id, `speedFactor`) pairs, sorted by id."""
    vehicles = _vehicles(_root(route_file), str(route_file))
    return _sha256_json(sorted((v.get("id"), v.get(EXCLUDED_ATTRIBUTE)) for v in vehicles))

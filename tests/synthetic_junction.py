"""A synthetic four-arm junction in local ground metres, shared by tests.

Right-hand traffic, origin at the centre. The south arm has two approach lanes between y = -35 and
its stop line at y = -10: S1 (x 0 to 3.5, left or through) and S2 (x 3.5 to 7, through or right).
The north arm (N1: left, through, right) and the west arm (W1: through, right) have one approach
lane each; the east arm has only an exit line. Exit lines lie on the outgoing side of each arm.
"""

from __future__ import annotations

from cairoflow_contracts.evidence import Gate, IntersectionGeometry, LanePolygon


def lane(lane_id, arm, polygon, turns=(), direction="approach") -> LanePolygon:
    return LanePolygon(
        lane_id=lane_id,
        arm=arm,
        direction=direction,
        polygon_m=polygon,
        allowed_turns=turns,
        provenance="synthetic",
        source="test drawing",
    )


def gate(arm, role, a, b) -> Gate:
    return Gate(arm=arm, role=role, segment_m=(a, b), provenance="synthetic", source="test")


LANES = (
    lane("S1", "S", [(0, -35), (3.5, -35), (3.5, -10), (0, -10)], ("left", "through")),
    lane("S2", "S", [(3.5, -35), (7, -35), (7, -10), (3.5, -10)], ("through", "right")),
    lane("N1", "N", [(-7, 10), (0, 10), (0, 60), (-7, 60)], ("left", "through", "right")),
    lane("W1", "W", [(-30, -7), (-10, -7), (-10, 0), (-30, 0)], ("through", "right")),
)
GATES = (
    gate("S", "stop_line", (0, -10), (7, -10)),
    gate("N", "stop_line", (-7, 10), (0, 10)),
    gate("W", "stop_line", (-10, -7), (-10, 0)),
    gate("N", "exit_line", (0, 10), (7, 10)),
    gate("E", "exit_line", (10, -7), (10, 0)),
    gate("S", "exit_line", (-7, -10), (0, -10)),
    gate("W", "exit_line", (-10, 0), (-10, 7)),
)


def geometry(calibration_spec_sha256: str, clip_id: str, lanes=LANES) -> IntersectionGeometry:
    return IntersectionGeometry(
        geometry_id="synthetic-4arm",
        clip_id=clip_id,
        calibration_spec_sha256=calibration_spec_sha256,
        lanes=lanes,
        gates=GATES,
    )

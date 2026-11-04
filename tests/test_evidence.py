"""Evidence schemas: valid records round-trip through JSON; invalid ones are rejected."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from cairoflow_contracts.evidence import (
    SCHEMA_VERSION,
    ClipLicense,
    FrameRef,
    GeometryValue,
    Observation,
    Provenance,
)

SHA = "a" * 64
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _provenance(**overrides) -> Provenance:
    values = {"kind": "model_generated", "produced_by": "sam-3.1", "produced_at_utc": NOW}
    return Provenance(**(values | overrides))


def _licence(**overrides) -> ClipLicense:
    values = {
        "source_id": "ngsim-lankershim",
        "source_url": "https://data.transportation.gov/d/uv3e-y54k",
        "licence_name": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": "U.S. Department of Transportation, Federal Highway Administration (2016).",
        "accessed_on": date(2026, 10, 27),
        "register_ref": "docs/DATA_REGISTER.md#2",
        "permits_commercial_use": True,
        "permits_cloud_processing": True,
        "permits_redistribution": True,
        "share_alike": True,
    }
    return ClipLicense(**(values | overrides))


def _observation(**overrides) -> Observation:
    values = {
        "observation_id": "obs-1",
        "frame": FrameRef(clip_id="c1", frame_index=3, timestamp_s=0.3),
        "class_label": "car",
        "object_id": "car:12",
        "bbox_xyxy_px": (10.0, 20.0, 50.0, 60.0),
        "confidence": 0.9,
        "provenance": _provenance(),
    }
    return Observation(**(values | overrides))


@pytest.mark.parametrize("make", [_provenance, _licence, _observation])
def test_round_trip_through_json(make) -> None:
    record = make()
    assert type(record).model_validate_json(record.model_dump_json()) == record
    assert record.schema_version == SCHEMA_VERSION


def test_records_are_immutable_and_reject_unknown_fields() -> None:
    record = _observation()
    with pytest.raises(ValidationError):
        record.confidence = 0.1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Observation.model_validate(record.model_dump() | {"speed_kmh": 40})


def test_wrong_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _provenance(schema_version="9.9.9")


@pytest.mark.parametrize(
    "box", [(50.0, 20.0, 10.0, 60.0), (10.0, 20.0, 10.0, 60.0), (-1.0, 0.0, 5.0, 5.0)]
)
def test_observation_rejects_invalid_boxes(box) -> None:
    with pytest.raises(ValidationError):
        _observation(bbox_xyxy_px=box)


def test_observation_rejects_out_of_range_confidence_and_bad_namespace() -> None:
    with pytest.raises(ValidationError):
        _observation(confidence=1.2)
    with pytest.raises(ValidationError):
        _observation(object_id="bus:12")  # namespace must match class_label
    with pytest.raises(ValidationError):
        _observation(object_id="12")
    assert _observation(confidence=None).confidence is None  # absent is allowed, not invented


def test_provenance_requires_utc_and_real_hashes() -> None:
    with pytest.raises(ValidationError):
        _provenance(produced_at_utc=datetime(2026, 10, 27, 12, 0))  # naive
    with pytest.raises(ValidationError):
        _provenance(produced_at_utc=NOW.astimezone(timezone(timedelta(hours=2))))
    with pytest.raises(ValidationError):
        _provenance(input_sha256=("not-a-hash",))
    with pytest.raises(ValidationError):
        _provenance(kind="measured")  # not an evidence kind
    assert _provenance(input_sha256=(SHA,)).input_sha256 == (SHA,)


def test_licence_controls_upload_and_requires_attribution() -> None:
    assert _licence().usable_for_upload
    assert not _licence(permits_cloud_processing=False).usable_for_upload
    assert not _licence(permits_commercial_use=False).usable_for_upload
    with pytest.raises(ValidationError):
        _licence(attribution="")
    with pytest.raises(ValidationError):
        _licence(source_url="ftp://example.org/clip")


def test_geometry_value_carries_an_adr_014_label() -> None:
    lane = GeometryValue(
        value=3.4, unit="m", provenance="approximated", source="homography", error=0.2
    )
    assert lane.provenance == "approximated"
    arm = GeometryValue(value=200, unit="m", provenance="synthetic", source="beyond camera view")
    assert arm.error is None
    with pytest.raises(ValidationError):
        GeometryValue(value=3.4, unit="m", provenance="guessed", source="x")
    with pytest.raises(ValidationError):
        GeometryValue(value=200, unit="m", provenance="synthetic", source="x", error=1.0)
    with pytest.raises(ValidationError):
        GeometryValue(value=3.4, unit="ft", provenance="measured", source="x")

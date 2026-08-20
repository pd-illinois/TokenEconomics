"""Fail-closed loading for versioned Plan commercial rate cards."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .commercial_contracts import (
    CommercialMeter,
    CommercialRateCard,
    EvidenceStatus,
    NativeUnit,
)


DEFAULT_COPILOT_STUDIO_RATE_CARD = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "commercial"
    / "copilot_studio_standard.v1.json"
)


class RateCardError(ValueError):
    """Raised when commercial evidence is missing, stale, or invalid."""


def _required(document: dict, field: str):
    value = document.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise RateCardError(f"{field} is required")
    return value


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RateCardError(f"{field} must be an ISO date") from exc


def _datetime(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise RateCardError(f"{field} must be an ISO timestamp") from exc


def load_copilot_studio_rate_card(
    path: str | Path = DEFAULT_COPILOT_STUDIO_RATE_CARD,
    *,
    as_of: date | None = None,
) -> CommercialRateCard:
    """Load one reviewed standard-harness rate card without fallback pricing."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RateCardError(f"rate card not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RateCardError(f"rate card could not be read: {path}") from exc
    if not isinstance(document, dict):
        raise RateCardError("rate card must be a JSON object")

    source_url = str(_required(document, "source_url"))
    if not source_url.startswith("https://"):
        raise RateCardError("source_url must use https")
    if document.get("status") != "active_reviewed":
        raise RateCardError("rate card status must be active_reviewed")
    if document.get("evidence_status") != EvidenceStatus.DOCUMENTED.value:
        raise RateCardError("active rate card evidence_status must be documented")

    effective_from = _date(_required(document, "effective_from"), "effective_from")
    effective_to_raw = document.get("effective_to")
    effective_to = (
        _date(effective_to_raw, "effective_to") if effective_to_raw else None
    )
    review_after = _date(_required(document, "review_after"), "review_after")
    forecast_date = as_of or date.today()
    if forecast_date < effective_from or (
        effective_to is not None and forecast_date > effective_to
    ):
        raise RateCardError(f"rate card is not effective on {forecast_date.isoformat()}")
    if forecast_date > review_after:
        raise RateCardError(
            f"rate card is stale after {review_after.isoformat()} and requires review"
        )

    version = str(_required(document, "version"))
    product = str(_required(document, "product"))
    experience = str(_required(document, "experience"))
    harness = str(_required(document, "harness"))
    source_retrieved_at = _datetime(
        _required(document, "source_retrieved_at"), "source_retrieved_at"
    )

    raw_meters = document.get("meters")
    if not isinstance(raw_meters, list) or not raw_meters:
        raise RateCardError("meters must be a non-empty array")

    meters: list[CommercialMeter] = []
    seen: set[str] = set()
    for raw in raw_meters:
        if not isinstance(raw, dict):
            raise RateCardError("each meter must be an object")
        meter_id = str(_required(raw, "meter_id"))
        if meter_id in seen:
            raise RateCardError(f"duplicate meter_id: {meter_id}")
        seen.add(meter_id)
        try:
            meter = CommercialMeter(
                meter_id=meter_id,
                product=product,
                experience=experience,
                harness=harness,
                feature=str(_required(raw, "feature")),
                native_unit=NativeUnit(_required(raw, "native_unit")),
                unit_size=float(_required(raw, "unit_size")),
                credits_per_unit=float(_required(raw, "credits_per_unit")),
                rate_card_version=version,
                effective_from=effective_from,
                effective_to=effective_to,
                source_url=source_url,
                source_retrieved_at=source_retrieved_at,
                evidence_status=EvidenceStatus.DOCUMENTED,
                separately_billed_byom=bool(
                    raw.get("separately_billed_byom", False)
                ),
                additive=bool(raw.get("additive", True)),
            )
        except (TypeError, ValueError) as exc:
            raise RateCardError(f"invalid meter {meter_id}: {exc}") from exc
        meters.append(meter)

    return CommercialRateCard(
        schema_version=str(_required(document, "schema_version")),
        rate_card_id=str(_required(document, "rate_card_id")),
        version=version,
        status="active_reviewed",
        evidence_status=EvidenceStatus.DOCUMENTED,
        effective_from=effective_from,
        effective_to=effective_to,
        review_after=review_after,
        source_url=source_url,
        source_revision=str(_required(document, "source_revision")),
        source_retrieved_at=source_retrieved_at,
        meters=tuple(meters),
    )

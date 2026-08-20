"""Versioned commercial-meter contracts for Plan-side forecasting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class EvidenceStatus(str, Enum):
    DOCUMENTED = "documented"
    MODELED = "modeled"
    SIMULATED = "simulated"
    PROPOSED = "proposed"
    MEASURED = "measured"
    BLOCKED = "blocked"
    PRODUCTION_VALIDATED = "production_validated"


class NativeUnit(str, Enum):
    EVENT = "event"
    ACTION = "action"
    TOKEN_OR_CHARACTER = "token_or_character"
    PAGE = "page"
    IMAGE = "image"
    MINUTE = "minute"
    API_CALL = "api_call"
    RUNTIME = "runtime"
    SUBSCRIPTION_SEAT = "subscription_seat"


class BillingDisposition(str, Enum):
    BILLABLE = "billable"
    ZERO_RATED_BY_ENTITLEMENT = "zero_rated_by_entitlement"
    INCLUDED_FIXED_SUBSCRIPTION = "included_fixed_subscription"
    TEST_EXEMPT = "test_exempt"
    SEPARATELY_BILLED_BYOM = "separately_billed_byom"
    UNKNOWN_REQUIRES_POLICY = "unknown_requires_policy"


@dataclass(frozen=True)
class CommercialMeter:
    meter_id: str
    product: str
    experience: str
    harness: str
    feature: str
    native_unit: NativeUnit
    unit_size: float
    credits_per_unit: float
    rate_card_version: str
    effective_from: date
    effective_to: date | None
    source_url: str
    source_retrieved_at: datetime
    evidence_status: EvidenceStatus
    separately_billed_byom: bool = False
    additive: bool = True

    def __post_init__(self) -> None:
        required = {
            "meter_id": self.meter_id,
            "product": self.product,
            "experience": self.experience,
            "harness": self.harness,
            "feature": self.feature,
            "rate_card_version": self.rate_card_version,
            "source_url": self.source_url,
        }
        for field, value in required.items():
            if not value.strip():
                raise ValueError(f"{field} is required")
        if not self.source_url.startswith("https://"):
            raise ValueError("source_url must use https")
        if not math.isfinite(self.unit_size) or self.unit_size <= 0:
            raise ValueError("unit_size must be finite and greater than zero")
        if not math.isfinite(self.credits_per_unit) or self.credits_per_unit < 0:
            raise ValueError("credits_per_unit must be finite and non-negative")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")


@dataclass(frozen=True)
class CommercialRateCard:
    schema_version: str
    rate_card_id: str
    version: str
    status: str
    evidence_status: EvidenceStatus
    effective_from: date
    effective_to: date | None
    review_after: date
    source_url: str
    source_revision: str
    source_retrieved_at: datetime
    meters: tuple[CommercialMeter, ...]

    def meter(self, meter_id: str) -> CommercialMeter:
        matches = [meter for meter in self.meters if meter.meter_id == meter_id]
        if len(matches) != 1:
            raise KeyError(f"meter not found: {meter_id}")
        return matches[0]


@dataclass(frozen=True)
class EntitlementContext:
    user_segment: str
    audience_type: str
    authenticated: bool | None
    identity_mode: str
    license_sku: str | None
    channel: str
    trigger_type: str
    product_boundary: str
    evidence_version: str
    computer_use: bool = False
    test_mode: bool = False
    fair_use_assumption: str | None = None

    @property
    def default_disposition(self) -> BillingDisposition:
        """Remain unknown until the versioned entitlement engine evaluates evidence."""
        return BillingDisposition.UNKNOWN_REQUIRES_POLICY


@dataclass(frozen=True)
class EntitlementDecision:
    meter_id: str
    disposition: BillingDisposition
    reason_code: str
    explanation: str
    rule_set_version: str
    evidence_status: EvidenceStatus


@dataclass(frozen=True)
class PredictedUsageEvent:
    event_id: str
    feature: str
    native_unit: NativeUnit
    quantity: float
    meter_id: str
    disposition: BillingDisposition
    evidence_status: EvidenceStatus
    quantity_source: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.quantity) or self.quantity < 0:
            raise ValueError("quantity must be finite and non-negative")

"""Tier 2 calibrator — learns correction factors from historical predictions.

Uses simple linear regression per model+archetype to adjust Tier 1
heuristic estimates based on recorded predicted-vs-actual pairs.

Calibration model:  actual ≈ slope × predicted + intercept

When enough data is available (≥ MIN_SAMPLES) and the fit is reasonable
(R² ≥ MIN_R_SQUARED), the calibrator adjusts Tier 1 predictions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from future_token_predictor.history.database import HistoryDatabase
from future_token_predictor.models.schemas import ModalityBreakdown


# Minimum samples before Tier 2 kicks in
MIN_SAMPLES = 10
# Minimum R² for the fit to be trusted
MIN_R_SQUARED = 0.3


@dataclass
class CalibrationFactors:
    """Learned correction factors for a model+archetype pair."""

    slope: float = 1.0
    intercept: float = 0.0
    r_squared: float = 0.0
    sample_count: int = 0
    mape: float = 0.0  # Mean Absolute Percentage Error

    @property
    def is_usable(self) -> bool:
        """Whether calibration has enough data and fit quality."""
        return self.sample_count >= MIN_SAMPLES and self.r_squared >= MIN_R_SQUARED


@dataclass
class ModalityCalibration:
    """Per-modality calibration factors."""

    text_input: CalibrationFactors
    text_output: CalibrationFactors
    total: CalibrationFactors
    sample_count: int = 0


def _fit_linear(
    predicted: np.ndarray,
    actual: np.ndarray,
) -> CalibrationFactors:
    """Fit a simple linear regression: actual = slope * predicted + intercept."""
    n = len(predicted)
    if n < 2:
        return CalibrationFactors(sample_count=n)

    # Filter out zero-predicted to avoid degenerate fits
    mask = predicted > 0
    predicted = predicted[mask]
    actual = actual[mask]
    n = len(predicted)
    if n < 2:
        return CalibrationFactors(sample_count=n)

    # Least squares fit
    x_mean = np.mean(predicted)
    y_mean = np.mean(actual)
    ss_xx = np.sum((predicted - x_mean) ** 2)
    ss_xy = np.sum((predicted - x_mean) * (actual - y_mean))
    ss_yy = np.sum((actual - y_mean) ** 2)

    if ss_xx == 0:
        return CalibrationFactors(sample_count=n)

    slope = float(ss_xy / ss_xx)
    intercept = float(y_mean - slope * x_mean)

    # R²
    if ss_yy == 0:
        r_squared = 1.0 if ss_xx == 0 else 0.0
    else:
        r_squared = float((ss_xy ** 2) / (ss_xx * ss_yy))

    # MAPE
    fitted = slope * predicted + intercept
    nonzero_actual = actual[actual > 0]
    fitted_nonzero = fitted[actual > 0]
    if len(nonzero_actual) > 0:
        mape = float(np.mean(np.abs((nonzero_actual - fitted_nonzero) / nonzero_actual)) * 100)
    else:
        mape = 0.0

    return CalibrationFactors(
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
        sample_count=n,
        mape=mape,
    )


class Calibrator:
    """Tier 2 calibrator that learns from prediction history."""

    def __init__(self, db: HistoryDatabase) -> None:
        self._db = db
        # Cache calibration factors to avoid recomputing per request
        self._cache: dict[tuple[str, str], CalibrationFactors] = {}

    def get_calibration(
        self,
        model: str,
        archetype: str,
        *,
        force_refresh: bool = False,
    ) -> CalibrationFactors:
        """Get or compute calibration factors for a model+archetype pair."""
        key = (model, archetype)
        if not force_refresh and key in self._cache:
            return self._cache[key]

        pairs = self._db.get_calibration_pairs(model, archetype)
        if len(pairs) < 2:
            factors = CalibrationFactors(sample_count=len(pairs))
        else:
            predicted = np.array([p for p, _ in pairs])
            actual = np.array([a for _, a in pairs])
            factors = _fit_linear(predicted, actual)

        self._cache[key] = factors
        return factors

    def has_calibration(self, model: str, archetype: str) -> bool:
        """Check if usable calibration data exists."""
        count = self._db.count_calibration_records(model, archetype)
        if count < MIN_SAMPLES:
            return False
        factors = self.get_calibration(model, archetype)
        return factors.is_usable

    def calibrate_tokens(
        self,
        tokens: ModalityBreakdown,
        model: str,
        archetype: str,
    ) -> Optional[ModalityBreakdown]:
        """Apply Tier 2 calibration to a Tier 1 token estimate.

        Returns calibrated ModalityBreakdown if calibration is available,
        None otherwise (caller should use Tier 1 as-is).
        """
        factors = self.get_calibration(model, archetype)
        if not factors.is_usable:
            return None

        # Also try per-modality calibration for text_input and text_output
        modality_pairs = self._db.get_modality_calibration_pairs(model, archetype)

        # Per-modality regression for the two dominant modalities
        ti_factors = self._fit_modality(modality_pairs, "text_input")
        to_factors = self._fit_modality(modality_pairs, "text_output")

        # Apply calibration
        calibrated = ModalityBreakdown(
            text_input=self._apply(tokens.text_input, ti_factors if ti_factors.is_usable else factors),
            text_output=self._apply(tokens.text_output, to_factors if to_factors.is_usable else factors),
            cached_input=tokens.cached_input,
            image_input=tokens.image_input,  # Image tokens are formula-based, don't calibrate
            image_output=tokens.image_output,
            document_input=tokens.document_input,
            audio_input=tokens.audio_input,
            audio_output=tokens.audio_output,
            reasoning=self._apply(tokens.reasoning, factors) if tokens.reasoning > 0 else 0.0,
        )

        return calibrated

    def clear_cache(self) -> None:
        """Clear the calibration cache."""
        self._cache.clear()

    @staticmethod
    def _apply(value: float, factors: CalibrationFactors) -> float:
        """Apply calibration: actual ≈ slope × predicted + intercept."""
        if value <= 0:
            return value
        calibrated = factors.slope * value + factors.intercept
        # Don't let calibration go negative
        return max(0.0, calibrated)

    def _fit_modality(
        self,
        pairs: list[dict[str, tuple[float, float]]],
        modality: str,
    ) -> CalibrationFactors:
        """Fit regression for a specific modality from paired data."""
        predicted_vals = []
        actual_vals = []
        for entry in pairs:
            if modality in entry:
                p, a = entry[modality]
                predicted_vals.append(p)
                actual_vals.append(a)

        if len(predicted_vals) < 2:
            return CalibrationFactors(sample_count=len(predicted_vals))

        return _fit_linear(np.array(predicted_vals), np.array(actual_vals))

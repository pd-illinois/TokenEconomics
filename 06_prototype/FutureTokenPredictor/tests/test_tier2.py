"""Tests for Tier 2: history database and calibrator."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from future_token_predictor.history.database import HistoryDatabase, PredictionRecord
from future_token_predictor.history.calibrator import (
    Calibrator,
    CalibrationFactors,
    MIN_SAMPLES,
    MIN_R_SQUARED,
    _fit_linear,
)
from future_token_predictor.models.schemas import ModalityBreakdown


# ── Fixtures ──


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database."""
    db_path = tmp_path / "test_history.db"
    db = HistoryDatabase(db_path)
    yield db
    db.close()


@pytest.fixture
def calibrator(tmp_db):
    """Create a calibrator with empty database."""
    return Calibrator(tmp_db)


def _seed_calibration_data(
    db: HistoryDatabase,
    model: str = "gpt-4.1",
    archetype: str = "SingleCall_TextOnly",
    n: int = 20,
    slope: float = 1.2,
    intercept: float = 50.0,
    noise_std: float = 10.0,
    seed: int = 42,
):
    """Insert synthetic predicted/actual pairs with a known linear relationship."""
    rng = np.random.default_rng(seed)
    ids = []
    for _ in range(n):
        predicted_total = float(rng.uniform(500, 5000))
        actual_total = slope * predicted_total + intercept + float(rng.normal(0, noise_std))
        # Per-modality breakdown
        pred_text_in = predicted_total * 0.6
        pred_text_out = predicted_total * 0.4
        actual_text_in = actual_total * 0.55
        actual_text_out = actual_total * 0.45

        record = PredictionRecord(
            model=model,
            provider="openai",
            archetype=archetype,
            predicted_text_input=pred_text_in,
            predicted_text_output=pred_text_out,
            predicted_total=predicted_total,
            predicted_cost=predicted_total * 0.00001,
        )
        pid = db.record_prediction(record)
        db.record_actual(
            pid,
            actual_text_input=actual_text_in,
            actual_text_output=actual_text_out,
            actual_total=actual_total,
            actual_cost=actual_total * 0.00001,
        )
        ids.append(pid)
    return ids


# ── Database Tests ──


class TestHistoryDatabase:
    def test_create_and_retrieve(self, tmp_db):
        record = PredictionRecord(
            model="gpt-4.1",
            provider="openai",
            archetype="SingleCall_TextOnly",
            predicted_text_input=500.0,
            predicted_text_output=300.0,
            predicted_total=800.0,
            predicted_cost=0.008,
        )
        pid = tmp_db.record_prediction(record)
        assert pid > 0

        retrieved = tmp_db.get_record(pid)
        assert retrieved is not None
        assert retrieved.model == "gpt-4.1"
        assert retrieved.predicted_total == 800.0
        assert retrieved.actual_total is None

    def test_record_actual(self, tmp_db):
        record = PredictionRecord(
            model="gpt-4.1",
            provider="openai",
            archetype="ToolAgent",
            predicted_total=5000.0,
            predicted_cost=0.05,
        )
        pid = tmp_db.record_prediction(record)
        status = tmp_db.record_actual(
            pid,
            actual_text_input=2800.0,
            actual_text_output=1500.0,
            actual_total=4300.0,
            actual_cost=0.043,
        )
        assert status == "updated"
        retrieved = tmp_db.get_record(pid)
        assert retrieved.actual_total == 4300.0
        assert retrieved.actual_cost == 0.043

        duplicate_status = tmp_db.record_actual(pid, actual_total=9999.0)
        assert duplicate_status == "already_recorded"
        assert tmp_db.get_record(pid).actual_total == 4300.0

    def test_record_actual_reports_missing_prediction(self, tmp_db):
        assert tmp_db.record_actual(99999, actual_total=100.0) == "not_found"

    def test_auto_calculate_total(self, tmp_db):
        record = PredictionRecord(
            model="gpt-4.1",
            provider="openai",
            archetype="SingleCall_TextOnly",
            predicted_total=1000.0,
            predicted_cost=0.01,
        )
        pid = tmp_db.record_prediction(record)
        tmp_db.record_actual(
            pid,
            actual_text_input=600.0,
            actual_text_output=400.0,
        )
        retrieved = tmp_db.get_record(pid)
        assert retrieved.actual_total == 1000.0  # 600 + 400

    def test_calibration_pairs(self, tmp_db):
        _seed_calibration_data(tmp_db, n=15)
        pairs = tmp_db.get_calibration_pairs("gpt-4.1", "SingleCall_TextOnly")
        assert len(pairs) == 15
        for predicted, actual in pairs:
            assert predicted > 0
            assert actual > 0

    def test_calibration_pairs_filter_model(self, tmp_db):
        _seed_calibration_data(tmp_db, model="gpt-4.1", n=10)
        _seed_calibration_data(tmp_db, model="claude-sonnet-4", n=5)
        pairs_gpt = tmp_db.get_calibration_pairs("gpt-4.1", "SingleCall_TextOnly")
        pairs_claude = tmp_db.get_calibration_pairs("claude-sonnet-4", "SingleCall_TextOnly")
        assert len(pairs_gpt) == 10
        assert len(pairs_claude) == 5

    def test_no_actual_returns_empty_pairs(self, tmp_db):
        record = PredictionRecord(
            model="gpt-4.1",
            provider="openai",
            archetype="SingleCall_TextOnly",
            predicted_total=1000.0,
            predicted_cost=0.01,
        )
        tmp_db.record_prediction(record)
        pairs = tmp_db.get_calibration_pairs("gpt-4.1", "SingleCall_TextOnly")
        assert len(pairs) == 0

    def test_count_calibration_records(self, tmp_db):
        _seed_calibration_data(tmp_db, n=12)
        count = tmp_db.count_calibration_records("gpt-4.1", "SingleCall_TextOnly")
        assert count == 12

    def test_recent_predictions(self, tmp_db):
        for i in range(5):
            record = PredictionRecord(
                model=f"model-{i}",
                provider="openai",
                archetype="SingleCall_TextOnly",
                predicted_total=float(i * 100),
                predicted_cost=0.0,
            )
            tmp_db.record_prediction(record)
        recent = tmp_db.get_recent_predictions(limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0].model == "model-4"

    def test_get_nonexistent_record(self, tmp_db):
        assert tmp_db.get_record(99999) is None

    def test_modality_calibration_pairs(self, tmp_db):
        _seed_calibration_data(tmp_db, n=10)
        pairs = tmp_db.get_modality_calibration_pairs("gpt-4.1", "SingleCall_TextOnly")
        assert len(pairs) == 10
        for entry in pairs:
            assert "text_input" in entry
            assert "text_output" in entry


# ── Linear Regression Tests ──


class TestFitLinear:
    def test_perfect_linear(self):
        predicted = np.array([100, 200, 300, 400, 500], dtype=float)
        actual = np.array([120, 240, 360, 480, 600], dtype=float)  # y = 1.2x
        factors = _fit_linear(predicted, actual)
        assert abs(factors.slope - 1.2) < 0.001
        assert abs(factors.intercept) < 0.001
        assert factors.r_squared > 0.999
        assert factors.sample_count == 5

    def test_with_intercept(self):
        predicted = np.array([100, 200, 300, 400, 500], dtype=float)
        actual = np.array([150, 250, 350, 450, 550], dtype=float)  # y = x + 50
        factors = _fit_linear(predicted, actual)
        assert abs(factors.slope - 1.0) < 0.001
        assert abs(factors.intercept - 50.0) < 0.001
        assert factors.r_squared > 0.999

    def test_noisy_data(self):
        rng = np.random.default_rng(42)
        predicted = rng.uniform(100, 1000, 50)
        actual = 1.15 * predicted + 30 + rng.normal(0, 20, 50)
        factors = _fit_linear(predicted, actual)
        assert abs(factors.slope - 1.15) < 0.05
        assert factors.r_squared > 0.9
        assert factors.mape < 10

    def test_single_point(self):
        factors = _fit_linear(np.array([100.0]), np.array([120.0]))
        assert factors.sample_count == 1
        assert not factors.is_usable

    def test_empty(self):
        factors = _fit_linear(np.array([]), np.array([]))
        assert factors.sample_count == 0
        assert not factors.is_usable

    def test_zeros_filtered(self):
        predicted = np.array([0, 0, 100, 200, 300], dtype=float)
        actual = np.array([50, 60, 120, 240, 360], dtype=float)
        factors = _fit_linear(predicted, actual)
        assert factors.sample_count == 3  # Only non-zero predicted used


# ── Calibrator Tests ──


class TestCalibrator:
    def test_no_data_returns_none(self, tmp_db, calibrator):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        result = calibrator.calibrate_tokens(tokens, "gpt-4.1", "SingleCall_TextOnly")
        assert result is None

    def test_insufficient_data_returns_none(self, tmp_db, calibrator):
        _seed_calibration_data(tmp_db, n=5)  # Less than MIN_SAMPLES
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        result = calibrator.calibrate_tokens(tokens, "gpt-4.1", "SingleCall_TextOnly")
        assert result is None

    def test_calibration_with_enough_data(self, tmp_db, calibrator):
        # Seed with actual > predicted (slope=1.2, intercept=50)
        _seed_calibration_data(tmp_db, n=25, slope=1.2, intercept=50.0, noise_std=5.0)

        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        result = calibrator.calibrate_tokens(tokens, "gpt-4.1", "SingleCall_TextOnly")

        # Calibration should increase tokens (actuals are higher)
        assert result is not None
        assert result.total > tokens.total

    def test_has_calibration(self, tmp_db, calibrator):
        assert not calibrator.has_calibration("gpt-4.1", "SingleCall_TextOnly")
        _seed_calibration_data(tmp_db, n=25, slope=1.2, intercept=50.0, noise_std=5.0)
        calibrator.clear_cache()
        assert calibrator.has_calibration("gpt-4.1", "SingleCall_TextOnly")

    def test_calibration_factors_accuracy(self, tmp_db, calibrator):
        _seed_calibration_data(tmp_db, n=50, slope=1.3, intercept=100.0, noise_std=5.0)
        factors = calibrator.get_calibration("gpt-4.1", "SingleCall_TextOnly")
        assert abs(factors.slope - 1.3) < 0.05
        assert abs(factors.intercept - 100.0) < 30.0
        assert factors.r_squared > 0.95
        assert factors.is_usable

    def test_different_archetypes_independent(self, tmp_db, calibrator):
        _seed_calibration_data(
            tmp_db, archetype="SingleCall_TextOnly", n=20, slope=1.1, noise_std=5.0
        )
        _seed_calibration_data(
            tmp_db, archetype="ToolAgent", n=20, slope=1.5, noise_std=5.0
        )
        f1 = calibrator.get_calibration("gpt-4.1", "SingleCall_TextOnly")
        f2 = calibrator.get_calibration("gpt-4.1", "ToolAgent")
        # Slopes should be different
        assert abs(f1.slope - f2.slope) > 0.2

    def test_cache_works(self, tmp_db, calibrator):
        _seed_calibration_data(tmp_db, n=20)
        f1 = calibrator.get_calibration("gpt-4.1", "SingleCall_TextOnly")
        f2 = calibrator.get_calibration("gpt-4.1", "SingleCall_TextOnly")
        assert f1 is f2  # Same object from cache

    def test_clear_cache(self, tmp_db, calibrator):
        _seed_calibration_data(tmp_db, n=20)
        f1 = calibrator.get_calibration("gpt-4.1", "SingleCall_TextOnly")
        calibrator.clear_cache()
        f2 = calibrator.get_calibration("gpt-4.1", "SingleCall_TextOnly")
        assert f1 is not f2  # Different object after cache clear
        assert abs(f1.slope - f2.slope) < 0.001  # Same values

    def test_calibrated_tokens_non_negative(self, tmp_db, calibrator):
        # Even with weird data, tokens should never go negative
        _seed_calibration_data(tmp_db, n=20, slope=0.5, intercept=-100.0, noise_std=5.0)
        tokens = ModalityBreakdown(text_input=50, text_output=30)
        result = calibrator.calibrate_tokens(tokens, "gpt-4.1", "SingleCall_TextOnly")
        if result is not None:
            assert result.text_input >= 0
            assert result.text_output >= 0

    def test_image_tokens_not_calibrated(self, tmp_db, calibrator):
        _seed_calibration_data(tmp_db, n=25, slope=1.3, intercept=50.0, noise_std=5.0)
        tokens = ModalityBreakdown(
            text_input=1000, text_output=500, image_input=765.0
        )
        result = calibrator.calibrate_tokens(tokens, "gpt-4.1", "SingleCall_TextOnly")
        assert result is not None
        # Image tokens are formula-based — should NOT be calibrated
        assert result.image_input == 765.0


# ── Integration with Predictor ──


class TestPredictorTier2Integration:
    def test_predict_records_to_history(self, tmp_path):
        from future_token_predictor import predict

        db_path = str(tmp_path / "test.db")
        result = predict(
            description="Simple GPT-4.1 text prompt",
            db_path=db_path,
        )
        assert result.prediction_method == "tier1_heuristic"
        assert result.prediction_id is not None

        # Verify it was recorded
        db = HistoryDatabase(db_path)
        recent = db.get_recent_predictions(limit=1)
        assert len(recent) == 1
        assert recent[0].model == result.model
        assert recent[0].predicted_total > 0
        db.close()

    def test_predict_uses_tier2_when_available(self, tmp_path):
        from future_token_predictor import predict

        db_path = str(tmp_path / "test.db")
        db = HistoryDatabase(db_path)

        # Seed calibration data for gpt-4.1 + SingleCall_TextOnly
        _seed_calibration_data(
            db, model="gpt-4.1", archetype="SingleCall_TextOnly",
            n=25, slope=1.3, intercept=50.0, noise_std=5.0,
        )
        db.close()

        result = predict(
            description="Simple GPT-4.1 text chatbot",
            db_path=db_path,
        )
        assert result.prediction_method == "tier2_calibrated"

    def test_predict_tier2_disabled(self, tmp_path):
        from future_token_predictor import predict

        db_path = str(tmp_path / "test.db")
        db = HistoryDatabase(db_path)
        _seed_calibration_data(
            db, model="gpt-4.1", archetype="SingleCall_TextOnly",
            n=25, slope=1.3, intercept=50.0, noise_std=5.0,
        )
        db.close()

        result = predict(
            description="Simple GPT-4.1 text chatbot",
            db_path=db_path,
            enable_tier2=False,
        )
        assert result.prediction_method == "tier1_heuristic"

    def test_record_actual_api(self, tmp_path):
        from future_token_predictor import record_actual

        db_path = str(tmp_path / "test.db")
        db = HistoryDatabase(db_path)
        record = PredictionRecord(
            model="gpt-4.1",
            provider="openai",
            archetype="SingleCall_TextOnly",
            predicted_total=1000.0,
            predicted_cost=0.01,
        )
        pid = db.record_prediction(record)
        db.close()

        outcome = record_actual(
            pid,
            actual_text_input=600,
            actual_text_output=500,
            db_path=db_path,
        )
        assert outcome.prediction_id == pid
        assert outcome.status == "updated"

        db = HistoryDatabase(db_path)
        retrieved = db.get_record(pid)
        assert retrieved.actual_total == 1100.0
        db.close()

    def test_record_actual_api_rejects_invalid_values(self, tmp_path):
        from future_token_predictor import record_actual

        with pytest.raises(ValueError, match="actual_total"):
            record_actual(1, actual_total=-1, db_path=str(tmp_path / "test.db"))

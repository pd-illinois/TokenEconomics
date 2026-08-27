"""SQLite storage for prediction history — supports Tier 2 calibration.

Stores predicted vs actual token usage so the calibrator can learn
correction factors per model+archetype combination.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Default DB location: ~/.future_token_predictor/history.db
_DEFAULT_DB_DIR = Path.home() / ".future_token_predictor"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,

    -- Keys for calibration grouping
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    archetype TEXT NOT NULL,
    complexity TEXT NOT NULL DEFAULT 'medium',

    -- Predicted tokens (from Tier 1)
    predicted_text_input REAL NOT NULL DEFAULT 0,
    predicted_text_output REAL NOT NULL DEFAULT 0,
    predicted_image_input REAL NOT NULL DEFAULT 0,
    predicted_document_input REAL NOT NULL DEFAULT 0,
    predicted_audio_input REAL NOT NULL DEFAULT 0,
    predicted_reasoning REAL NOT NULL DEFAULT 0,
    predicted_total REAL NOT NULL DEFAULT 0,
    predicted_cost REAL NOT NULL DEFAULT 0,

    -- Actual tokens (recorded after execution)
    actual_text_input REAL,
    actual_text_output REAL,
    actual_image_input REAL,
    actual_document_input REAL,
    actual_audio_input REAL,
    actual_reasoning REAL,
    actual_total REAL,
    actual_cost REAL,

    -- Metadata
    prediction_method TEXT NOT NULL DEFAULT 'tier1_heuristic',
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_calibration_key
    ON predictions(model, archetype);

CREATE INDEX IF NOT EXISTS idx_model
    ON predictions(model);
"""


@dataclass
class PredictionRecord:
    """A single prediction + optional actual outcome."""

    id: Optional[int] = None
    created_at: Optional[str] = None

    model: str = ""
    provider: str = ""
    archetype: str = ""
    complexity: str = "medium"

    predicted_text_input: float = 0.0
    predicted_text_output: float = 0.0
    predicted_image_input: float = 0.0
    predicted_document_input: float = 0.0
    predicted_audio_input: float = 0.0
    predicted_reasoning: float = 0.0
    predicted_total: float = 0.0
    predicted_cost: float = 0.0

    actual_text_input: Optional[float] = None
    actual_text_output: Optional[float] = None
    actual_image_input: Optional[float] = None
    actual_document_input: Optional[float] = None
    actual_audio_input: Optional[float] = None
    actual_reasoning: Optional[float] = None
    actual_total: Optional[float] = None
    actual_cost: Optional[float] = None

    prediction_method: str = "tier1_heuristic"
    description: Optional[str] = None


class HistoryDatabase:
    """Thread-safe SQLite store for prediction history."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            self._db_path = _DEFAULT_DB_PATH
        else:
            self._db_path = Path(db_path)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    def record_prediction(self, record: PredictionRecord) -> int:
        """Store a prediction. Returns the row ID."""
        now = record.created_at or datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO predictions (
                created_at, model, provider, archetype, complexity,
                predicted_text_input, predicted_text_output,
                predicted_image_input, predicted_document_input,
                predicted_audio_input, predicted_reasoning,
                predicted_total, predicted_cost,
                prediction_method, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                record.model,
                record.provider,
                record.archetype,
                record.complexity,
                record.predicted_text_input,
                record.predicted_text_output,
                record.predicted_image_input,
                record.predicted_document_input,
                record.predicted_audio_input,
                record.predicted_reasoning,
                record.predicted_total,
                record.predicted_cost,
                record.prediction_method,
                record.description,
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def record_actual(
        self,
        prediction_id: int,
        *,
        actual_text_input: float = 0.0,
        actual_text_output: float = 0.0,
        actual_image_input: float = 0.0,
        actual_document_input: float = 0.0,
        actual_audio_input: float = 0.0,
        actual_reasoning: float = 0.0,
        actual_total: Optional[float] = None,
        actual_cost: Optional[float] = None,
    ) -> str:
        """Record actual usage once and return updated/already_recorded/not_found."""
        if actual_total is None:
            actual_total = (
                actual_text_input
                + actual_text_output
                + actual_image_input
                + actual_document_input
                + actual_audio_input
                + actual_reasoning
            )
        conn = self._get_conn()
        cursor = conn.execute(
            """UPDATE predictions SET
                actual_text_input = ?,
                actual_text_output = ?,
                actual_image_input = ?,
                actual_document_input = ?,
                actual_audio_input = ?,
                actual_reasoning = ?,
                actual_total = ?,
                actual_cost = ?
            WHERE id = ? AND actual_total IS NULL""",
            (
                actual_text_input,
                actual_text_output,
                actual_image_input,
                actual_document_input,
                actual_audio_input,
                actual_reasoning,
                actual_total,
                actual_cost,
                prediction_id,
            ),
        )
        conn.commit()
        if cursor.rowcount == 1:
            return "updated"

        row = conn.execute(
            "SELECT actual_total FROM predictions WHERE id = ?", (prediction_id,)
        ).fetchone()
        return "already_recorded" if row is not None else "not_found"

    def get_calibration_pairs(
        self,
        model: str,
        archetype: str,
        *,
        limit: int = 500,
    ) -> list[tuple[float, float]]:
        """Get (predicted_total, actual_total) pairs for calibration.

        Only returns records where actual_total is not NULL.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT predicted_total, actual_total
            FROM predictions
            WHERE model = ? AND archetype = ? AND actual_total IS NOT NULL
            ORDER BY created_at DESC
            LIMIT ?""",
            (model, archetype, limit),
        ).fetchall()
        return [(row["predicted_total"], row["actual_total"]) for row in rows]

    def get_modality_calibration_pairs(
        self,
        model: str,
        archetype: str,
        *,
        limit: int = 500,
    ) -> list[dict[str, tuple[float, float]]]:
        """Get per-modality (predicted, actual) pairs for calibration.

        Returns list of dicts with keys: text_input, text_output, etc.
        Each value is (predicted, actual).
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT
                predicted_text_input, actual_text_input,
                predicted_text_output, actual_text_output,
                predicted_image_input, actual_image_input,
                predicted_document_input, actual_document_input,
                predicted_audio_input, actual_audio_input,
                predicted_reasoning, actual_reasoning
            FROM predictions
            WHERE model = ? AND archetype = ? AND actual_total IS NOT NULL
            ORDER BY created_at DESC
            LIMIT ?""",
            (model, archetype, limit),
        ).fetchall()

        result = []
        for row in rows:
            entry = {}
            for modality in [
                "text_input", "text_output", "image_input",
                "document_input", "audio_input", "reasoning",
            ]:
                predicted = row[f"predicted_{modality}"]
                actual = row[f"actual_{modality}"]
                if predicted is not None and actual is not None:
                    entry[modality] = (predicted, actual)
            result.append(entry)
        return result

    def get_record(self, prediction_id: int) -> Optional[PredictionRecord]:
        """Retrieve a single prediction record."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM predictions WHERE id = ?",
            (prediction_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def count_calibration_records(self, model: str, archetype: str) -> int:
        """Count records with actuals for a model+archetype pair."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*) as cnt
            FROM predictions
            WHERE model = ? AND archetype = ? AND actual_total IS NOT NULL""",
            (model, archetype),
        ).fetchone()
        return row["cnt"]

    def get_recent_predictions(
        self, *, limit: int = 20
    ) -> list[PredictionRecord]:
        """Get recent predictions (for inspection/debugging)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PredictionRecord:
        return PredictionRecord(
            id=row["id"],
            created_at=row["created_at"],
            model=row["model"],
            provider=row["provider"],
            archetype=row["archetype"],
            complexity=row["complexity"],
            predicted_text_input=row["predicted_text_input"],
            predicted_text_output=row["predicted_text_output"],
            predicted_image_input=row["predicted_image_input"],
            predicted_document_input=row["predicted_document_input"],
            predicted_audio_input=row["predicted_audio_input"],
            predicted_reasoning=row["predicted_reasoning"],
            predicted_total=row["predicted_total"],
            predicted_cost=row["predicted_cost"],
            actual_text_input=row["actual_text_input"],
            actual_text_output=row["actual_text_output"],
            actual_image_input=row["actual_image_input"],
            actual_document_input=row["actual_document_input"],
            actual_audio_input=row["actual_audio_input"],
            actual_reasoning=row["actual_reasoning"],
            actual_total=row["actual_total"],
            actual_cost=row["actual_cost"],
            prediction_method=row["prediction_method"],
            description=row["description"],
        )

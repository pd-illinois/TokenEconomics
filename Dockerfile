FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOKENECONOMICS_HOST=0.0.0.0 \
    TOKENECONOMICS_PORT=8765 \
    TOKENECONOMICS_STATE_ROOT=/data \
    TOKENECONOMICS_FOUNDRY_ONLY=true

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY FutureTokenPredictor /app/FutureTokenPredictor
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/requirements.txt \
    && python -m pip install --no-cache-dir /app/FutureTokenPredictor

COPY . /app

RUN mkdir -p /app/seed \
    && for path in \
        studio_billing_evidence \
        studio_decision_state \
        studio_governance_evidence \
        studio_learning_evidence \
        studio_lifecycle \
        studio_plans \
        studio_policy_changes \
        studio_portability_evidence \
        studio_reconciliation_evidence \
        studio_reports \
        studio_response_learning \
        studio_runs; do \
         if [ -d "/app/$path" ]; then mv "/app/$path" "/app/seed/$path"; fi; \
       done \
    && sed -i 's/\r$//' /app/scripts/container-entrypoint.sh \
    && chmod +x /app/scripts/container-entrypoint.sh \
    && test -s /app/TokEcoStudio.png \
    && python -m compileall -q /app/studio.py /app/costgov /app/rag /app/scripts \
    && python -c "import studio; import rag.batch_feedback"

EXPOSE 8765

ENTRYPOINT ["/app/scripts/container-entrypoint.sh"]

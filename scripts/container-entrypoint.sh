#!/bin/sh
set -eu

state_root="${TOKENECONOMICS_STATE_ROOT:-/data}"

for path in \
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
  studio_runs
do
  mkdir -p "$state_root/$path"
  if [ -d "/app/seed/$path" ] && [ -z "$(ls -A "$state_root/$path")" ]; then
    cp -a "/app/seed/$path/." "$state_root/$path/"
  fi
  if [ ! -e "/app/$path" ]; then
    ln -s "$state_root/$path" "/app/$path"
  fi
done

exec python /app/studio.py

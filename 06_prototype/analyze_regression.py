#!/usr/bin/env python
import json
from pathlib import Path
from collections import defaultdict

p = Path('studio_reports/enterprise_corpus_run_20260728-124600.json')
data = json.loads(p.read_text(encoding='utf-8'))
results = data['results']

print('=== REGRESSION RUN SUMMARY ===')
print(f'Total cases: {len(results)}')
print(f'Cases completed: {sum(1 for r in results if r["status"]=="complete")}')
print(f'Cases failed: {sum(1 for r in results if r["status"]!="complete")}')
print()

# Check for cardinality issues
bad_cardinality = [r for r in results if r.get('report_plan_count') != 1 or r.get('report_receipt_count') != 1]
print(f'Bad cardinality (not 1 plan/1 receipt): {len(bad_cardinality)}')
if bad_cardinality:
    for r in bad_cardinality:
        print(f'  {r["case_id"]}: {r["report_plan_count"]} plans, {r["report_receipt_count"]} receipts')
print()

# Model distribution
model_dist = defaultdict(int)
provider_dist = defaultdict(int)
for r in results:
    model_dist[f"{r['provider']}:{r['model']}"] += 1
    provider_dist[r['provider']] += 1

print(f'Unique provider:model pairs: {len(model_dist)}')
print(f'Providers used: {sorted(provider_dist.keys())}')
print()

# Cost analysis
costs = [r['monthly_model_cost_mean'] for r in results if r['monthly_model_cost_mean'] is not None]
print(f'Cost range: ${min(costs):.2f} - ${max(costs):,.2f} per month')
print(f'Median cost: ${sorted(costs)[len(costs)//2]:,.2f}')
print()

# Topology and archetype distribution
topology_dist = defaultdict(int)
archetype_dist = defaultdict(int)
confidence_dist = defaultdict(int)
for r in results:
    topology_dist[r['analysis_topology']] += 1
    archetype_dist[r['archetype']] += 1
    confidence_dist[r.get('analysis_confidence', 'unknown')] += 1

print('Analysis topologies:')
for topo, count in sorted(topology_dist.items(), key=lambda x: -x[1]):
    print(f'  {topo}: {count}')
print()

print('Archetypes:')
for arch, count in sorted(archetype_dist.items(), key=lambda x: -x[1]):
    print(f'  {arch}: {count}')
print()

print('Confidence levels:')
for conf, count in sorted(confidence_dist.items(), key=lambda x: -x[1]):
    print(f'  {conf}: {count}')

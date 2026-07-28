# TokenEconomics

Per-token model prices have fallen dramatically (the Stanford AI Index reports a
~280× drop for a fixed capability level), yet agent bills keep climbing. The reason
is that agents don't make one cheap call — they run long, branching, stochastic
**trajectories**, and the Stanford Digital Economy Lab has measured up to ~30× cost
variance for the same coding task. Cheaper tokens can still mean more expensive work.

TokenEconomics reframes the problem around the only unit that maps to business value:
**cost per accepted task**, not cost per token.

$$U(\pi) = \frac{\mathbb{E}[C_{task}\mid\pi]}{P(A=1\mid\pi)}$$

A policy $\pi$ is chosen to minimize expected cost per accepted task, subject to a
per-segment quality floor $Q_s(\pi) \ge Q_{min}$ and a tail-risk budget
$P(C_{task} > B) \le \varepsilon$. The discipline runs as a loop:
**Forecast → Select → Enforce → Evaluate → Revert → Reconcile**, split across two planes:

- **FutureTokenPredictor** — a *feed-forward* planner that predicts a workload's cost
  and quality distribution (modeled P50/P95) **before** execution, outside the request path.
- **TokenGov** — a *feedback* runtime governor that admits, routes, caps, and reconciles
  spend **during** execution.

The approach maps onto existing Azure primitives (App Configuration, API Management,
Foundry evaluation, Azure Monitor, Functions, Cost Management) rather than a new
proprietary stack. These are research prototypes, not launched products, and no
guaranteed savings or quality are claimed.

## v1 release scope

**The v1 release only contains prediction, powered by FutureTokenPredictor.**

The Studio UI ships the five-view shell (Plan → Govern → Runs → Observe → Reconcile),
but **only Plan is interactive in v1** — it calls FutureTokenPredictor to produce
feed-forward cost/quality predictions. Govern, Runs, Observe, and Reconcile are
read-only previews and are planned for v2 (release gate `TE-001.5`, surface
`studio-plan-readonly`).

### Coming in v2

We will soon release **TokenGov**, the feedback runtime governor that provides policy
and governance control built on Azure infrastructure — admitting, routing, capping,
and reconciling agent spend during execution, and activating the Govern, Runs, Observe,
and Reconcile views.


## Run it locally

Prerequisites: **Python 3.11+**, **git**, and PowerShell (commands below are for Windows).

### 1. Clone the repository

```powershell
git clone <your-repo-url> TokenEconomics
cd TokenEconomics
```

### 2. Create the environment and install FutureTokenPredictor

```powershell
cd 06_prototype
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".\FutureTokenPredictor[dev]"
```

### 3. (Optional) verify the predictor

```powershell
.\.venv\Scripts\python.exe .\FutureTokenPredictor\scripts\run_tests.py --all --expect pass
```

### 4. Start the Studio server

```powershell
.\.venv\Scripts\python.exe .\studio.py
```

Then open <http://127.0.0.1:8765> and use the **Plan** view to run a prediction.

More prototype detail (demos, Azure policy deploy) lives in
[`06_prototype/README.md`](06_prototype/README.md).

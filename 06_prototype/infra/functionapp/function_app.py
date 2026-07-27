"""
Azure Function: eval->enforcement binding (the control-plane "hands").

Productionizes costgov/decision.py. Fired by a Monitor alert (or called directly with an
eval report); reads the current knobs from Azure App Configuration, applies the same
worst-segment ladder logic with hysteresis, and WRITES the new knobs back to App Config.
The data plane (APIM / gateway) reads those knobs — closing the loop with no code deploy.

Auth: the Function's system-assigned managed identity (App Configuration Data Owner).
"""
import json
import os
import logging
import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.appconfiguration import AzureAppConfigurationClient, ConfigurationSetting

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# Ordered cheapest/riskiest -> safest (mirrors costgov/decision.py).
LADDER = ["cost", "balanced", "quality"]


def _client() -> AzureAppConfigurationClient:
    return AzureAppConfigurationClient(
        base_url=os.environ["AZURE_APPCONFIG_ENDPOINT"],
        credential=DefaultAzureCredential())


def _get(client, key, default):
    try:
        return client.get_configuration_setting(key=key).value
    except Exception:
        return default


def _set(client, key, value):
    client.set_configuration_setting(ConfigurationSetting(key=key, value=str(value)))


@app.route(route="decide", methods=["POST"])
def decide(req: func.HttpRequest) -> func.HttpResponse:
    """Body: {"eval_report": {"mean_score": x, "by_difficulty": {...}}, "tenant_spend_ok": bool}"""
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("expected JSON body", status_code=400)

    report = body.get("eval_report", {})
    by_diff = report.get("by_difficulty") or {}
    mean = float(report.get("mean_score", 1.0))
    spend_ok = bool(body.get("tenant_spend_ok", True))

    client = _client()
    floor = float(_get(client, "evaluation:min_quality", "0.8"))
    mode = _get(client, "routing:mode", "balanced")
    thr = float(_get(client, "semantic_cache:score_threshold", "0.83"))

    # Gate on the WORST segment, not the mean (a good aggregate can hide a collapsed segment).
    worst = min(by_diff.values()) if by_diff else mean
    worst_seg = min(by_diff, key=by_diff.get) if by_diff else "overall"
    idx = LADDER.index(mode) if mode in LADDER else 1
    actions = []

    if worst < floor:
        if idx < len(LADDER) - 1:
            new = LADDER[idx + 1]
            _set(client, "routing:mode", new)
            actions.append(f"REVERT: routing {mode} -> {new} ('{worst_seg}' {worst} < floor {floor})")
        if thr < 0.9:
            newthr = round(thr + 0.05, 2)
            _set(client, "semantic_cache:score_threshold", newthr)
            actions.append(f"TIGHTEN: cache threshold {thr} -> {newthr}")
    else:
        headroom = worst - floor
        if headroom > 0.1 and idx > 0 and spend_ok:
            new = LADDER[idx - 1]
            _set(client, "routing:mode", new)
            actions.append(f"OPTIMIZE: routing {mode} -> {new} (worst-segment headroom {round(headroom, 3)})")

    if not actions:
        actions.append("HOLD: all segments within band, no change")

    logging.info("decision binding actions: %s", actions)
    return func.HttpResponse(json.dumps({"actions": actions}), mimetype="application/json")

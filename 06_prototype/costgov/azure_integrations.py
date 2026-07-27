"""
azure_integrations.py — legacy simulated-demo integrations.

These helpers are not the TokenGov policy authority used by Studio. Studio loads one
versioned policy document through policy_store.py and fails closed. All Azure imports
here are lazy.

  - hydrate_config_from_appconfig(): pull the cost knobs from Azure App Configuration
    (the real CONFIG STORE) instead of config.json.
  - init_app_insights(): route telemetry to Application Insights via OpenTelemetry.

Real embeddings for the semantic cache are wired in demo.py (needs an embeddings
deployment); see LIVE.md.
"""

from __future__ import annotations
import logging
import os


def hydrate_config_from_appconfig(config: dict) -> dict:
    """Overlay knobs from App Configuration onto the given config dict, if configured.

    Keys use a flat 'costgov:' prefix, e.g. costgov:routing.mode = balanced.
    Returns the (possibly updated) config; no-op if AZURE_APPCONFIG_ENDPOINT is unset.
    """
    endpoint = os.environ.get("AZURE_APPCONFIG_ENDPOINT")
    if not endpoint:
        return config
    from azure.appconfiguration import AzureAppConfigurationClient
    from azure.identity import DefaultAzureCredential

    client = AzureAppConfigurationClient(endpoint, DefaultAzureCredential())
    for setting in client.list_configuration_settings(key_filter="costgov:*"):
        path = setting.key.split("costgov:", 1)[1].split(".")   # e.g. ['routing','mode']
        node = config
        for k in path[:-1]:
            node = node.setdefault(k, {})
        # coerce simple types
        val = setting.value
        for cast in (int, float):
            try:
                val = cast(setting.value); break
            except (ValueError, TypeError):
                pass
        if val in ("true", "false"):
            val = (val == "true")
        node[path[-1]] = val
    print(f"  [appconfig] knobs hydrated from {endpoint}")
    return config


def push_knob_to_appconfig(path_keys, value) -> bool:
    """Reject legacy flat-key writes that bypass policy review and admission."""
    del path_keys, value
    raise RuntimeError(
        "unrestricted App Configuration knob writes are disabled; "
        "publish a reviewed TokenGov policy revision instead"
    )


def init_app_insights() -> bool:
    """Wire OpenTelemetry -> Application Insights if the connection string is present.
    Also flips telemetry.EMIT so each request is shipped as a 'costgov.request' record
    (queried by dashboard/workbook.json). Returns True if enabled."""
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(logger_name="costgov")
    logging.getLogger("costgov").setLevel(logging.INFO)
    from . import telemetry
    telemetry.EMIT = True
    print("  [appinsights] telemetry export enabled (per-request -> customDimensions)")
    return True

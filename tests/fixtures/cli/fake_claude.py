"""Synthetic application capability reports. This is not a native CLI protocol."""
import json
import sys

provider = "claude"
scenario = sys.argv[1] if len(sys.argv) > 1 else "healthy"
if scenario == "malformed":
    print("not JSON")
    raise SystemExit(0)
report = {
    "schema_version": 1,
    "provider": provider,
    "tool_profile": "restricted_read_write" if provider == "claude" else "workspace_exec",
    "streaming": scenario != "no_stream",
    "cancellation": scenario != "no_cancel",
    "filesystem_confinement": scenario != "unconfined",
    "network_behavior": "unverified" if scenario == "network_unknown" else "provider_transport_only",
    "credential_inheritance": "inherited" if scenario == "credentials_inherited" else "cli_native_only",
}
print(json.dumps(report))


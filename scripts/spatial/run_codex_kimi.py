"""Run one isolated Codex CLI task with the inventory's K3.1 provider."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from private_config import load_kimi_config, redact

parser = argparse.ArgumentParser()
parser.add_argument("prompt", type=Path)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--sandbox", choices=["workspace-write", "danger-full-access"], default="workspace-write", help="Sandbox for this isolated CLI run only; does not change user defaults")
parser.add_argument("--reasoning", choices=["low", "medium", "high"], default="high")
args = parser.parse_args()
config = load_kimi_config()
root = Path(__file__).resolve().parents[2]
env = os.environ.copy()
env["ARK_KIMI_API_KEY"] = config["api_key"]
provider = {"name": "Ark Kimi experiment", "base_url": config["base_url"], "env_key": "ARK_KIMI_API_KEY", "wire_api": "responses", "request_max_retries": 1, "stream_max_retries": 1, "stream_idle_timeout_ms": 300000}
provider_toml = "{" + ", ".join(k + "=" + json.dumps(v) for k, v in provider.items()) + "}"
command = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--json", "-C", str(root), "-s", args.sandbox, "-c", 'approval_policy="never"', "-c", 'model_provider="ark-kimi-experiment"', "-c", "model_providers.ark-kimi-experiment=" + provider_toml, "-m", config["model"], "-c", 'model_reasoning_effort=' + json.dumps(args.reasoning), "-"]
# TOML inline tables use =, JSON string escaping remains compatible here.
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open("w") as log:
    p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    p.stdin.write(args.prompt.read_text())
    p.stdin.close()
    for line in p.stdout:
        safe = redact(line, config)
        log.write(safe)
        log.flush()
        print(safe, end="", flush=True)
    code = p.wait()
print("Codex K3.1 exit:", code)
raise SystemExit(code)

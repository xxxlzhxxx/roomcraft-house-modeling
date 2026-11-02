"""Resolve the experiment's model in memory; never persist credentials/endpoints."""
import os
import re
from pathlib import Path


def load_kimi_config():
    base = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model = os.environ.get("ARK_KIMI_MODEL")
    key = os.environ.get("ARK_KIMI_API_KEY")
    if not (model and key):
        inventory = Path(os.environ.get("LLM_INVENTORY_FILE", str(Path.home() / "WorkSpace/LLM_env.md")))
        lines = inventory.read_text().splitlines()
        index = next((i for i, line in enumerate(lines) if line.strip().lower().strip("`# ") in {"k3.1", "kimi k3.1", "kimi-k3.1-pro"}), None)
        if index is None:
            raise RuntimeError("K3.1 is missing from the private model inventory")
        match = re.search(r"ep-[\w-]+", "\n".join(lines[index + 1:index + 5]))
        keys = re.findall(r"(?im)API\s*key\s*[:：]\s*([^\s`]+)", "\n".join(lines[:index]))
        if not match or not keys:
            raise RuntimeError("K3.1 private configuration is incomplete")
        model = model or match.group(0)
        key = key or keys[-1]
    return {"base_url": base, "model": model, "api_key": key}


def redact(text, config):
    for name in ("api_key", "model"):
        text = text.replace(config[name], "[PRIVATE]")
    text = re.sub(r"ep-[\w-]+", "[PRIVATE_ENDPOINT]", text)
    return text

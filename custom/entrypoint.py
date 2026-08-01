#!/usr/bin/env python3
import os
import subprocess
import sys

CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
if CUSTOM_DIR not in sys.path:
    sys.path.insert(0, CUSTOM_DIR)

# Register KiroProvider FIRST, before litellm loads any config
import litellm
from litellm.utils import custom_llm_setup
from kiro.provider import KiroProvider

litellm.custom_provider_map = [
    {"provider": "kiro", "custom_handler": KiroProvider()},
]
custom_llm_setup()
print("[Kiro] Custom provider registered", file=sys.stderr)


def run_prisma_migration() -> None:
    repo_root = os.path.dirname(CUSTOM_DIR)
    migration_script = os.path.join(repo_root, "litellm", "proxy", "prisma_migration.py")
    venv_python = os.path.join(repo_root, ".venv", "bin", "python")
    python_bin = venv_python if (os.path.isfile(venv_python) and os.access(venv_python, os.X_OK)) else sys.executable
    subprocess.run([python_bin, migration_script], check=True)
    print("Migration script ran successfully!")


def run_sso_flow() -> "RefreshDaemon | None":
    from aws_sso.config import SSOConfig
    from aws_sso.device_auth import DeviceAuthFlow
    from aws_sso.logger import SSOResponseLogger
    from aws_sso.refresh import RefreshDaemon

    config = SSOConfig.from_env()
    if not config.enabled:
        return None

    print("[AWS SSO] Initializing...", file=sys.stderr)
    logger = SSOResponseLogger(config.log_dir)
    device_auth = DeviceAuthFlow(config, logger)

    if not device_auth.load_from_store():
        device_auth.run_interactive()

    os.environ["KIRO_SSO_TOKEN_FILE"] = config.token_store_path

    daemon = RefreshDaemon(config, logger, device_auth)
    daemon.start()
    return daemon


def generate_dynamic_config(token_file: str) -> str:
    import json
    import yaml
    from kiro.provider import fetch_available_models

    dynamic_path = "/tmp/litellm-dynamic-config.yaml"
    region = os.environ.get("KIRO_REGION", "us-east-1")
    master_key = os.environ.get("LITELLM_MASTER_KEY", "sk-1234")

    try:
        access_token = json.loads(open(token_file).read()).get("access_token", "")
    except Exception:
        access_token = ""

    if not access_token:
        return "/app/config.yaml"

    models = fetch_available_models(access_token, region)
    model_list = [
        {"model_name": f"kiro-{m['modelId']}", "litellm_params": {"model": f"kiro/{m['modelId']}"}}
        for m in models
    ]

    config = {
        "model_list": model_list,
        "general_settings": {"master_key": master_key},
        "litellm_settings": {"drop_params": True, "telemetry": False},
    }
    with open(dynamic_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"[Kiro] Dynamic config written with {len(model_list)} models", file=sys.stderr)
    return dynamic_path


def start_proxy(config_path: str) -> None:
    """Start LiteLLM proxy in this same process so daemon threads stay alive."""
    # Build sys.argv that proxy_cli expects
    args = ["litellm", "--config", config_path, "--port", "4000"]
    # Append any extra args passed to this script (skip script name)
    for arg in sys.argv[1:]:
        if arg not in args:
            args.append(arg)
    sys.argv = args

    from litellm.proxy.proxy_cli import run_server
    run_server()


def main() -> None:
    try:
        run_prisma_migration()
    except Exception as e:
        print(f"Warning: Prisma migration failed: {e}", file=sys.stderr)

    config_path = "/app/config.yaml"
    try:
        daemon = run_sso_flow()
        if daemon is not None:
            token_file = os.environ.get("KIRO_SSO_TOKEN_FILE", "")
            config_path = generate_dynamic_config(token_file)
    except Exception as e:
        print(f"[AWS SSO] Error: {e}", file=sys.stderr)
        if os.getenv("AWS_SSO_FAIL_OPEN", "").lower() != "true":
            print("[AWS SSO] Exiting. Set AWS_SSO_FAIL_OPEN=true to start without SSO.", file=sys.stderr)
            sys.exit(1)
        print("[AWS SSO] Continuing without SSO (AWS_SSO_FAIL_OPEN=true)", file=sys.stderr)

    start_proxy(config_path)


if __name__ == "__main__":
    main()

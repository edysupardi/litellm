#!/usr/bin/env python3
import os
import signal
import subprocess
import sys

CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
DYNAMIC_CONFIG_PATH = "/tmp/litellm-dynamic-config.yaml"
STATIC_CONFIG_PATH = "/app/config.yaml"

if CUSTOM_DIR not in sys.path:
    sys.path.insert(0, CUSTOM_DIR)


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

    # Expose token file path so KiroProvider can read it per-request
    os.environ["KIRO_SSO_TOKEN_FILE"] = config.token_store_path

    daemon = RefreshDaemon(config, logger, device_auth)
    daemon.start()
    return daemon


def generate_dynamic_config(access_token: str) -> str:
    import yaml
    from kiro.provider import fetch_available_models

    region = os.environ.get("KIRO_REGION", "us-east-1")
    master_key = os.environ.get("LITELLM_MASTER_KEY", "sk-1234")
    models = fetch_available_models(access_token, region)

    model_list = [
        {
            "model_name": f"kiro-{m['modelId']}",
            "litellm_params": {
                "model": f"kiro/{m['modelId']}",
            },
        }
        for m in models
    ]

    config = {
        "model_list": model_list,
        "general_settings": {"master_key": master_key},
        "litellm_settings": {"drop_params": True, "telemetry": False},
    }

    with open(DYNAMIC_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"[Kiro] Dynamic config written with {len(model_list)} models", file=sys.stderr)
    return DYNAMIC_CONFIG_PATH


def _get_access_token_from_store() -> str:
    import json
    token_file = os.environ.get("KIRO_SSO_TOKEN_FILE", "")
    if not token_file or not os.path.exists(token_file):
        return ""
    try:
        return json.loads(open(token_file).read()).get("access_token", "")
    except Exception:
        return ""


def build_litellm_cmd(config_path: str) -> list[str]:
    base = ["ddtrace-run", "litellm"] if os.getenv("USE_DDTRACE", "").lower() == "true" else ["litellm"]
    if os.getenv("USE_DDTRACE", "").lower() == "true":
        os.environ["DD_TRACE_OPENAI_ENABLED"] = "False"
    args = sys.argv[1:]
    if config_path and "--config" not in args:
        args = ["--config", config_path] + args
    return base + args


def main() -> None:
    try:
        run_prisma_migration()
    except Exception as e:
        print(f"Warning: Prisma migration failed: {e}", file=sys.stderr)

    daemon = None
    sso_succeeded = False
    try:
        daemon = run_sso_flow()
        sso_succeeded = daemon is not None
    except Exception as e:
        print(f"[AWS SSO] Error: {e}", file=sys.stderr)
        if os.getenv("AWS_SSO_FAIL_OPEN", "").lower() != "true":
            print("[AWS SSO] Exiting. Set AWS_SSO_FAIL_OPEN=true to start without SSO.", file=sys.stderr)
            sys.exit(1)
        print("[AWS SSO] Continuing without SSO (AWS_SSO_FAIL_OPEN=true)", file=sys.stderr)

    config_path = STATIC_CONFIG_PATH
    if sso_succeeded:
        try:
            access_token = _get_access_token_from_store()
            if access_token:
                config_path = generate_dynamic_config(access_token)
        except Exception as e:
            print(f"[Kiro] Failed to generate dynamic config: {e}, falling back to static config", file=sys.stderr)

    # Register KiroProvider via startup hook
    os.environ["LITELLM_WORKER_STARTUP_HOOKS"] = "startup_hook:register_kiro_provider"
    # Make custom/ importable inside the litellm subprocess
    existing_path = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = f"{CUSTOM_DIR}:{existing_path}" if existing_path else CUSTOM_DIR

    cmd = build_litellm_cmd(config_path)
    proc = subprocess.Popen(cmd)

    def _forward_signal(signum: int, _frame: object) -> None:
        proc.send_signal(signum)

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    exit_code = proc.wait()
    if daemon is not None:
        daemon.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

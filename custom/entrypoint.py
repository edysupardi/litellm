#!/usr/bin/env python3
import os
import signal
import subprocess
import sys

CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
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
    from aws_sso.account_role import AccountRoleDetector
    from aws_sso.config import SSOConfig
    from aws_sso.credentials import CredentialManager
    from aws_sso.device_auth import DeviceAuthFlow
    from aws_sso.logger import SSOResponseLogger
    from aws_sso.refresh import RefreshDaemon

    config = SSOConfig.from_env()
    if not config.enabled:
        return

    print("[AWS SSO] Initializing...", file=sys.stderr)
    logger = SSOResponseLogger(config.log_dir)
    device_auth = DeviceAuthFlow(config, logger)
    account_detector = AccountRoleDetector(config, logger)
    credential_manager = CredentialManager(config, logger)

    device_auth.run_interactive()

    account_id, role_name = account_detector.auto_detect(device_auth.access_token)
    config.account_id = account_id
    config.role_name = role_name

    credential_manager.fetch(device_auth.access_token, account_id, role_name)
    credential_manager.inject()

    daemon = RefreshDaemon(config, logger, device_auth, credential_manager)
    daemon.start()

    return daemon


def build_litellm_cmd() -> list[str]:
    if os.getenv("USE_DDTRACE", "").lower() == "true":
        os.environ["DD_TRACE_OPENAI_ENABLED"] = "False"
        return ["ddtrace-run", "litellm"] + sys.argv[1:]
    return ["litellm"] + sys.argv[1:]


def main() -> None:
    try:
        run_prisma_migration()
    except Exception as e:
        print(f"Warning: Prisma migration failed: {e}", file=sys.stderr)

    daemon = None
    try:
        daemon = run_sso_flow()
    except Exception as e:
        print(f"[AWS SSO] Error: {e}", file=sys.stderr)
        if os.getenv("AWS_SSO_FAIL_OPEN", "").lower() != "true":
            print("[AWS SSO] Exiting. Set AWS_SSO_FAIL_OPEN=true to start without SSO.", file=sys.stderr)
            sys.exit(1)
        print("[AWS SSO] Continuing without SSO (AWS_SSO_FAIL_OPEN=true)", file=sys.stderr)

    cmd = build_litellm_cmd()
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

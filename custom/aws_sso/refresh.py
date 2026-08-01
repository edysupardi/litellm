import sys
import threading
import time
from typing import TYPE_CHECKING, Optional

from .config import SSOConfig
from .credentials import CredentialManager
from .device_auth import DeviceAuthFlow
from .exceptions import SSOAuthError
from .logger import SSOResponseLogger

CHECK_INTERVAL = 60
TOKEN_REFRESH_THRESHOLD = 300


class RefreshDaemon:
    def __init__(
        self,
        config: SSOConfig,
        logger: SSOResponseLogger,
        device_auth: DeviceAuthFlow,
        credential_manager: CredentialManager,
    ) -> None:
        self.config = config
        self.logger = logger
        self.device_auth = device_auth
        self.credential_manager = credential_manager
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="aws-sso-refresh",
            daemon=True,
        )
        self._thread.start()
        print("[AWS SSO] Background refresh daemon started", file=sys.stderr)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(CHECK_INTERVAL):
            try:
                self._check_and_refresh()
            except Exception as e:
                self.logger.log("refresh_daemon_error", {"error": str(e)})
                print(f"[AWS SSO] Refresh daemon error: {e}", file=sys.stderr)

    def _check_and_refresh(self) -> None:
        token_remaining = self.device_auth.token_expires_at - time.time()
        cred_remaining = self.credential_manager.seconds_until_expiry()
        print(
            f"[AWS SSO] Token expires in {token_remaining:.0f}s, "
            f"credentials expire in {cred_remaining:.0f}s",
            file=sys.stderr,
        )

        if not self.credential_manager.is_expiring_soon():
            return

        with self._lock:
            self._refresh_or_relogin()

    def _refresh_or_relogin(self) -> None:
        token_remaining = self.device_auth.token_expires_at - time.time()
        if token_remaining < TOKEN_REFRESH_THRESHOLD:
            try:
                self.device_auth.refresh_access_token()
                print("[AWS SSO] Access token refreshed", file=sys.stderr)
            except SSOAuthError:
                print("[AWS SSO] Token refresh failed, triggering full re-login", file=sys.stderr)
                self._full_relogin()
                return

        try:
            self.credential_manager.fetch(
                access_token=self.device_auth.access_token,
                account_id=self.config.account_id,  # type: ignore[arg-type]
                role_name=self.config.role_name,  # type: ignore[arg-type]
            )
            self.credential_manager.refresh()
            print("[AWS SSO] Credentials refreshed successfully", file=sys.stderr)
        except Exception as e:
            self.logger.log("credential_refresh_error", {"error": str(e)})
            print(f"[AWS SSO] Credential fetch failed: {e}, triggering full re-login", file=sys.stderr)
            self._full_relogin()

    def _full_relogin(self) -> None:
        print("[AWS SSO] Starting full re-login...", file=sys.stderr)
        self.device_auth.run_interactive()
        self.credential_manager.fetch(
            access_token=self.device_auth.access_token,
            account_id=self.config.account_id,  # type: ignore[arg-type]
            role_name=self.config.role_name,  # type: ignore[arg-type]
        )
        self.credential_manager.refresh()
        print("[AWS SSO] Re-login complete", file=sys.stderr)

import sys
import threading
import time
from typing import Optional

from .config import SSOConfig
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
    ) -> None:
        self.config = config
        self.logger = logger
        self.device_auth = device_auth
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
        remaining = self.device_auth.token_expires_at - time.time()
        print(f"[AWS SSO] Token expires in {remaining:.0f}s", file=sys.stderr)
        if remaining >= TOKEN_REFRESH_THRESHOLD:
            return
        with self._lock:
            self._refresh_or_relogin()

    def _refresh_or_relogin(self) -> None:
        try:
            self.device_auth.refresh_access_token()
            print("[AWS SSO] Token refreshed successfully", file=sys.stderr)
        except SSOAuthError:
            print("[AWS SSO] Token refresh failed, triggering full re-login", file=sys.stderr)
            self._full_relogin()

    def _full_relogin(self) -> None:
        print("[AWS SSO] Starting full re-login...", file=sys.stderr)
        self.device_auth.run_interactive()
        print("[AWS SSO] Re-login complete", file=sys.stderr)

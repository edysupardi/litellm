import json
import sys
import time
from pathlib import Path
from typing import Any, Optional, Tuple

import boto3

from .config import SSOConfig
from .exceptions import SSOAuthError, SSOTimeoutError
from .logger import SSOResponseLogger

GRANT_TYPE_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"
GRANT_TYPE_REFRESH = "refresh_token"


class DeviceAuthFlow:
    def __init__(self, config: SSOConfig, logger: SSOResponseLogger) -> None:
        self.config = config
        self.logger = logger
        self._client = boto3.client("sso-oidc", region_name=config.sso_region)

        self.client_id: str = ""
        self.client_secret: str = ""
        self.device_code: str = ""
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.token_expires_at: float = 0.0

    def load_from_store(self) -> bool:
        """Load persisted token from disk. Returns True if valid token found."""
        path = Path(self.config.token_store_path)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            expires_at = data.get("token_expires_at", 0)
            if expires_at - time.time() < 300:
                print("[AWS SSO] Stored token expired or expiring soon, re-login required", file=sys.stderr)
                return False
            self.client_id = data.get("client_id", "")
            self.client_secret = data.get("client_secret", "")
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token", "")
            self.token_expires_at = expires_at
            print("[AWS SSO] Loaded valid token from store", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[AWS SSO] Failed to load token store: {e}", file=sys.stderr)
            return False

    def save_to_store(self) -> None:
        path = Path(self.config.token_store_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expires_at": self.token_expires_at,
        }
        path.write_text(json.dumps(data, indent=2))

    def _register_client(self) -> dict[str, Any]:
        response = self._client.register_client(
            clientName="kiro-gateway",
            clientType="public",
            scopes=[
                "codewhisperer:completions",
                "codewhisperer:analysis",
                "codewhisperer:conversations",
            ],
        )
        self.logger.log("register_client", response)
        self.client_id = response["clientId"]
        self.client_secret = response["clientSecret"]
        return response

    def _start_device_authorization(self) -> dict[str, Any]:
        response = self._client.start_device_authorization(
            clientId=self.client_id,
            clientSecret=self.client_secret,
            startUrl=self.config.start_url,
        )
        self.logger.log("start_device_authorization", response)
        self.device_code = response["deviceCode"]
        return response

    def _poll_for_token(self, interval: int, expires_in: int) -> dict[str, Any]:
        deadline = time.time() + expires_in
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                response = self._client.create_token(
                    clientId=self.client_id,
                    clientSecret=self.client_secret,
                    grantType=GRANT_TYPE_DEVICE,
                    deviceCode=self.device_code,
                )
                self.logger.log(f"create_token_attempt_{attempt}_success", response)
                self._store_token(response)
                return response
            except self._client.exceptions.AuthorizationPendingException:
                self.logger.log(f"create_token_attempt_{attempt}_pending", {"status": "pending"})
                time.sleep(interval)
            except self._client.exceptions.SlowDownException:
                self.logger.log(f"create_token_attempt_{attempt}_slow_down", {"status": "slow_down"})
                interval += 5
                time.sleep(interval)
            except Exception as e:
                self.logger.log(f"create_token_attempt_{attempt}_error", {"error": str(e)})
                raise SSOAuthError(f"Token creation failed: {e}") from e
        raise SSOTimeoutError("Timed out waiting for user to complete authorization")

    def _store_token(self, response: dict[str, Any]) -> None:
        self.access_token = response["accessToken"]
        self.refresh_token = response.get("refreshToken", "")
        self.token_expires_at = time.time() + response["expiresIn"]

    def refresh_access_token(self) -> dict[str, Any]:
        if not self.refresh_token:
            raise SSOAuthError("No refresh token available")
        try:
            response = self._client.create_token(
                clientId=self.client_id,
                clientSecret=self.client_secret,
                grantType=GRANT_TYPE_REFRESH,
                refreshToken=self.refresh_token,
            )
            self.logger.log("refresh_token_success", response)
            self._store_token(response)
            self.save_to_store()
            return response
        except Exception as e:
            self.logger.log("refresh_token_error", {"error": str(e)})
            raise SSOAuthError(f"Token refresh failed: {e}") from e

    def run_interactive(self) -> Tuple[str, float]:
        print("[AWS SSO] Registering OIDC client...", file=sys.stderr)
        self._register_client()

        print("[AWS SSO] Starting device authorization...", file=sys.stderr)
        auth = self._start_device_authorization()

        interval = auth.get("interval", 5)
        expires_in = auth["expiresIn"]

        print("\n" + "=" * 60, file=sys.stderr)
        print("AWS SSO LOGIN REQUIRED", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"\n1. Open this URL in your browser:\n   {auth['verificationUriComplete']}", file=sys.stderr)
        print(f"\n2. Enter this code if prompted: {auth['userCode']}", file=sys.stderr)
        print(f"\nWaiting for authorization (timeout: {expires_in}s)...\n", file=sys.stderr)

        self._poll_for_token(interval, expires_in)
        self.save_to_store()
        print("[AWS SSO] Authorization successful! Token saved.", file=sys.stderr)
        return self.access_token, self.token_expires_at

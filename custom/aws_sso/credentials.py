import configparser
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3

from .config import SSOConfig
from .exceptions import SSOAuthError
from .logger import SSOResponseLogger

AWS_PROFILE_NAME = "litellm-sso"
AWS_CREDENTIALS_PATH = Path.home() / ".aws" / "credentials"


class CredentialManager:
    REFRESH_THRESHOLD_SECONDS = 300

    def __init__(self, config: SSOConfig, logger: SSOResponseLogger) -> None:
        self.config = config
        self.logger = logger
        self._sso = boto3.client("sso", region_name=config.sso_region)
        self._access_key: str = ""
        self._secret_key: str = ""
        self._session_token: str = ""
        self.expires_at: float = 0.0

    def fetch(self, access_token: str, account_id: str, role_name: str) -> dict[str, Any]:
        response = self._sso.get_role_credentials(
            accessToken=access_token,
            accountId=account_id,
            roleName=role_name,
        )
        self.logger.log("get_role_credentials", response)
        creds = response["roleCredentials"]
        self._access_key = creds["accessKeyId"]
        self._secret_key = creds["secretAccessKey"]
        self._session_token = creds["sessionToken"]
        self.expires_at = creds["expiration"] / 1000.0
        return response

    def inject(self) -> None:
        """Write credentials to ~/.aws/credentials and set AWS_PROFILE.

        We deliberately do NOT set individual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
        AWS_SESSION_TOKEN env vars. If those are set, boto3 treats them as the highest-priority
        source and never re-reads the credentials file, which would break refresh. Instead, we
        point boto3 at the profile so every new client picks up whatever is currently on disk.
        """
        if not self._access_key:
            raise SSOAuthError("No credentials available to inject")
        # Remove individual credential env vars if they were set by something else
        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            os.environ.pop(var, None)
        os.environ["AWS_PROFILE"] = AWS_PROFILE_NAME
        self._write_credentials_file()
        expiry = datetime.fromtimestamp(self.expires_at).isoformat()
        print(f"[AWS SSO] Credentials written to profile [{AWS_PROFILE_NAME}], expire at: {expiry}", file=sys.stderr)

    def refresh(self) -> None:
        """Overwrite the credentials file with fresh credentials.

        boto3 re-reads ~/.aws/credentials on each new client creation (LiteLLM caches
        credentials for ~10 minutes via its DualCache, so the new values are picked up
        well before the credentials actually expire).
        """
        if not self._access_key:
            raise SSOAuthError("No credentials available to refresh")
        self._write_credentials_file()
        expiry = datetime.fromtimestamp(self.expires_at).isoformat()
        print(f"[AWS SSO] Credentials file refreshed, new expiry: {expiry}", file=sys.stderr)

    def _write_credentials_file(self) -> None:
        AWS_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = configparser.ConfigParser()
        if AWS_CREDENTIALS_PATH.exists():
            cfg.read(AWS_CREDENTIALS_PATH)
        if AWS_PROFILE_NAME not in cfg:
            cfg[AWS_PROFILE_NAME] = {}
        cfg[AWS_PROFILE_NAME]["aws_access_key_id"] = self._access_key
        cfg[AWS_PROFILE_NAME]["aws_secret_access_key"] = self._secret_key
        cfg[AWS_PROFILE_NAME]["aws_session_token"] = self._session_token
        with open(AWS_CREDENTIALS_PATH, "w") as f:
            cfg.write(f)

    def seconds_until_expiry(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def is_expiring_soon(self) -> bool:
        return self.seconds_until_expiry() < self.REFRESH_THRESHOLD_SECONDS

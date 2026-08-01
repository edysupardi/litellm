import os
from dataclasses import dataclass, field
from typing import Optional

from .exceptions import SSOConfigError


@dataclass
class SSOConfig:
    enabled: bool
    start_url: str
    sso_region: str
    log_dir: str
    fail_open: bool
    account_id: Optional[str] = field(default=None)
    role_name: Optional[str] = field(default=None)

    @classmethod
    def from_env(cls) -> "SSOConfig":
        enabled = os.getenv("ENABLE_AWS_SSO_OIDC", "").lower() == "true"
        start_url = os.getenv("AWS_SSO_START_URL", "")
        sso_region = os.getenv("AWS_SSO_REGION", "")
        log_dir = os.getenv("AWS_SSO_LOG_DIR", "/var/log/aws-sso")
        fail_open = os.getenv("AWS_SSO_FAIL_OPEN", "").lower() == "true"

        if enabled and not start_url:
            raise SSOConfigError("ENABLE_AWS_SSO_OIDC=true requires AWS_SSO_START_URL")
        if enabled and not sso_region:
            raise SSOConfigError("ENABLE_AWS_SSO_OIDC=true requires AWS_SSO_REGION")

        return cls(
            enabled=enabled,
            start_url=start_url,
            sso_region=sso_region,
            log_dir=log_dir,
            fail_open=fail_open,
        )

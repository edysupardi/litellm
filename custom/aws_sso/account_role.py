import sys
from typing import Any, Tuple

import boto3

from .config import SSOConfig
from .exceptions import SSOAuthError
from .logger import SSOResponseLogger


class AccountRoleDetector:
    def __init__(self, config: SSOConfig, logger: SSOResponseLogger) -> None:
        self.config = config
        self.logger = logger
        self._sso = boto3.client("sso", region_name=config.sso_region)

    def list_accounts(self, access_token: str) -> list[dict[str, Any]]:
        paginator = self._sso.get_paginator("list_accounts")
        accounts: list[dict[str, Any]] = []
        for page in paginator.paginate(accessToken=access_token):
            self.logger.log("list_accounts", page)
            accounts.extend(page.get("accountList", []))
        return accounts

    def list_account_roles(self, access_token: str, account_id: str) -> list[dict[str, Any]]:
        paginator = self._sso.get_paginator("list_account_roles")
        roles: list[dict[str, Any]] = []
        for page in paginator.paginate(accessToken=access_token, accountId=account_id):
            self.logger.log("list_account_roles", page)
            roles.extend(page.get("roleList", []))
        return roles

    def auto_detect(self, access_token: str) -> Tuple[str, str]:
        accounts = self.list_accounts(access_token)
        if not accounts:
            raise SSOAuthError("No accounts available for this SSO user")

        account = accounts[0]
        account_id = account["accountId"]
        account_name = account.get("accountName", "unnamed")

        if len(accounts) > 1:
            print(f"[AWS SSO] {len(accounts)} accounts available, using first: {account_id} ({account_name})", file=sys.stderr)
        else:
            print(f"[AWS SSO] Using account: {account_id} ({account_name})", file=sys.stderr)

        roles = self.list_account_roles(access_token, account_id)
        if not roles:
            raise SSOAuthError(f"No roles available for account {account_id}")

        role_name = roles[0]["roleName"]

        if len(roles) > 1:
            print(f"[AWS SSO] {len(roles)} roles available, using first: {role_name}", file=sys.stderr)
        else:
            print(f"[AWS SSO] Using role: {role_name}", file=sys.stderr)

        return account_id, role_name

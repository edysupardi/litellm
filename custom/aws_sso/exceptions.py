class SSOError(Exception):
    pass


class SSOAuthError(SSOError):
    pass


class SSOTimeoutError(SSOError):
    pass


class SSOConfigError(SSOError):
    pass

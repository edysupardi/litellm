import litellm

_registered = False


def register_kiro_provider() -> None:
    global _registered
    if _registered:
        return
    import sys
    sys.path.insert(0, "/app/custom")
    from kiro.provider import KiroProvider
    litellm.custom_provider_map = [
        {"provider": "kiro", "custom_handler": KiroProvider()},
    ]
    _registered = True
    print("[Kiro] Custom provider registered", file=sys.stderr)

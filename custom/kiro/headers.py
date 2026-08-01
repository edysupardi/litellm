import hashlib
import os
import socket
import uuid
from typing import Any


def _fingerprint() -> str:
    hostname = socket.gethostname()
    try:
        username = os.getlogin()
    except OSError:
        username = os.environ.get("USER", "unknown")
    raw = f"{hostname}-{username}-kiro-gateway"
    return hashlib.sha256(raw.encode()).hexdigest()


_FP = _fingerprint()


def build_headers(access_token: str, stream: bool = False) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": (
            f"aws-sdk-js/1.0.27 ua/2.1 os/linux#5.0 lang/js md/nodejs#22.21.1 "
            f"api/codewhispererstreaming#1.0.27 m/E KiroIDE-0.7.45-{_FP}"
        ),
        "x-amz-user-agent": f"aws-sdk-js/1.0.27 KiroIDE-0.7.45-{_FP}",
        "x-amzn-codewhisperer-optout": "true",
        "x-amzn-kiro-agent-mode": "vibe",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=3",
    }
    if stream:
        headers["Connection"] = "close"
    return headers

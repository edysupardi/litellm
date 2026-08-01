import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class SSOResponseLogger:
    def __init__(self, log_dir: str) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, operation: str, response: Any) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        filepath = self.log_dir / f"{timestamp}_{operation}.json"
        with open(filepath, "w") as f:
            json.dump(self._serialize(response), f, indent=2, default=str)
        print(f"[AWS SSO] Logged {operation} -> {filepath}", file=sys.stderr)
        return str(filepath)

    def _serialize(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self._serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize(v) for v in obj]
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return obj

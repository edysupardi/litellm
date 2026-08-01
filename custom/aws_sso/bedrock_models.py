import sys
from typing import Any

import boto3
import yaml


def fetch_bedrock_models(region: str) -> list[dict[str, Any]]:
    client = boto3.client("bedrock", region_name=region)
    response = client.list_foundation_models(
        byOutputModality="TEXT",
        byInferenceType="ON_DEMAND",
    )
    models = []
    for m in response.get("modelSummaries", []):
        model_id = m["modelId"]
        provider = m.get("providerName", "").lower().replace(" ", "-")
        short_name = model_id.split("/")[-1] if "/" in model_id else model_id
        model_name = f"{provider}-{short_name}" if provider else short_name
        models.append({
            "model_name": model_name,
            "litellm_params": {
                "model": f"bedrock/converse/{model_id}",
                "aws_region_name": region,
            },
        })
    print(f"[AWS SSO] Fetched {len(models)} Bedrock models from {region}", file=sys.stderr)
    return models


def write_dynamic_config(models: list[dict[str, Any]], master_key: str, output_path: str) -> None:
    config = {
        "model_list": models,
        "general_settings": {
            "master_key": master_key,
        },
        "litellm_settings": {
            "drop_params": True,
            "telemetry": False,
        },
    }
    with open(output_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"[AWS SSO] Dynamic config written to {output_path}", file=sys.stderr)

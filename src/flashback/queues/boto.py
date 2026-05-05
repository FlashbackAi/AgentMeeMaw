"""boto3 client helpers for queue integrations."""

from __future__ import annotations

import os
from typing import Any

import boto3


def make_sqs_client(region_name: str) -> Any:
    """Create an SQS client, honoring local endpoint overrides.

    ``SQS_ENDPOINT_URL`` is the repo-local knob used for LocalStack. The
    ``AWS_ENDPOINT_URL_*`` fallbacks keep this compatible with newer botocore
    conventions and existing shell setups.
    """

    endpoint_url = (
        os.environ.get("SQS_ENDPOINT_URL")
        or os.environ.get("AWS_ENDPOINT_URL_SQS")
        or os.environ.get("AWS_ENDPOINT_URL")
    )
    kwargs: dict[str, Any] = {"region_name": region_name}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("sqs", **kwargs)

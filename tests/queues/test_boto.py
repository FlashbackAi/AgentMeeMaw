from __future__ import annotations

from flashback.queues import boto


def test_make_sqs_client_uses_local_endpoint(monkeypatch):
    calls = []

    def fake_client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return object()

    monkeypatch.setenv("SQS_ENDPOINT_URL", "http://localhost:4566")
    monkeypatch.setattr(boto.boto3, "client", fake_client)

    boto.make_sqs_client("us-east-1")

    assert calls == [
        (
            "sqs",
            {
                "region_name": "us-east-1",
                "endpoint_url": "http://localhost:4566",
            },
        )
    ]


def test_make_sqs_client_omits_endpoint_by_default(monkeypatch):
    calls = []

    def fake_client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return object()

    monkeypatch.delenv("SQS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_SQS", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setattr(boto.boto3, "client", fake_client)

    boto.make_sqs_client("us-west-2")

    assert calls == [("sqs", {"region_name": "us-west-2"})]

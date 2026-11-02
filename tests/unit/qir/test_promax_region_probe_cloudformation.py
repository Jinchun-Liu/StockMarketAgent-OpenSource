from __future__ import annotations

from pathlib import Path

import yaml
from scripts.public_demo_aws import assert_cloudformation_template_alias_free

TEMPLATE = Path("deploy/aws/promax-region-probe.yaml")
RAW_TEMPLATE = TEMPLATE.read_bytes()
DOCUMENT = yaml.safe_load(RAW_TEMPLATE)
RESOURCES = DOCUMENT["Resources"]
FUNCTION = RESOURCES["ProbeFunction"]["Properties"]
CODE = FUNCTION["Code"]["ZipFile"]


def test_region_probe_template_is_alias_free_and_contains_only_two_resources() -> None:
    assert_cloudformation_template_alias_free(RAW_TEMPLATE)
    assert set(RESOURCES) == {"ProbeFunction", "ProbeRole"}
    assert RESOURCES["ProbeRole"]["Properties"] == {
        "AssumeRolePolicyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    }


def test_region_probe_has_no_credentials_logs_or_public_listener() -> None:
    rendered = RAW_TEMPLATE.decode("utf-8").lower()
    assert "secret" not in rendered
    assert "credential" not in rendered
    assert "api-key" not in rendered
    assert "loggroup" not in rendered
    assert "functionurl" not in rendered
    assert "environment" not in FUNCTION


def test_region_probe_is_fixed_to_one_tls_verified_non_redirecting_request() -> None:
    assert 'BASE_URL = "https://pcd.mobcvb.cn/tushare/pro/"' in CODE
    assert 'BASE_URL + "daily?"' in CODE
    assert "ssl.create_default_context()" in CODE
    assert "class NoRedirect" in CODE
    assert "return None" in CODE
    assert "timeout=10" in CODE
    assert "response.read(1)" in CODE
    assert "event.get" not in CODE
    assert "urlopen" not in CODE


def test_region_probe_runtime_is_bounded_and_ephemeral_by_default() -> None:
    assert FUNCTION["Architectures"] == ["arm64"]
    assert FUNCTION["Runtime"] == "python3.13"
    assert FUNCTION["Timeout"] == 20
    assert FUNCTION["MemorySize"] == 128
    assert "DeletionPolicy" not in RESOURCES["ProbeFunction"]
    assert "DeletionPolicy" not in RESOURCES["ProbeRole"]

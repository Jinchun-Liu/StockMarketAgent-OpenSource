from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import scripts.public_demo_aws as aws_module
from scripts.public_demo_aws import (
    CLOUDFORMATION_WAIT_TIMEOUT_SECONDS,
    CLOUDFRONT_WAIT_TIMEOUT_SECONDS,
    DEFAULT_AWS_CLI_TIMEOUT_SECONDS,
    REGION,
    STACK_NAME,
    AwsCli,
    AwsSafetyError,
    _verify_deployed_resources,
    create_plan_document,
    deploy,
    locate_aws_cli,
    normalize_changes,
    plan,
    smoke,
    upload,
    verify_change_set,
    verify_cli_v2,
    verify_existing_stack,
    verify_identity,
    verify_plan_document,
)

ACCOUNT = "123456789012"
CHANGE_SET_ARN = (
    "arn:aws:cloudformation:ap-northeast-1:123456789012:"
    "changeSet/public-demo-a/00000000-0000-4000-8000-000000000001"
)
STACK_ID = (
    f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:"
    f"stack/{STACK_NAME}/00000000-0000-4000-8000-000000000002"
)
ORIGIN_SECRET_ARN = (
    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:stockmarketagent-r2-origin-AbCdEf"
)
R2_NO_CACHE_POLICY_ID = "49941de9-8639-4157-930a-37e5bf89863d"
R2_ORIGIN_REQUEST_POLICY_ID = "9d37dd33-3641-4244-b541-2ba83bf3fb37"


def _business_resource_tags(product_sha: str = "a" * 40) -> dict[str, str]:
    return {
        "Project": "stockmarketagent-public-demo",
        "ManagedBy": "cloudformation",
        "ProductSha": product_sha,
    }


def _cloudformation_bucket_tags(product_sha: str = "a" * 40) -> dict[str, str]:
    return {
        **_business_resource_tags(product_sha),
        "aws:cloudformation:stack-name": STACK_NAME,
        "aws:cloudformation:logical-id": "PublicDemoBucket",
        "aws:cloudformation:stack-id": STACK_ID,
    }


def _tag_records(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in tags.items()]


def _change(
    *,
    action: str = "Modify",
    logical_id: str = "PublicDemoDistribution",
    resource_type: str = "AWS::CloudFront::Distribution",
    replacement: str = "False",
) -> dict[str, object]:
    return {
        "ResourceChange": {
            "Action": action,
            "LogicalResourceId": logical_id,
            "ResourceType": resource_type,
            "Replacement": replacement,
        }
    }


def _described_change_set(
    *,
    product_sha: str = "a" * 40,
    change_set_type: str = "UPDATE",
    execution_status: str = "AVAILABLE",
) -> dict[str, object]:
    return {
        "ChangeSetId": CHANGE_SET_ARN,
        "StackName": STACK_NAME,
        "ChangeSetType": change_set_type,
        "Status": "CREATE_COMPLETE",
        "ExecutionStatus": execution_status,
        "Parameters": [{"ParameterKey": "ProductSha", "ParameterValue": product_sha}],
        "Tags": [
            {"Key": "Project", "Value": "stockmarketagent-public-demo"},
            {"Key": "ManagedBy", "Value": "StockMarketAgent"},
        ],
        "Changes": [_change()],
    }


def _stack() -> dict[str, object]:
    return {
        "Stacks": [
            {
                "StackName": STACK_NAME,
                "StackId": STACK_ID,
                "StackStatus": "UPDATE_COMPLETE",
                "Tags": [
                    {"Key": "Project", "Value": "stockmarketagent-public-demo"},
                    {"Key": "ManagedBy", "Value": "StockMarketAgent"},
                ],
            }
        ]
    }


def _legacy_stack(product_sha: str = "f" * 40) -> dict[str, object]:
    payload = _stack()
    stack = payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    stack["Tags"] = [
        {"Key": "Project", "Value": "stockmarketagent-public-demo"},
        {"Key": "ManagedBy", "Value": "cloudformation"},
        {"Key": "ProductSha", "Value": product_sha},
    ]
    return payload


def _stack_with_outputs(product_sha: str = "a" * 40) -> dict[str, object]:
    payload = _stack()
    stack = payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    stack["Parameters"] = [{"ParameterKey": "ProductSha", "ParameterValue": product_sha}]
    stack["Outputs"] = [
        {"OutputKey": "BucketName", "OutputValue": "project-bucket"},
        {"OutputKey": "DistributionDomainName", "OutputValue": "demo.cloudfront.net"},
        {"OutputKey": "DistributionId", "OutputValue": "EDIST"},
        {"OutputKey": "PublicDemoUrl", "OutputValue": "https://demo.cloudfront.net"},
        {"OutputKey": "ProductSha", "OutputValue": product_sha},
    ]
    return payload


def test_cli_identity_region_and_stack_are_exact() -> None:
    verify_cli_v2("aws-cli/2.17.0 Python/3.12 Darwin/24 exe/x86_64")
    identity = verify_identity(
        {"Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo/test"},
        ACCOUNT,
    )
    assert identity["Account"] == ACCOUNT
    assert verify_existing_stack(_stack())["StackName"] == STACK_NAME
    assert REGION == "ap-northeast-1"
    command = AwsCli("/opt/aws")._command("sts", "get-caller-identity", "--output", "json")
    assert command == [
        "/opt/aws",
        "sts",
        "get-caller-identity",
        "--output",
        "json",
        "--region",
        REGION,
        "--no-cli-pager",
    ]

    with pytest.raises(AwsSafetyError, match="CLI v2"):
        verify_cli_v2("aws-cli/1.0")
    with pytest.raises(AwsSafetyError, match="does not match"):
        verify_identity(
            {"Account": "999999999999", "Arn": "arn:aws:iam::999999999999:user/x"},
            ACCOUNT,
        )
    wrong = _stack()
    tags = wrong["Stacks"][0]["Tags"]  # type: ignore[index]
    assert isinstance(tags, list)
    tags[0] = {"Key": "Project", "Value": "other"}
    with pytest.raises(AwsSafetyError, match="tags"):
        verify_existing_stack(wrong)


def test_public_operator_accepts_an_explicit_verified_cli_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "aws"
    executable.write_text("verified test fixture", encoding="utf-8")
    monkeypatch.setenv("STOCK_ASSISTANT_AWS_CLI", str(executable))
    assert locate_aws_cli() == str(executable.resolve())


def test_existing_stack_accepts_only_canonical_or_exact_legacy_tags() -> None:
    assert verify_existing_stack(_stack())["StackName"] == STACK_NAME
    assert verify_existing_stack(_legacy_stack())["StackName"] == STACK_NAME

    rejected_tags = [
        [
            {"Key": "Project", "Value": "stockmarketagent-public-demo"},
            {"Key": "ManagedBy", "Value": "cloudformation"},
            {"Key": "ProductSha", "Value": "not-a-full-sha"},
        ],
        [
            {"Key": "Project", "Value": "stockmarketagent-public-demo"},
            {"Key": "ManagedBy", "Value": "cloudformation"},
        ],
        [
            {"Key": "Project", "Value": "stockmarketagent-public-demo"},
            {"Key": "ManagedBy", "Value": "cloudformation"},
            {"Key": "ProductSha", "Value": "f" * 40},
            {"Key": "Unexpected", "Value": "tag"},
        ],
        [
            {"Key": "Project", "Value": "other"},
            {"Key": "ManagedBy", "Value": "cloudformation"},
            {"Key": "ProductSha", "Value": "f" * 40},
        ],
        [
            {"Key": "Project", "Value": "stockmarketagent-public-demo"},
            {"Key": "ManagedBy", "Value": "CloudFormation"},
            {"Key": "ProductSha", "Value": "f" * 40},
        ],
    ]
    for tags in rejected_tags:
        payload = _stack()
        stack = payload["Stacks"][0]  # type: ignore[index]
        assert isinstance(stack, dict)
        stack["Tags"] = tags
        with pytest.raises(AwsSafetyError, match="tags"):
            verify_existing_stack(payload)


def _legacy_probe_changes() -> list[dict[str, object]]:
    return [
        _change(
            logical_id="PublicDemoBucket",
            resource_type="AWS::S3::Bucket",
        ),
        _change(
            logical_id="PublicDemoBucketPolicy",
            resource_type="AWS::S3::BucketPolicy",
        ),
        _change(),
    ]


class _PlanInvoker:
    def __init__(
        self,
        stack: dict[str, object] | None,
        *,
        described_tags: dict[str, str],
        changes: list[dict[str, object]] | None = None,
    ) -> None:
        self.stack = stack
        self.described_tags = described_tags
        self.changes = [_change()] if changes is None else changes
        self.create_arguments: tuple[str, ...] | None = None

    def json(self, service: str, operation: str, *arguments: str) -> object:
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo/test",
            }
        if (service, operation) == ("cloudformation", "validate-template"):
            return {}
        if (service, operation) == ("cloudformation", "describe-stacks"):
            if self.stack is None:
                raise subprocess.CalledProcessError(
                    255,
                    ["aws", "cloudformation", "describe-stacks"],
                    stderr=(
                        "An error occurred (ValidationError) when calling the "
                        "DescribeStacks operation: "
                        f"Stack with id {STACK_NAME} does not exist"
                    ),
                )
            return self.stack
        if (service, operation) == ("cloudformation", "create-change-set"):
            self.create_arguments = arguments
            return {"Id": CHANGE_SET_ARN}
        if (service, operation) == ("cloudformation", "describe-change-set"):
            change_set_type = "CREATE" if self.stack is None else "UPDATE"
            payload = _described_change_set(change_set_type=change_set_type)
            del payload["ChangeSetType"]
            payload["Tags"] = [
                {"Key": key, "Value": value} for key, value in self.described_tags.items()
            ]
            payload["Changes"] = self.changes
            return payload
        raise AssertionError(f"unexpected AWS JSON call: {service} {operation} {arguments}")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        raise AssertionError(f"unexpected AWS text call: {service} {operation} {arguments}")


def _run_plan(
    tmp_path: Path,
    stack: dict[str, object] | None,
    *,
    described_tags: dict[str, str],
    changes: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    template = (tmp_path / "template.yaml").resolve()
    template.write_text("Resources: {}\n", encoding="utf-8")
    invoker = _PlanInvoker(
        stack,
        described_tags=described_tags,
        changes=changes,
    )
    document = plan(
        invoker,
        expected_account_id=ACCOUNT,
        product_sha="a" * 40,
        template=template,
        output=(tmp_path / "plan.json").resolve(),
    )
    assert invoker.create_arguments is not None
    return document, invoker.create_arguments


def test_legacy_update_plan_preserves_and_hashes_exact_existing_stack_tags(
    tmp_path: Path,
) -> None:
    legacy_tags = {
        "Project": "stockmarketagent-public-demo",
        "ManagedBy": "cloudformation",
        "ProductSha": "f" * 40,
    }
    document, arguments = _run_plan(
        tmp_path,
        _legacy_stack(),
        described_tags=legacy_tags,
        changes=_legacy_probe_changes(),
    )
    tags_index = arguments.index("--tags")
    assert arguments[tags_index + 1 :] == (
        "Key=Project,Value=stockmarketagent-public-demo",
        "Key=ManagedBy,Value=cloudformation",
        f"Key=ProductSha,Value={'f' * 40}",
    )
    assert document["stack_tags"] == legacy_tags
    assert document["stack_tag_profile"] == "legacy"
    assert document["changes"] == normalize_changes(
        {"Status": "CREATE_COMPLETE", "Changes": _legacy_probe_changes()}
    )

    tampered = json.loads(json.dumps(document))
    tampered["stack_tags"]["ProductSha"] = "e" * 40
    with pytest.raises(AwsSafetyError, match="plan hash"):
        verify_plan_document(tampered)


@pytest.mark.parametrize("stack", [_stack(), None])
def test_canonical_and_new_stack_plans_keep_canonical_tags(
    tmp_path: Path, stack: dict[str, object] | None
) -> None:
    canonical_tags = {
        "Project": "stockmarketagent-public-demo",
        "ManagedBy": "StockMarketAgent",
    }
    document, arguments = _run_plan(
        tmp_path,
        stack,
        described_tags=canonical_tags,
    )
    tags_index = arguments.index("--tags")
    assert arguments[tags_index + 1 :] == (
        "Key=Project,Value=stockmarketagent-public-demo",
        "Key=ManagedBy,Value=StockMarketAgent",
    )
    assert document["stack_tags"] == canonical_tags
    assert document["stack_tag_profile"] == "canonical"


@pytest.mark.parametrize(
    "stderr",
    [
        (
            "An error occurred (AccessDenied) when calling the DescribeStacks operation: "
            "principal does not exist in policy"
        ),
        (
            "An error occurred (ValidationError) when calling the DescribeStacks operation: "
            "Stack with id stockmarketagent-public-demo does not exist"
        ),
        (
            "An error occurred (ValidationError) when calling the DescribeStacks operation "
            "(reached max retries: 0): "
            f"Stack with id {STACK_NAME} does not exist"
        ),
        "Could not connect to the endpoint URL: https://cloudformation.invalid/",
    ],
)
def test_describe_stack_fails_closed_for_non_missing_stack_errors(stderr: str) -> None:
    class ErrorInvoker:
        def json(self, service: str, operation: str, *arguments: str) -> object:
            raise subprocess.CalledProcessError(
                255,
                ["aws", service, operation, *arguments],
                stderr=stderr,
            )

    with pytest.raises(subprocess.CalledProcessError):
        aws_module._describe_stack(ErrorInvoker())


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "aws: [ERROR]: ",
    ],
)
@pytest.mark.parametrize("retry_detail", ["", " (reached max retries: 3)"])
def test_describe_stack_accepts_only_exact_missing_stack_error(
    prefix: str,
    retry_detail: str,
) -> None:
    class MissingInvoker:
        def json(self, service: str, operation: str, *arguments: str) -> object:
            raise subprocess.CalledProcessError(
                255,
                ["aws", service, operation, *arguments],
                stderr=(
                    f"{prefix}An error occurred (ValidationError) when calling the "
                    f"DescribeStacks operation{retry_detail}: "
                    f"Stack with id {STACK_NAME} does not exist\n"
                ),
            )

    assert aws_module._describe_stack(MissingInvoker()) is None


def test_api_origin_is_bound_to_tokyo_region_across_plan_boundaries(tmp_path: Path) -> None:
    singapore_origin = "runtime-api.ap-southeast-1.elb.amazonaws.com"
    with pytest.raises(AwsSafetyError, match="API origin domain"):
        create_plan_document(
            change_set_arn=CHANGE_SET_ARN,
            product_sha="a" * 40,
            expected_account_id=ACCOUNT,
            template_sha256="b" * 64,
            changes=[],
            api_origin_domain=singapore_origin,
        )

    canonical_tags = {
        "Project": "stockmarketagent-public-demo",
        "ManagedBy": "StockMarketAgent",
    }
    template = (tmp_path / "template.yaml").resolve()
    template.write_text("Resources: {}\n", encoding="utf-8")
    invoker = _PlanInvoker(_stack(), described_tags=canonical_tags)
    with pytest.raises(AwsSafetyError, match="API origin domain"):
        plan(
            invoker,
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            template=template,
            output=(tmp_path / "plan.json").resolve(),
            api_origin_domain=singapore_origin,
        )
    assert invoker.create_arguments is None

    described = _described_change_set()
    described["Parameters"] = [
        {"ParameterKey": "ProductSha", "ParameterValue": "a" * 40},
        {"ParameterKey": "ApiOriginDomainName", "ParameterValue": singapore_origin},
    ]
    with pytest.raises(AwsSafetyError, match="API origin domain"):
        verify_change_set(
            described,
            expected_arn=CHANGE_SET_ARN,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
            expected_change_set_type="UPDATE",
            expected_execution_status="AVAILABLE",
            expected_stack_tags=canonical_tags,
            expected_api_origin_domain=singapore_origin,
        )

    deployed_stack = _stack()["Stacks"][0]  # type: ignore[index]
    assert isinstance(deployed_stack, dict)
    deployed_stack["Parameters"] = [
        {"ParameterKey": "ProductSha", "ParameterValue": "a" * 40},
        {"ParameterKey": "ApiOriginDomainName", "ParameterValue": singapore_origin},
    ]
    with pytest.raises(AwsSafetyError, match="API origin domain"):
        aws_module._deployed_api_origin_domain(
            deployed_stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


def test_api_origin_requires_same_account_verification_secret() -> None:
    with pytest.raises(AwsSafetyError, match="configured together"):
        create_plan_document(
            change_set_arn=CHANGE_SET_ARN,
            product_sha="a" * 40,
            expected_account_id=ACCOUNT,
            template_sha256="b" * 64,
            changes=[],
            api_origin_domain="runtime-api.ap-northeast-1.elb.amazonaws.com",
        )
    with pytest.raises(AwsSafetyError, match="outside the fixed account"):
        create_plan_document(
            change_set_arn=CHANGE_SET_ARN,
            product_sha="a" * 40,
            expected_account_id=ACCOUNT,
            template_sha256="b" * 64,
            changes=[],
            api_origin_domain="runtime-api.ap-northeast-1.elb.amazonaws.com",
            api_origin_secret_arn=(  # noqa: S106 - ARN fixture, not a secret value.
                "arn:aws:secretsmanager:ap-northeast-1:210987654321:"
                "secret:stockmarketagent-r2-origin-AbCdEf"
            ),
        )


def test_retained_private_preview_policies_are_paired_and_bound_to_the_api_origin() -> None:
    api_origin_domain = "runtime-api.ap-northeast-1.elb.amazonaws.com"
    changes = normalize_changes({"Status": "CREATE_COMPLETE", "Changes": [_change()]})
    document = create_plan_document(
        change_set_arn=CHANGE_SET_ARN,
        product_sha="a" * 40,
        expected_account_id=ACCOUNT,
        template_sha256="b" * 64,
        changes=changes,
        api_origin_domain=api_origin_domain,
        api_origin_secret_arn=ORIGIN_SECRET_ARN,
        api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
        api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
    )

    assert verify_plan_document(document) == document
    assert document["api_no_cache_policy_id"] == R2_NO_CACHE_POLICY_ID
    assert document["api_origin_request_policy_id"] == R2_ORIGIN_REQUEST_POLICY_ID

    invalid_cases = (
        (R2_NO_CACHE_POLICY_ID, "", api_origin_domain, "configured together"),
        ("", R2_ORIGIN_REQUEST_POLICY_ID, api_origin_domain, "configured together"),
        (R2_NO_CACHE_POLICY_ID, R2_ORIGIN_REQUEST_POLICY_ID, "", "require the R2 API"),
        ("not-a-policy-id", R2_ORIGIN_REQUEST_POLICY_ID, api_origin_domain, "is invalid"),
    )
    for cache_policy_id, origin_policy_id, origin_domain, message in invalid_cases:
        with pytest.raises(AwsSafetyError, match=message):
            create_plan_document(
                change_set_arn=CHANGE_SET_ARN,
                product_sha="a" * 40,
                expected_account_id=ACCOUNT,
                template_sha256="b" * 64,
                changes=changes,
                api_origin_domain=origin_domain,
                api_origin_secret_arn=ORIGIN_SECRET_ARN if origin_domain else "",
                api_no_cache_policy_id=cache_policy_id,
                api_origin_request_policy_id=origin_policy_id,
            )


def test_plan_rejects_an_invalid_retained_policy_before_creating_a_change_set(
    tmp_path: Path,
) -> None:
    class InvalidPolicyPlanInvoker(_PlanInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("cloudfront", "get-cache-policy"):
                return {
                    "CachePolicy": {
                        "CachePolicyConfig": {
                            "MinTTL": 0,
                            "DefaultTTL": 60,
                            "MaxTTL": 60,
                            "ParametersInCacheKeyAndForwardedToOrigin": {
                                "CookiesConfig": {"CookieBehavior": "none"},
                                "HeadersConfig": {"HeaderBehavior": "none"},
                                "QueryStringsConfig": {"QueryStringBehavior": "none"},
                                "EnableAcceptEncodingBrotli": False,
                                "EnableAcceptEncodingGzip": False,
                            },
                        }
                    }
                }
            return super().json(service, operation, *arguments)

    template = (tmp_path / "template.yaml").resolve()
    template.write_text("Resources: {}\n", encoding="utf-8")
    invoker = InvalidPolicyPlanInvoker(
        _stack(),
        described_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "StockMarketAgent",
        },
    )

    with pytest.raises(AwsSafetyError, match="R2 API cache policy"):
        plan(
            invoker,
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            template=template,
            output=(tmp_path / "plan.json").resolve(),
            api_origin_domain="runtime-api.ap-northeast-1.elb.amazonaws.com",
            api_origin_secret_arn=ORIGIN_SECRET_ARN,
            api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
            api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
        )

    assert invoker.create_arguments is None


def test_change_set_binds_exact_retained_private_preview_policy_ids() -> None:
    api_origin_domain = "runtime-api.ap-northeast-1.elb.amazonaws.com"
    payload = _described_change_set()
    payload["Parameters"] = [
        {"ParameterKey": "ProductSha", "ParameterValue": "a" * 40},
        {"ParameterKey": "ApiOriginDomainName", "ParameterValue": api_origin_domain},
        {"ParameterKey": "ApiOriginVerifySecretArn", "ParameterValue": ORIGIN_SECRET_ARN},
        {
            "ParameterKey": "PrivatePreviewApiNoCachePolicyId",
            "ParameterValue": R2_NO_CACHE_POLICY_ID,
        },
        {
            "ParameterKey": "PrivatePreviewApiOriginRequestPolicyId",
            "ParameterValue": R2_ORIGIN_REQUEST_POLICY_ID,
        },
    ]

    assert verify_change_set(
        payload,
        expected_arn=CHANGE_SET_ARN,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
        expected_change_set_type="UPDATE",
        expected_execution_status="AVAILABLE",
        expected_stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "StockMarketAgent",
        },
        expected_api_origin_domain=api_origin_domain,
        expected_api_origin_secret_arn=ORIGIN_SECRET_ARN,
        expected_api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
        expected_api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
    ) == normalize_changes(payload)

    payload["Parameters"][-1]["ParameterValue"] = (  # type: ignore[index]
        "11111111-1111-4111-8111-111111111111"
    )
    with pytest.raises(AwsSafetyError, match="parameters differ"):
        verify_change_set(
            payload,
            expected_arn=CHANGE_SET_ARN,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
            expected_change_set_type="UPDATE",
            expected_execution_status="AVAILABLE",
            expected_stack_tags={
                "Project": "stockmarketagent-public-demo",
                "ManagedBy": "StockMarketAgent",
            },
            expected_api_origin_domain=api_origin_domain,
            expected_api_origin_secret_arn=ORIGIN_SECRET_ARN,
            expected_api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
            expected_api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
        )


def test_aws_cli_uses_long_bounded_wait_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[int] = []

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, int)
        observed.append(timeout)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(aws_module.subprocess, "run", fake_run)
    cli = AwsCli("/opt/aws")
    cli.text("sts", "get-caller-identity")
    cli.text("cloudformation", "wait", "stack-update-complete")
    cli.text("cloudfront", "wait", "invalidation-completed")
    assert observed == [
        DEFAULT_AWS_CLI_TIMEOUT_SECONDS,
        CLOUDFORMATION_WAIT_TIMEOUT_SECONDS,
        CLOUDFRONT_WAIT_TIMEOUT_SECONDS,
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_change(action="Remove"), "action"),
        (_change(replacement="Conditional"), "replacement"),
        (_change(logical_id="UnexpectedBucket"), "unknown project resource"),
        (_change(resource_type="AWS::IAM::Role"), "unknown project resource"),
    ],
)
def test_change_set_rejects_remove_replacement_and_unknown_resources(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(AwsSafetyError, match=message):
        normalize_changes({"Status": "CREATE_COMPLETE", "Changes": [change]})


def test_change_set_plan_hash_binds_arn_account_region_sha_and_diff(tmp_path: Path) -> None:
    changes = normalize_changes({"Status": "CREATE_COMPLETE", "Changes": [_change()]})
    plan = create_plan_document(
        change_set_arn=CHANGE_SET_ARN,
        product_sha="a" * 40,
        expected_account_id=ACCOUNT,
        template_sha256="b" * 64,
        changes=changes,
    )
    assert verify_plan_document(plan) == plan
    assert plan["region"] == REGION
    assert plan["stack_name"] == STACK_NAME
    assert plan["change_set_type"] == "UPDATE"

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    tampered = json.loads(plan_path.read_text())
    tampered["product_sha"] = "c" * 40
    with pytest.raises(AwsSafetyError, match="plan hash"):
        verify_plan_document(tampered)


def test_change_set_must_be_complete_and_scoped_to_expected_arn() -> None:
    with pytest.raises(AwsSafetyError, match="not ready"):
        normalize_changes({"Status": "FAILED", "Changes": []})
    with pytest.raises(AwsSafetyError, match="outside the fixed account/region"):
        create_plan_document(
            change_set_arn=CHANGE_SET_ARN.replace(REGION, "us-east-1"),
            product_sha="a" * 40,
            expected_account_id=ACCOUNT,
            template_sha256="b" * 64,
            changes=[],
        )


def test_described_change_set_binds_stack_type_parameter_and_execution_phase() -> None:
    assert verify_change_set(
        _described_change_set(),
        expected_arn=CHANGE_SET_ARN,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
        expected_change_set_type="UPDATE",
        expected_execution_status="AVAILABLE",
        expected_stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "StockMarketAgent",
        },
    ) == normalize_changes(_described_change_set())

    payload_without_type = _described_change_set()
    del payload_without_type["ChangeSetType"]
    assert verify_change_set(
        payload_without_type,
        expected_arn=CHANGE_SET_ARN,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
        expected_change_set_type="UPDATE",
        expected_execution_status="AVAILABLE",
        expected_stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "StockMarketAgent",
        },
    ) == normalize_changes(payload_without_type)

    mutations = (
        ("StackName", "other", "stack name"),
        ("ChangeSetType", "CREATE", "type"),
        ("ExecutionStatus", "EXECUTE_COMPLETE", "execution status"),
    )
    for key, value, message in mutations:
        payload = _described_change_set()
        payload[key] = value
        with pytest.raises(AwsSafetyError, match=message):
            verify_change_set(
                payload,
                expected_arn=CHANGE_SET_ARN,
                expected_account_id=ACCOUNT,
                expected_product_sha="a" * 40,
                expected_change_set_type="UPDATE",
                expected_execution_status="AVAILABLE",
                expected_stack_tags={
                    "Project": "stockmarketagent-public-demo",
                    "ManagedBy": "StockMarketAgent",
                },
            )

    payload = _described_change_set()
    payload["Parameters"] = [{"ParameterKey": "ProductSha", "ParameterValue": "b" * 40}]
    with pytest.raises(AwsSafetyError, match="ProductSha"):
        verify_change_set(
            payload,
            expected_arn=CHANGE_SET_ARN,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
            expected_change_set_type="UPDATE",
            expected_execution_status="AVAILABLE",
            expected_stack_tags={
                "Project": "stockmarketagent-public-demo",
                "ManagedBy": "StockMarketAgent",
            },
        )

    payload = _described_change_set()
    payload["Tags"] = [
        {"Key": "Project", "Value": "stockmarketagent-public-demo"},
        {"Key": "ManagedBy", "Value": "cloudformation"},
        {"Key": "ProductSha", "Value": "f" * 40},
    ]
    with pytest.raises(AwsSafetyError, match="tags"):
        verify_change_set(
            payload,
            expected_arn=CHANGE_SET_ARN,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
            expected_change_set_type="UPDATE",
            expected_execution_status="AVAILABLE",
            expected_stack_tags={
                "Project": "stockmarketagent-public-demo",
                "ManagedBy": "StockMarketAgent",
            },
        )


def test_plan_semantics_cannot_be_rehashed_for_another_account() -> None:
    plan = create_plan_document(
        change_set_arn=CHANGE_SET_ARN,
        product_sha="a" * 40,
        expected_account_id=ACCOUNT,
        template_sha256="b" * 64,
        changes=[],
    )
    plan["expected_account_id"] = "999999999999"
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    with pytest.raises(AwsSafetyError, match="outside its account/region"):
        verify_plan_document(plan)


class _NoAwsCalls:
    def json(self, service: str, operation: str, *arguments: str) -> object:
        raise AssertionError(f"unexpected AWS JSON call: {service} {operation} {arguments}")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        raise AssertionError(f"unexpected AWS text call: {service} {operation} {arguments}")


def test_mutating_commands_require_exact_confirmation_before_aws_calls(tmp_path: Path) -> None:
    invoker = _NoAwsCalls()
    with pytest.raises(AwsSafetyError, match="exact DEPLOY"):
        deploy(
            invoker,
            expected_account_id=ACCOUNT,
            plan_path=tmp_path / "missing-plan.json",
            change_set_arn=CHANGE_SET_ARN,
            plan_sha256="a" * 64,
            confirmation="NO",
            template=tmp_path / "missing-template.yaml",
        )
    with pytest.raises(AwsSafetyError, match="exact SYNC_DELETE"):
        upload(
            invoker,
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            artifact_dir=tmp_path,
            confirmation="NO",
        )


class _UploadInvoker:
    def __init__(self) -> None:
        self.text_calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.calls: list[tuple[str, str, str]] = []

    def json(self, service: str, operation: str, *arguments: str) -> object:
        self.calls.append(("json", service, operation))
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo/test",
            }
        if (service, operation) == ("cloudformation", "describe-stacks"):
            return _stack_with_outputs()
        if (service, operation) == ("s3api", "get-bucket-encryption"):
            return {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                }
            }
        if (service, operation) == ("s3api", "get-bucket-versioning"):
            return {"Status": "Enabled"}
        if (service, operation) == ("s3api", "get-public-access-block"):
            return {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                }
            }
        if (service, operation) == ("s3api", "get-bucket-ownership-controls"):
            return {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}
        if (service, operation) == ("s3api", "list-objects-v2"):
            return {"KeyCount": 0}
        if (service, operation) == ("cloudfront", "create-invalidation"):
            return {"Invalidation": {"Id": "INV"}}
        return _DeploymentInvoker().json(service, operation, *arguments)

    def text(self, service: str, operation: str, *arguments: str) -> str:
        self.calls.append(("text", service, operation))
        self.text_calls.append((service, operation, arguments))
        return ""


def test_upload_dryrun_deletes_old_maps_and_cp_overwrites_immutable_metadata(
    tmp_path: Path,
) -> None:
    artifact = (tmp_path / "out").resolve()
    immutable = artifact / "_next/static"
    immutable.mkdir(parents=True)
    (artifact / "index.html").write_text("public", encoding="utf-8")
    (immutable / "app.js").write_text("static", encoding="utf-8")
    (artifact / "build-metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project": "StockMarketAgent",
                "product_commit": "a" * 40,
                "generated_at": "2026-07-31T01:05:00Z",
                "runtime_mode": "PUBLIC_STATIC",
                "release_tier": "DEV_ONLY",
                "data_mode": "DEMO_REPLAY",
                "instrument_id": "XSHG:603986",
            }
        ),
        encoding="utf-8",
    )
    invoker = _UploadInvoker()
    upload(
        invoker,
        expected_account_id=ACCOUNT,
        product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation="SYNC_DELETE",
    )
    assert len(invoker.text_calls) == 4
    dryrun, sync, immutable_cp, invalidation_wait = invoker.text_calls
    assert dryrun[:2] == ("s3", "sync")
    assert "--dryrun" in dryrun[2]
    assert "--delete" in dryrun[2]
    assert "--exclude" not in dryrun[2]
    assert sync[:2] == ("s3", "sync")
    assert "--delete" in sync[2]
    assert "--exclude" not in sync[2]
    assert immutable_cp[:2] == ("s3", "cp")
    assert "--recursive" in immutable_cp[2]
    assert "public,max-age=31536000,immutable" in immutable_cp[2]
    assert invalidation_wait[:2] == ("cloudfront", "wait")
    assert invalidation_wait[2] == (
        "invalidation-completed",
        "--distribution-id",
        "EDIST",
        "--id",
        "INV",
    )
    assert invoker.calls.index(("json", "cloudfront", "get-distribution")) < (
        invoker.calls.index(("json", "s3api", "list-objects-v2"))
    )


def test_upload_fails_closed_when_invalidation_id_is_missing(tmp_path: Path) -> None:
    artifact = (tmp_path / "out").resolve()
    immutable = artifact / "_next/static"
    immutable.mkdir(parents=True)
    (artifact / "index.html").write_text("public", encoding="utf-8")
    (immutable / "app.js").write_text("static", encoding="utf-8")
    (artifact / "build-metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project": "StockMarketAgent",
                "product_commit": "a" * 40,
                "generated_at": "2026-07-31T01:05:00Z",
                "runtime_mode": "PUBLIC_STATIC",
                "release_tier": "DEV_ONLY",
                "data_mode": "DEMO_REPLAY",
                "instrument_id": "XSHG:603986",
            }
        ),
        encoding="utf-8",
    )

    class MissingIdInvoker(_UploadInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("cloudfront", "create-invalidation"):
                return {"Invalidation": {}}
            return super().json(service, operation, *arguments)

    with pytest.raises(AwsSafetyError, match="omitted its Id"):
        upload(
            MissingIdInvoker(),
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation="SYNC_DELETE",
        )


def test_upload_refuses_full_site_replacement_after_initial_shell(tmp_path: Path) -> None:
    artifact = (tmp_path / "out").resolve()
    immutable = artifact / "_next/static"
    immutable.mkdir(parents=True)
    (artifact / "index.html").write_text("public", encoding="utf-8")
    (immutable / "app.js").write_text("static", encoding="utf-8")
    (artifact / "build-metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project": "StockMarketAgent",
                "product_commit": "a" * 40,
                "generated_at": "2026-08-04T01:05:00Z",
                "runtime_mode": "PUBLIC_STATIC",
                "release_tier": "DEV_ONLY",
                "data_mode": "DEMO_REPLAY",
                "instrument_id": "XSHG:603986",
            }
        ),
        encoding="utf-8",
    )

    class NonEmptyBucketInvoker(_UploadInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("s3api", "list-objects-v2"):
                return {"KeyCount": 1}
            return super().json(service, operation, *arguments)

    invoker = NonEmptyBucketInvoker()
    with pytest.raises(AwsSafetyError, match="FULL_SITE_UPLOAD_INITIAL_ONLY"):
        upload(
            invoker,
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation="SYNC_DELETE",
        )
    assert invoker.text_calls == []


class _DeploymentInvoker:
    def __init__(self, *, product_sha: str = "a" * 40) -> None:
        self.product_sha = product_sha

    @staticmethod
    def _cache_policy(
        ttls: tuple[int, int, int],
        *,
        enable_accept_encoding: bool = True,
    ) -> dict[str, object]:
        return {
            "CachePolicy": {
                "CachePolicyConfig": {
                    "MinTTL": ttls[0],
                    "DefaultTTL": ttls[1],
                    "MaxTTL": ttls[2],
                    "ParametersInCacheKeyAndForwardedToOrigin": {
                        "CookiesConfig": {"CookieBehavior": "none"},
                        "HeadersConfig": {"HeaderBehavior": "none"},
                        "QueryStringsConfig": {"QueryStringBehavior": "none"},
                        "EnableAcceptEncodingBrotli": enable_accept_encoding,
                        "EnableAcceptEncodingGzip": enable_accept_encoding,
                    },
                }
            }
        }

    def json(self, service: str, operation: str, *arguments: str) -> object:
        if (service, operation) == ("s3api", "get-bucket-encryption"):
            return {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                }
            }
        if (service, operation) == ("s3api", "get-bucket-versioning"):
            return {"Status": "Enabled"}
        if (service, operation) == ("s3api", "get-public-access-block"):
            return {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                }
            }
        if (service, operation) == ("s3api", "get-bucket-ownership-controls"):
            return {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}
        if (service, operation) == ("s3api", "get-bucket-policy"):
            return {
                "Policy": json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "DenyInsecureTransport",
                                "Effect": "Deny",
                                "Principal": "*",
                                "Action": "s3:*",
                                "Resource": [
                                    "arn:aws:s3:::project-bucket",
                                    "arn:aws:s3:::project-bucket/*",
                                ],
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            },
                            {
                                "Sid": "AllowOnlyThisCloudFrontDistribution",
                                "Effect": "Allow",
                                "Principal": {"Service": "cloudfront.amazonaws.com"},
                                "Action": "s3:GetObject",
                                "Resource": "arn:aws:s3:::project-bucket/*",
                                "Condition": {
                                    "StringEquals": {
                                        "AWS:SourceArn": (
                                            f"arn:aws:cloudfront::{ACCOUNT}:distribution/EDIST"
                                        )
                                    }
                                },
                            },
                        ],
                    }
                )
            }
        if (service, operation) == ("cloudfront", "get-distribution"):
            return {
                "Distribution": {
                    "Id": "EDIST",
                    "ARN": f"arn:aws:cloudfront::{ACCOUNT}:distribution/EDIST",
                    "Status": "Deployed",
                    "DomainName": "demo.cloudfront.net",
                    "DistributionConfig": {
                        "Enabled": True,
                        "HttpVersion": "http2and3",
                        "PriceClass": "PriceClass_200",
                        "DefaultRootObject": "index.html",
                        "IsIPV6Enabled": True,
                        "Aliases": {"Quantity": 0},
                        "Logging": {
                            "Enabled": False,
                            "IncludeCookies": False,
                            "Bucket": "",
                            "Prefix": "",
                        },
                        "WebACLId": "",
                        "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
                        "Origins": {
                            "Quantity": 1,
                            "Items": [
                                {
                                    "Id": "private-s3-origin",
                                    "DomainName": (
                                        "project-bucket.s3.ap-northeast-1.amazonaws.com"
                                    ),
                                    "OriginAccessControlId": "EOAC",
                                    "S3OriginConfig": {
                                        "OriginAccessIdentity": "",
                                        "OriginReadTimeout": 30,
                                    },
                                }
                            ],
                        },
                        "DefaultCacheBehavior": {
                            "AllowedMethods": {
                                "Quantity": 2,
                                "Items": ["GET", "HEAD"],
                                "CachedMethods": {
                                    "Quantity": 2,
                                    "Items": ["GET", "HEAD"],
                                },
                            },
                            "Compress": True,
                            "ViewerProtocolPolicy": "redirect-to-https",
                            "TargetOriginId": "private-s3-origin",
                            "CachePolicyId": "SHORT",
                            "ResponseHeadersPolicyId": "HEADERS",
                            "FunctionAssociations": {
                                "Quantity": 1,
                                "Items": [
                                    {
                                        "EventType": "viewer-request",
                                        "FunctionARN": (
                                            f"arn:aws:cloudfront::{ACCOUNT}:function/"
                                            f"{STACK_NAME}-route-rewrite"
                                        ),
                                    }
                                ],
                            },
                            "LambdaFunctionAssociations": {"Quantity": 0},
                        },
                        "CacheBehaviors": {
                            "Quantity": 3,
                            "Items": [
                                {
                                    "AllowedMethods": {
                                        "Quantity": 2,
                                        "Items": ["GET", "HEAD"],
                                        "CachedMethods": {
                                            "Quantity": 2,
                                            "Items": ["GET", "HEAD"],
                                        },
                                    },
                                    "Compress": True,
                                    "PathPattern": "data/current.json",
                                    "ViewerProtocolPolicy": "redirect-to-https",
                                    "TargetOriginId": "private-s3-origin",
                                    "CachePolicyId": "NO_CACHE",
                                    "ResponseHeadersPolicyId": "HEADERS",
                                    "FunctionAssociations": {"Quantity": 0},
                                    "LambdaFunctionAssociations": {"Quantity": 0},
                                },
                                {
                                    "AllowedMethods": {
                                        "Quantity": 2,
                                        "Items": ["GET", "HEAD"],
                                        "CachedMethods": {
                                            "Quantity": 2,
                                            "Items": ["GET", "HEAD"],
                                        },
                                    },
                                    "Compress": True,
                                    "PathPattern": "data/snapshots/*",
                                    "ViewerProtocolPolicy": "redirect-to-https",
                                    "TargetOriginId": "private-s3-origin",
                                    "CachePolicyId": "IMMUTABLE",
                                    "ResponseHeadersPolicyId": "HEADERS",
                                    "FunctionAssociations": {"Quantity": 0},
                                    "LambdaFunctionAssociations": {"Quantity": 0},
                                },
                                {
                                    "AllowedMethods": {
                                        "Quantity": 2,
                                        "Items": ["GET", "HEAD"],
                                        "CachedMethods": {
                                            "Quantity": 2,
                                            "Items": ["GET", "HEAD"],
                                        },
                                    },
                                    "Compress": True,
                                    "PathPattern": "_next/static/*",
                                    "ViewerProtocolPolicy": "redirect-to-https",
                                    "TargetOriginId": "private-s3-origin",
                                    "CachePolicyId": "IMMUTABLE",
                                    "ResponseHeadersPolicyId": "HEADERS",
                                    "FunctionAssociations": {"Quantity": 0},
                                    "LambdaFunctionAssociations": {"Quantity": 0},
                                },
                            ],
                        },
                        "CustomErrorResponses": {
                            "Quantity": 0,
                        },
                    },
                }
            }
        if (service, operation) == ("cloudfront", "get-origin-access-control"):
            return {
                "OriginAccessControl": {
                    "Id": "EOAC",
                    "OriginAccessControlConfig": {
                        "OriginAccessControlOriginType": "s3",
                        "SigningBehavior": "always",
                        "SigningProtocol": "sigv4",
                    },
                }
            }
        if (service, operation) == ("cloudfront", "get-cache-policy"):
            policy_id = arguments[arguments.index("--id") + 1]
            ttls = {
                "SHORT": (0, 60, 300),
                "NO_CACHE": (0, 0, 0),
                "IMMUTABLE": (31536000, 31536000, 31536000),
            }
            return self._cache_policy(
                ttls[policy_id],
                enable_accept_encoding=policy_id != "NO_CACHE",
            )
        if (service, operation) == ("cloudfront", "get-response-headers-policy"):
            return {
                "ResponseHeadersPolicy": {
                    "ResponseHeadersPolicyConfig": {
                        "SecurityHeadersConfig": {
                            "ContentTypeOptions": {"Override": True},
                            "FrameOptions": {"FrameOption": "DENY", "Override": True},
                            "StrictTransportSecurity": {
                                "AccessControlMaxAgeSec": 31536000,
                                "IncludeSubdomains": True,
                                "Override": True,
                                "Preload": True,
                            },
                            "ContentSecurityPolicy": {
                                "ContentSecurityPolicy": (
                                    "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
                                ),
                                "Override": True,
                            },
                        }
                    }
                }
            }
        if (service, operation) == ("s3api", "get-bucket-tagging"):
            return {"TagSet": _tag_records(_cloudformation_bucket_tags(self.product_sha))}
        if (service, operation) == ("cloudfront", "list-tags-for-resource"):
            return {"Tags": {"Items": _tag_records(_business_resource_tags(self.product_sha))}}
        raise AssertionError(f"unexpected AWS JSON call: {service} {operation} {arguments}")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        raise AssertionError(f"unexpected AWS text call: {service} {operation} {arguments}")


class _LegacyDeployInvoker(_DeploymentInvoker):
    def __init__(
        self,
        *,
        pre_stack_tags: dict[str, str] | None = None,
        post_stack_tags: dict[str, str] | None = None,
    ) -> None:
        super().__init__(product_sha="a" * 40)
        self.legacy_tags = {
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "cloudformation",
            "ProductSha": "f" * 40,
        }
        self.pre_stack_tags = self.legacy_tags if pre_stack_tags is None else pre_stack_tags
        self.post_stack_tags = self.legacy_tags if post_stack_tags is None else post_stack_tags
        self.describe_change_set_calls = 0
        self.describe_stack_calls = 0
        self.text_calls: list[tuple[str, str, tuple[str, ...]]] = []

    @staticmethod
    def _tag_records(tags: dict[str, str]) -> list[dict[str, str]]:
        return [{"Key": key, "Value": value} for key, value in tags.items()]

    def json(self, service: str, operation: str, *arguments: str) -> object:
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo/test",
            }
        if (service, operation) == ("cloudformation", "describe-change-set"):
            execution_status = (
                "AVAILABLE" if self.describe_change_set_calls == 0 else "EXECUTE_COMPLETE"
            )
            self.describe_change_set_calls += 1
            payload = _described_change_set(execution_status=execution_status)
            del payload["ChangeSetType"]
            payload["Tags"] = self._tag_records(self.legacy_tags)
            payload["Changes"] = _legacy_probe_changes()
            return payload
        if (service, operation) == ("cloudformation", "describe-stacks"):
            payload = _stack_with_outputs(product_sha="a" * 40)
            stack = payload["Stacks"][0]  # type: ignore[index]
            assert isinstance(stack, dict)
            stack_tags = (
                self.pre_stack_tags if self.describe_stack_calls == 0 else self.post_stack_tags
            )
            self.describe_stack_calls += 1
            stack["Tags"] = self._tag_records(stack_tags)
            return payload
        return super().json(service, operation, *arguments)

    def text(self, service: str, operation: str, *arguments: str) -> str:
        if (service, operation) in {
            ("cloudformation", "execute-change-set"),
            ("cloudformation", "wait"),
        }:
            self.text_calls.append((service, operation, arguments))
            return ""
        return super().text(service, operation, *arguments)


def _legacy_deploy_inputs(
    tmp_path: Path,
) -> tuple[Path, Path, str]:
    template = (tmp_path / "template.yaml").resolve()
    template.write_text("Resources: {}\n", encoding="utf-8")
    plan_document = create_plan_document(
        change_set_arn=CHANGE_SET_ARN,
        product_sha="a" * 40,
        expected_account_id=ACCOUNT,
        template_sha256=hashlib.sha256(template.read_bytes()).hexdigest(),
        changes=normalize_changes(
            {"Status": "CREATE_COMPLETE", "Changes": _legacy_probe_changes()}
        ),
        stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "cloudformation",
            "ProductSha": "f" * 40,
        },
    )
    plan_path = (tmp_path / "plan.json").resolve()
    plan_path.write_text(json.dumps(plan_document), encoding="utf-8")
    plan_sha = plan_document["plan_sha256"]
    assert isinstance(plan_sha, str)
    return template, plan_path, plan_sha


def test_deploy_legacy_happy_path_binds_pre_and_post_execute_stack_tags(
    tmp_path: Path,
) -> None:
    template, plan_path, plan_sha = _legacy_deploy_inputs(tmp_path)
    invoker = _LegacyDeployInvoker()

    deploy(
        invoker,
        expected_account_id=ACCOUNT,
        plan_path=plan_path,
        change_set_arn=CHANGE_SET_ARN,
        plan_sha256=plan_sha,
        confirmation="DEPLOY",
        template=template,
    )

    assert invoker.describe_change_set_calls == 2
    assert invoker.describe_stack_calls == 2
    assert invoker.text_calls == [
        (
            "cloudformation",
            "execute-change-set",
            ("--change-set-name", CHANGE_SET_ARN),
        ),
        (
            "cloudformation",
            "wait",
            ("stack-update-complete", "--stack-name", STACK_NAME),
        ),
    ]


def test_deploy_rejects_pre_execute_stack_tag_drift_without_mutation(
    tmp_path: Path,
) -> None:
    template, plan_path, plan_sha = _legacy_deploy_inputs(tmp_path)
    invoker = _LegacyDeployInvoker(
        pre_stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "StockMarketAgent",
        }
    )

    with pytest.raises(AwsSafetyError, match="current stack tags"):
        deploy(
            invoker,
            expected_account_id=ACCOUNT,
            plan_path=plan_path,
            change_set_arn=CHANGE_SET_ARN,
            plan_sha256=plan_sha,
            confirmation="DEPLOY",
            template=template,
        )
    assert invoker.describe_change_set_calls == 1
    assert invoker.describe_stack_calls == 1
    assert invoker.text_calls == []


def test_deploy_rechecks_retained_policy_content_before_execute(
    tmp_path: Path,
) -> None:
    api_origin_domain = "runtime-api.ap-northeast-1.elb.amazonaws.com"
    template = (tmp_path / "template.yaml").resolve()
    template.write_text("Resources: {}\n", encoding="utf-8")
    plan_document = create_plan_document(
        change_set_arn=CHANGE_SET_ARN,
        product_sha="a" * 40,
        expected_account_id=ACCOUNT,
        template_sha256=hashlib.sha256(template.read_bytes()).hexdigest(),
        changes=normalize_changes(
            {"Status": "CREATE_COMPLETE", "Changes": _legacy_probe_changes()}
        ),
        stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "cloudformation",
            "ProductSha": "f" * 40,
        },
        api_origin_domain=api_origin_domain,
        api_origin_secret_arn=ORIGIN_SECRET_ARN,
        api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
        api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
    )
    plan_path = (tmp_path / "plan.json").resolve()
    plan_path.write_text(json.dumps(plan_document), encoding="utf-8")
    plan_sha = plan_document["plan_sha256"]
    assert isinstance(plan_sha, str)

    class DriftedPolicyDeployInvoker(_LegacyDeployInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("cloudformation", "describe-change-set"):
                payload = super().json(service, operation, *arguments)
                assert isinstance(payload, dict)
                payload["Parameters"] = [
                    {"ParameterKey": "ProductSha", "ParameterValue": "a" * 40},
                    {"ParameterKey": "ApiOriginDomainName", "ParameterValue": api_origin_domain},
                    {
                        "ParameterKey": "ApiOriginVerifySecretArn",
                        "ParameterValue": ORIGIN_SECRET_ARN,
                    },
                    {
                        "ParameterKey": "PrivatePreviewApiNoCachePolicyId",
                        "ParameterValue": R2_NO_CACHE_POLICY_ID,
                    },
                    {
                        "ParameterKey": "PrivatePreviewApiOriginRequestPolicyId",
                        "ParameterValue": R2_ORIGIN_REQUEST_POLICY_ID,
                    },
                ]
                return payload
            if (service, operation) == ("cloudfront", "get-cache-policy"):
                return {
                    "CachePolicy": {
                        "CachePolicyConfig": {
                            "MinTTL": 0,
                            "DefaultTTL": 1,
                            "MaxTTL": 1,
                            "ParametersInCacheKeyAndForwardedToOrigin": {
                                "CookiesConfig": {"CookieBehavior": "none"},
                                "HeadersConfig": {"HeaderBehavior": "none"},
                                "QueryStringsConfig": {"QueryStringBehavior": "none"},
                                "EnableAcceptEncodingBrotli": False,
                                "EnableAcceptEncodingGzip": False,
                            },
                        }
                    }
                }
            return super().json(service, operation, *arguments)

    invoker = DriftedPolicyDeployInvoker()
    with pytest.raises(AwsSafetyError, match="R2 API cache policy"):
        deploy(
            invoker,
            expected_account_id=ACCOUNT,
            plan_path=plan_path,
            change_set_arn=CHANGE_SET_ARN,
            plan_sha256=plan_sha,
            confirmation="DEPLOY",
            template=template,
        )

    assert invoker.describe_change_set_calls == 1
    assert invoker.describe_stack_calls == 0
    assert invoker.text_calls == []


def test_deploy_rejects_post_execute_stack_tag_drift(tmp_path: Path) -> None:
    template, plan_path, plan_sha = _legacy_deploy_inputs(tmp_path)
    invoker = _LegacyDeployInvoker(
        post_stack_tags={
            "Project": "stockmarketagent-public-demo",
            "ManagedBy": "StockMarketAgent",
        }
    )

    with pytest.raises(AwsSafetyError, match="deployed stack tags"):
        deploy(
            invoker,
            expected_account_id=ACCOUNT,
            plan_path=plan_path,
            change_set_arn=CHANGE_SET_ARN,
            plan_sha256=plan_sha,
            confirmation="DEPLOY",
            template=template,
        )
    assert invoker.describe_change_set_calls == 2
    assert invoker.describe_stack_calls == 2
    assert [call[:2] for call in invoker.text_calls] == [
        ("cloudformation", "execute-change-set"),
        ("cloudformation", "wait"),
    ]


def test_post_deploy_readback_binds_all_security_resources_and_product_sha() -> None:
    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    _verify_deployed_resources(
        _DeploymentInvoker(),
        stack,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
    )
    with pytest.raises(AwsSafetyError, match="resource tags"):
        _verify_deployed_resources(
            _DeploymentInvoker(product_sha="b" * 40),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


def test_post_deploy_readback_rejects_distribution_wide_error_interception() -> None:
    class InterceptingErrorInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            payload = super().json(service, operation, *arguments)
            if (service, operation) == ("cloudfront", "get-distribution"):
                distribution = payload["Distribution"]  # type: ignore[index]
                distribution["DistributionConfig"]["CustomErrorResponses"] = {
                    "Quantity": 1,
                    "Items": [
                        {
                            "ErrorCode": 404,
                            "ResponseCode": "404",
                            "ResponsePagePath": "/404.html",
                            "ErrorCachingMinTTL": 0,
                        }
                    ],
                }
            return payload

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    with pytest.raises(AwsSafetyError, match="must not intercept API origin"):
        _verify_deployed_resources(
            InterceptingErrorInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


def test_post_deploy_readback_accepts_exact_optional_api_origin() -> None:
    api_origin_domain = "runtime-api.ap-northeast-1.elb.amazonaws.com"

    class ApiOriginInvoker(_DeploymentInvoker):
        r2_no_cache_policy_id = R2_NO_CACHE_POLICY_ID
        r2_origin_request_policy_id = R2_ORIGIN_REQUEST_POLICY_ID

        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("cloudfront", "get-origin-request-policy"):
                policy_id = arguments[arguments.index("--id") + 1]
                if policy_id == self.r2_origin_request_policy_id:
                    return {
                        "OriginRequestPolicy": {
                            "OriginRequestPolicyConfig": {
                                "HeadersConfig": {
                                    "HeaderBehavior": "allExcept",
                                    "Headers": {"Quantity": 1, "Items": ["Host"]},
                                },
                                "CookiesConfig": {"CookieBehavior": "none"},
                                "QueryStringsConfig": {"QueryStringBehavior": "all"},
                            }
                        }
                    }
                return {
                    "OriginRequestPolicy": {
                        "OriginRequestPolicyConfig": {
                            "HeadersConfig": {
                                "HeaderBehavior": "whitelist",
                                "Headers": {
                                    "Quantity": 4,
                                    "Items": [
                                        "Accept",
                                        "X-Request-ID",
                                        "X-Correlation-ID",
                                        "Last-Event-ID",
                                    ],
                                },
                            },
                            "CookiesConfig": {"CookieBehavior": "none"},
                            "QueryStringsConfig": {"QueryStringBehavior": "all"},
                        }
                    }
                }
            if (service, operation) == ("cloudfront", "get-cache-policy"):
                policy_id = arguments[arguments.index("--id") + 1]
                if policy_id == self.r2_no_cache_policy_id:
                    return {
                        "CachePolicy": {
                            "CachePolicyConfig": {
                                "MinTTL": 0,
                                "DefaultTTL": 0,
                                "MaxTTL": 0,
                                "ParametersInCacheKeyAndForwardedToOrigin": {
                                    "CookiesConfig": {"CookieBehavior": "none"},
                                    "HeadersConfig": {"HeaderBehavior": "none"},
                                    "QueryStringsConfig": {"QueryStringBehavior": "none"},
                                    "EnableAcceptEncodingBrotli": False,
                                    "EnableAcceptEncodingGzip": False,
                                },
                            }
                        }
                    }
            payload = super().json(service, operation, *arguments)
            if (service, operation) == ("cloudfront", "get-distribution"):
                distribution = payload["Distribution"]  # type: ignore[index]
                config = distribution["DistributionConfig"]
                origins = config["Origins"]
                origins["Quantity"] = 2
                origins["Items"].append(
                    {
                        "Id": "runtime-api-origin",
                        "DomainName": api_origin_domain,
                        "OriginAccessControlId": "",
                        "CustomOriginConfig": {
                            "HTTPPort": 80,
                            "HTTPSPort": 443,
                            "OriginProtocolPolicy": "http-only",
                            "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                            "OriginReadTimeout": 60,
                            "OriginKeepaliveTimeout": 5,
                        },
                        "CustomHeaders": {
                            "Quantity": 1,
                            "Items": [
                                {
                                    "HeaderName": "X-StockMarketAgent-Origin-Verify",
                                    "HeaderValuePresent": True,
                                }
                            ],
                        },
                    }
                )
                behaviors = config["CacheBehaviors"]
                behaviors["Quantity"] = 5
                behaviors["Items"].append(
                    {
                        "AllowedMethods": {
                            "Quantity": 7,
                            "Items": ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"],
                            "CachedMethods": {
                                "Quantity": 3,
                                "Items": ["GET", "HEAD", "OPTIONS"],
                            },
                        },
                        "Compress": False,
                        "PathPattern": "api/v2/*",
                        "ViewerProtocolPolicy": "redirect-to-https",
                        "TargetOriginId": "runtime-api-origin",
                        "CachePolicyId": self.r2_no_cache_policy_id,
                        "OriginRequestPolicyId": self.r2_origin_request_policy_id,
                        "ResponseHeadersPolicyId": "HEADERS",
                        "FunctionAssociations": {"Quantity": 0},
                        "LambdaFunctionAssociations": {"Quantity": 0},
                    }
                )
                behaviors["Items"].append(
                    {
                        "AllowedMethods": {
                            "Quantity": 3,
                            "Items": ["HEAD", "GET", "OPTIONS"],
                            "CachedMethods": {"Quantity": 2, "Items": ["HEAD", "GET"]},
                        },
                        "Compress": False,
                        "PathPattern": "api/*",
                        "ViewerProtocolPolicy": "redirect-to-https",
                        "TargetOriginId": "runtime-api-origin",
                        "CachePolicyId": "NO_CACHE",
                        "OriginRequestPolicyId": "API_ORIGIN_REQUEST",
                        "ResponseHeadersPolicyId": "HEADERS",
                        "FunctionAssociations": {"Quantity": 0},
                        "LambdaFunctionAssociations": {"Quantity": 0},
                    }
                )
            return payload

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    stack["Parameters"] = [
        {"ParameterKey": "ProductSha", "ParameterValue": "a" * 40},
        {"ParameterKey": "ApiOriginDomainName", "ParameterValue": api_origin_domain},
        {"ParameterKey": "ApiOriginVerifySecretArn", "ParameterValue": ORIGIN_SECRET_ARN},
        {
            "ParameterKey": "PrivatePreviewApiNoCachePolicyId",
            "ParameterValue": R2_NO_CACHE_POLICY_ID,
        },
        {
            "ParameterKey": "PrivatePreviewApiOriginRequestPolicyId",
            "ParameterValue": R2_ORIGIN_REQUEST_POLICY_ID,
        },
    ]
    _verify_deployed_resources(
        ApiOriginInvoker(),
        stack,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
        expected_api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
        expected_api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
    )

    class MismatchedDistributionPolicyInvoker(ApiOriginInvoker):
        r2_no_cache_policy_id = "11111111-1111-4111-8111-111111111111"

    with pytest.raises(AwsSafetyError, match="R2 API behavior"):
        _verify_deployed_resources(
            MismatchedDistributionPolicyInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
            expected_api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
            expected_api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
        )

    stack["Parameters"][-1]["ParameterValue"] = (  # type: ignore[index]
        "22222222-2222-4222-8222-222222222222"
    )
    with pytest.raises(AwsSafetyError, match="parameters differ from plan"):
        _verify_deployed_resources(
            ApiOriginInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
            expected_api_no_cache_policy_id=R2_NO_CACHE_POLICY_ID,
            expected_api_origin_request_policy_id=R2_ORIGIN_REQUEST_POLICY_ID,
        )


def test_post_deploy_readback_accepts_business_only_bucket_tags() -> None:
    class BusinessOnlyBucketTagsInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("s3api", "get-bucket-tagging"):
                return {"TagSet": _tag_records(_business_resource_tags(self.product_sha))}
            return super().json(service, operation, *arguments)

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    _verify_deployed_resources(
        BusinessOnlyBucketTagsInvoker(),
        stack,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
    )


@pytest.mark.parametrize(
    "bucket_tags",
    [
        {**_cloudformation_bucket_tags(), "ProductSha": "b" * 40},
        {**_cloudformation_bucket_tags(), "aws:cloudformation:stack-name": "wrong-stack"},
        {**_cloudformation_bucket_tags(), "aws:cloudformation:logical-id": "WrongBucket"},
        {
            **_cloudformation_bucket_tags(),
            "aws:cloudformation:stack-id": STACK_ID.replace(
                "00000000-0000-4000-8000-000000000002",
                "00000000-0000-4000-8000-000000000003",
            ),
        },
        {
            key: value
            for key, value in _cloudformation_bucket_tags().items()
            if key != "aws:cloudformation:stack-id"
        },
        {**_cloudformation_bucket_tags(), "Unexpected": "value"},
        {
            **_business_resource_tags(),
            "aws:cloudformationx:stack-name": STACK_NAME,
            "aws:cloudformation:logical-id": "PublicDemoBucket",
            "aws:cloudformation:stack-id": STACK_ID,
        },
    ],
    ids=[
        "wrong-product-sha",
        "wrong-stack-name",
        "wrong-logical-id",
        "wrong-stack-id",
        "missing-ownership-tag",
        "extra-tag",
        "pseudo-namespace",
    ],
)
def test_post_deploy_readback_rejects_nonexact_bucket_tags(
    bucket_tags: dict[str, str],
) -> None:
    class MutatedBucketTagsInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("s3api", "get-bucket-tagging"):
                return {"TagSet": _tag_records(bucket_tags)}
            return super().json(service, operation, *arguments)

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    with pytest.raises(AwsSafetyError, match="resource tags"):
        _verify_deployed_resources(
            MutatedBucketTagsInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


def test_post_deploy_readback_keeps_cloudfront_tags_business_only() -> None:
    class CloudFrontOwnershipTagsInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("cloudfront", "list-tags-for-resource"):
                return {"Tags": {"Items": _tag_records(_cloudformation_bucket_tags())}}
            return super().json(service, operation, *arguments)

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    with pytest.raises(AwsSafetyError, match="resource tags"):
        _verify_deployed_resources(
            CloudFrontOwnershipTagsInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


def test_post_deploy_readback_accepts_legacy_omitted_s3_origin_timeout() -> None:
    class OmittedTimeoutInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            payload = super().json(service, operation, *arguments)
            if (service, operation) == ("cloudfront", "get-distribution"):
                distribution = payload["Distribution"]  # type: ignore[index]
                origins = distribution["DistributionConfig"]["Origins"]["Items"]
                origins[0]["S3OriginConfig"] = {"OriginAccessIdentity": ""}
            return payload

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    _verify_deployed_resources(
        OmittedTimeoutInvoker(),
        stack,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
    )


@pytest.mark.parametrize(
    "s3_origin_config",
    [
        {"OriginAccessIdentity": "legacy-oai", "OriginReadTimeout": 30},
        {"OriginAccessIdentity": "", "OriginReadTimeout": 29},
        {"OriginAccessIdentity": "", "OriginReadTimeout": "30"},
        {"OriginAccessIdentity": "", "OriginReadTimeout": 30.0},
        {
            "OriginAccessIdentity": "",
            "OriginReadTimeout": 30,
            "Unexpected": "value",
        },
    ],
)
def test_post_deploy_readback_rejects_unlocked_s3_origin_config(
    s3_origin_config: dict[str, object],
) -> None:
    class MutatedOriginInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            payload = super().json(service, operation, *arguments)
            if (service, operation) == ("cloudfront", "get-distribution"):
                distribution = payload["Distribution"]  # type: ignore[index]
                origins = distribution["DistributionConfig"]["Origins"]["Items"]
                origins[0]["S3OriginConfig"] = s3_origin_config
            return payload

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    with pytest.raises(AwsSafetyError, match="locked private S3 origin"):
        _verify_deployed_resources(
            MutatedOriginInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


def test_legacy_stack_product_sha_is_historical_not_deployment_authority() -> None:
    stack_payload = _stack_with_outputs(product_sha="a" * 40)
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    stack["Tags"] = _legacy_stack(product_sha="f" * 40)["Stacks"][0]["Tags"]  # type: ignore[index]

    verified_stack = verify_existing_stack(stack_payload)
    _verify_deployed_resources(
        _DeploymentInvoker(product_sha="a" * 40),
        verified_stack,
        expected_account_id=ACCOUNT,
        expected_product_sha="a" * 40,
    )


@pytest.mark.parametrize(
    ("behavior_name", "field", "value"),
    [
        (
            "DefaultCacheBehavior",
            "AllowedMethods",
            {
                "Quantity": 3,
                "Items": ["GET", "HEAD", "POST"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
        ),
        (
            "DefaultCacheBehavior",
            "CachedMethods",
            {"Quantity": 1, "Items": ["GET"]},
        ),
        ("DefaultCacheBehavior", "Compress", False),
        (
            "DefaultCacheBehavior",
            "LambdaFunctionAssociations",
            {
                "Quantity": 1,
                "Items": [{"EventType": "viewer-request", "LambdaFunctionARN": "unsafe"}],
            },
        ),
        (
            "DefaultCacheBehavior",
            "FunctionAssociations",
            {
                "Quantity": 1,
                "Items": [{"EventType": "viewer-response", "FunctionARN": "unsafe"}],
            },
        ),
        (
            "CacheBehaviors",
            "AllowedMethods",
            {
                "Quantity": 3,
                "Items": ["GET", "HEAD", "OPTIONS"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            },
        ),
        (
            "CacheBehaviors",
            "CachedMethods",
            {"Quantity": 1, "Items": ["GET"]},
        ),
        ("CacheBehaviors", "Compress", False),
        (
            "CacheBehaviors",
            "LambdaFunctionAssociations",
            {
                "Quantity": 1,
                "Items": [{"EventType": "origin-request", "LambdaFunctionARN": "unsafe"}],
            },
        ),
        (
            "CacheBehaviors",
            "FunctionAssociations",
            {
                "Quantity": 1,
                "Items": [{"EventType": "viewer-response", "FunctionARN": "unsafe"}],
            },
        ),
    ],
)
def test_post_deploy_readback_rejects_mutated_cache_behavior_security(
    behavior_name: str,
    field: str,
    value: object,
) -> None:
    class MutatedDeploymentInvoker(_DeploymentInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            payload = super().json(service, operation, *arguments)
            if (service, operation) != ("cloudfront", "get-distribution"):
                return payload
            distribution = payload["Distribution"]  # type: ignore[index]
            config = distribution["DistributionConfig"]  # type: ignore[index]
            if behavior_name == "DefaultCacheBehavior":
                behavior = config[behavior_name]
            else:
                behavior = config[behavior_name]["Items"][0]
            if field == "CachedMethods":
                behavior["AllowedMethods"][field] = value
            else:
                behavior[field] = value
            return payload

    stack_payload = _stack_with_outputs()
    stack = stack_payload["Stacks"][0]  # type: ignore[index]
    assert isinstance(stack, dict)
    with pytest.raises(AwsSafetyError):
        _verify_deployed_resources(
            MutatedDeploymentInvoker(),
            stack,
            expected_account_id=ACCOUNT,
            expected_product_sha="a" * 40,
        )


class _SmokeInvoker:
    def json(self, service: str, operation: str, *arguments: str) -> object:
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo/test",
            }
        if (service, operation) == ("cloudformation", "describe-stacks"):
            return _stack_with_outputs()
        return _DeploymentInvoker().json(service, operation, *arguments)

    def text(self, service: str, operation: str, *arguments: str) -> str:
        raise AssertionError(f"unexpected AWS text call: {service} {operation} {arguments}")


def test_smoke_casefolds_and_requires_nosniff_csp_and_hsts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    security = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
        ),
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    }
    requested_urls: list[str] = []

    def success(url: str, *, expected_host: str) -> tuple[int, bytes, dict[str, str]]:
        requested_urls.append(url)
        assert expected_host in {
            "demo.cloudfront.net",
            "project-bucket.s3.ap-northeast-1.amazonaws.com",
        }
        if "s3.ap-northeast-1.amazonaws.com" in url:
            return 403, b"private", {}
        if url.endswith("build-metadata.json"):
            return 200, json.dumps({"product_commit": "a" * 40}).encode(), security
        if url.endswith("/missing/"):
            return 404, b"not found", security
        if "/evidence/" in url:
            return 200, "正在检查这条证据".encode(), security
        if url.endswith("/details/"):
            return 200, "为什么得到这个结果".encode(), security
        return 200, b"ok", security

    monkeypatch.setattr(aws_module, "_https_get", success)
    smoke(
        _SmokeInvoker(),
        expected_account_id=ACCOUNT,
        product_sha="a" * 40,
        evidence_id="00000000-0000-0000-0000-000000001001",
    )
    assert "https://demo.cloudfront.net/details/" in requested_urls

    def missing_details_csp(
        url: str,
        *,
        expected_host: str,
    ) -> tuple[int, bytes, dict[str, str]]:
        status, body, headers = success(url, expected_host=expected_host)
        headers = dict(headers)
        if url.endswith("/details/"):
            headers.pop("Content-Security-Policy", None)
        return status, body, headers

    monkeypatch.setattr(aws_module, "_https_get", missing_details_csp)
    with pytest.raises(AwsSafetyError, match="security headers"):
        smoke(
            _SmokeInvoker(),
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            evidence_id="00000000-0000-0000-0000-000000001001",
        )


def test_smoke_requires_details_page_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    security = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
        ),
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    }

    def wrong_details_marker(
        url: str,
        *,
        expected_host: str,
    ) -> tuple[int, bytes, dict[str, str]]:
        del expected_host
        if "s3.ap-northeast-1.amazonaws.com" in url:
            return 403, b"private", {}
        if url.endswith("build-metadata.json"):
            return 200, json.dumps({"product_commit": "a" * 40}).encode(), security
        if url.endswith("/missing/"):
            return 404, b"not found", security
        if "/evidence/" in url:
            return 200, "正在检查这条证据".encode(), security
        if url.endswith("/details/"):
            return 200, b"generic static page", security
        return 200, b"ok", security

    monkeypatch.setattr(aws_module, "_https_get", wrong_details_marker)
    with pytest.raises(AwsSafetyError, match="details page marker"):
        smoke(
            _SmokeInvoker(),
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            evidence_id="00000000-0000-0000-0000-000000001001",
        )


@pytest.mark.parametrize(
    "public_url",
    [
        "https://user@demo.cloudfront.net",
        "https://demo.cloudfront.net:443",
        "https://demo.cloudfront.net/path",
        "https://demo.cloudfront.net?query=1",
        "https://demo.cloudfront.net#fragment",
        "https://evil.example",
    ],
)
def test_smoke_rejects_public_url_not_equal_to_distribution_origin(
    monkeypatch: pytest.MonkeyPatch,
    public_url: str,
) -> None:
    class InvalidUrlInvoker(_SmokeInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            if (service, operation) == ("cloudformation", "describe-stacks"):
                payload = _stack_with_outputs()
                outputs = payload["Stacks"][0]["Outputs"]  # type: ignore[index]
                assert isinstance(outputs, list)
                for output in outputs:
                    if output["OutputKey"] == "PublicDemoUrl":
                        output["OutputValue"] = public_url
                return payload
            return super().json(service, operation, *arguments)

    monkeypatch.setattr(
        aws_module,
        "_https_get",
        lambda *args, **kwargs: pytest.fail("HTTP must not start for an invalid public URL"),
    )
    with pytest.raises(AwsSafetyError, match="PublicDemoUrl"):
        smoke(
            InvalidUrlInvoker(),
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            evidence_id="00000000-0000-0000-0000-000000001001",
        )


def test_redirect_handler_rejects_cross_host_before_following() -> None:
    handler = aws_module._RejectRedirects("demo.cloudfront.net")
    request = aws_module.Request("https://demo.cloudfront.net/")
    with pytest.raises(AwsSafetyError, match="exact HTTPS host"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://evil.example/collect",
        )
    assert (
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "/same-host",
        )
        is None
    )


@pytest.mark.parametrize(
    "evidence_id",
    [
        "..",
        "../00000000-0000-0000-0000-000000001001",
        "00000000-0000-0000-0000-000000001001/extra",
        "%2e%2e",
        "not-a-uuid",
        "abcdefab-cdef-4abc-8def-abcdefabcdef".upper(),
    ],
)
def test_smoke_rejects_noncanonical_or_multisegment_evidence_id(
    monkeypatch: pytest.MonkeyPatch,
    evidence_id: str,
) -> None:
    monkeypatch.setattr(
        aws_module,
        "_https_get",
        lambda *args, **kwargs: pytest.fail("HTTP must not start for an invalid evidence ID"),
    )
    with pytest.raises(AwsSafetyError, match=r"evidence ID"):
        smoke(
            _SmokeInvoker(),
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            evidence_id=evidence_id,
        )


def test_smoke_requires_evidence_loader_body_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    security = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
        ),
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    }

    def wrong_evidence(url: str, *, expected_host: str) -> tuple[int, bytes, dict[str, str]]:
        del expected_host
        if "s3.ap-northeast-1.amazonaws.com" in url:
            return 403, b"private", {}
        if url.endswith("build-metadata.json"):
            return 200, json.dumps({"product_commit": "a" * 40}).encode(), security
        if url.endswith("/missing/"):
            return 404, b"not found", security
        if url.endswith("/details/"):
            return 200, "为什么得到这个结果".encode(), security
        return 200, b"another evidence page", security

    monkeypatch.setattr(aws_module, "_https_get", wrong_evidence)
    with pytest.raises(AwsSafetyError, match="loader marker"):
        smoke(
            _SmokeInvoker(),
            expected_account_id=ACCOUNT,
            product_sha="a" * 40,
            evidence_id="00000000-0000-0000-0000-000000001001",
        )


def _live_artifact(tmp_path: Path) -> Path:
    artifact = (tmp_path / "live-out").resolve()
    (artifact / "_next/static").mkdir(parents=True)
    snapshot = artifact / "data/snapshots/00000000-0000-4000-8000-000000000001"
    snapshot.mkdir(parents=True)
    (artifact / "index.html").write_text("live shell", encoding="utf-8")
    (artifact / "_next/static/app.js").write_text("static", encoding="utf-8")
    (artifact / "build-metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project": "StockMarketAgent",
                "product_commit": "a" * 40,
                "generated_at": "2026-07-31T01:05:00Z",
                "runtime_mode": "PUBLIC_STATIC",
                "release_tier": "DEV_ONLY",
                "data_mode": "DEMO_REPLAY",
                "instrument_id": "XSHG:603986",
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "manifest.json").write_text('{"snapshot":"immutable"}', encoding="utf-8")
    (snapshot / "briefing.json").write_text('{"briefing":"live"}', encoding="utf-8")
    (artifact / "data/current.json").write_text(
        '{"snapshot_id":"00000000-0000-4000-8000-000000000001"}',
        encoding="utf-8",
    )
    return artifact


def _live_snapshot(tmp_path: Path) -> Path:
    snapshot = (tmp_path / "live-snapshot").resolve()
    (snapshot / "evidence").mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "project": "StockMarketAgent",
        "product_commit": "a" * 40,
        "generated_at": "2026-07-31T01:05:00Z",
        "runtime_mode": "PUBLIC_STATIC",
        "release_tier": "DEV_ONLY",
        "data_mode": "DEMO_REPLAY",
        "instrument_id": "XSHG:603986",
    }
    files: dict[str, object] = {
        "active-target.json": {"data": {"found": True}},
        "latest-briefing.json": {
            "data": {
                "runtime_mode": "public_static",
                "release_tier": "dev_only",
                "rights_status": "not_applicable",
            }
        },
        "evidence-index.json": {"schema_version": "1.0", "events": []},
        "build-metadata.json": metadata,
        "evidence/10000000-0000-4000-8000-000000000001.json": {
            "data": {
                "runtime_mode": "public_static",
                "release_tier": "dev_only",
                "rights_status": "not_applicable",
                "source_url": (
                    "https://example.com/stockmarketagent/synthetic-evidence/"
                    "00000000-0000-4000-8000-000000000001"
                ),
            }
        },
    }
    for name, payload in files.items():
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload), encoding="utf-8")
    return snapshot


def _run_frozen_shell_upgrade(
    invoker: aws_module.AwsInvoker,
    *,
    expected_account_id: str,
    old_product_sha: str,
    new_product_sha: str,
    artifact_dir: Path,
    confirmation: str,
    captured_artifact_paths: list[Path] | None = None,
) -> dict[str, str]:
    snapshot = _live_snapshot(artifact_dir.parent)
    with TemporaryDirectory(prefix="test-frozen-shell-") as temporary:
        staging = Path(temporary).resolve()
        frozen_snapshot = aws_module._freeze_public_tree(
            snapshot,
            staging / "snapshot",
            kind="snapshot",
        )
        frozen_artifact = aws_module._freeze_shell_artifact(
            artifact_dir,
            staging / "artifact",
        )
        release_material = aws_module._build_shell_release_material(
            old_product_sha=old_product_sha,
            new_product_sha=new_product_sha,
            source_tree_sha="b" * 40,
            snapshot_dir=frozen_snapshot,
            artifact_dir=frozen_artifact,
        )
        if captured_artifact_paths is not None:
            captured_artifact_paths.append(frozen_artifact)
        return aws_module._upgrade_existing_shell_frozen(
            invoker,
            expected_account_id=expected_account_id,
            old_product_sha=old_product_sha,
            new_product_sha=new_product_sha,
            release_material=release_material,
            confirmation=confirmation,
        )


class _UpgradeInvoker(_UploadInvoker):
    def __init__(self) -> None:
        super().__init__()
        self.objects: dict[str, dict[str, object]] = {
            "legacy/keep.txt": {
                "ETag": '"legacy"',
                "VersionId": "legacy-version",
                "ContentLength": 4,
                "Metadata": {"sha256": "legacy"},
            }
        }
        self.shell_sha = "f" * 40
        self.invalidation_count = 0
        self.invalidations: dict[str, str] = {}
        self.invalidation_paths: tuple[str, ...] = ()
        self.fail_wait_once = False
        self.fail_marker_after_invalidation_once = False
        self.put_order: list[str] = []
        self.put_arguments: dict[str, tuple[str, ...]] = {}
        self.operator_calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.version_objects: dict[tuple[str, str], tuple[dict[str, object], bytes]] = {}
        self.corrupt_head_prefix: str | None = None
        self.corrupt_head_versioned: bool | None = None
        self.corrupt_head_field: str | None = None
        self._version = 0

    @staticmethod
    def _metadata(arguments: tuple[str, ...]) -> dict[str, str]:
        raw = arguments[arguments.index("--metadata") + 1]
        return dict(item.split("=", 1) for item in raw.split(","))

    def _head_value(
        self,
        *,
        key: str,
        versioned: bool,
        value: dict[str, object],
    ) -> dict[str, object]:
        if (
            self.corrupt_head_prefix is not None
            and key.startswith(self.corrupt_head_prefix)
            and self.corrupt_head_versioned is versioned
            and self.corrupt_head_field is not None
        ):
            value[self.corrupt_head_field] = "wrong-remote-property"
        return value

    def json(self, service: str, operation: str, *arguments: str) -> object:
        self.operator_calls.append((service, operation, arguments))
        if (service, operation) == ("s3api", "head-object"):
            key = arguments[arguments.index("--key") + 1]
            if "--version-id" in arguments:
                version_id = arguments[arguments.index("--version-id") + 1]
                versioned = self.version_objects.get((key, version_id))
                if versioned is None:
                    raise subprocess.CalledProcessError(
                        1,
                        ["aws", "s3api", "head-object"],
                        stderr="404 Not Found",
                    )
                return self._head_value(
                    key=key,
                    versioned=True,
                    value=dict(versioned[0]),
                )
            if key not in self.objects:
                raise subprocess.CalledProcessError(
                    1,
                    ["aws", "s3api", "head-object"],
                    stderr="404 Not Found",
                )
            return self._head_value(
                key=key,
                versioned=False,
                value=dict(self.objects[key]),
            )
        if (service, operation) == ("s3api", "put-object"):
            key = arguments[arguments.index("--key") + 1]
            current = self.objects.get(key)
            if "--if-none-match" in arguments and current is not None:
                raise subprocess.CalledProcessError(1, ["aws", "s3api", "put-object"])
            if "--if-match" in arguments:
                expected = arguments[arguments.index("--if-match") + 1]
                if current is None or current["ETag"] != expected:
                    raise subprocess.CalledProcessError(1, ["aws", "s3api", "put-object"])
            body = b""
            if "--body" in arguments:
                body_path = Path(arguments[arguments.index("--body") + 1])
                body = body_path.read_bytes()
                if key == "build-metadata.json":
                    value = json.loads(body.decode("utf-8"))
                    self.shell_sha = value["product_commit"]
            metadata = self._metadata(arguments)
            if (
                key == aws_module.SHELL_UPGRADE_MARKER_KEY
                and "invalidation-id" in metadata
                and self.fail_marker_after_invalidation_once
            ):
                self.fail_marker_after_invalidation_once = False
                raise subprocess.CalledProcessError(1, ["aws", "s3api", "put-object"])
            self._version += 1
            value = {
                "ETag": f'"etag-{self._version}"',
                "VersionId": f"version-{self._version}",
                "ContentLength": len(body),
                "Metadata": metadata,
            }
            for argument, response_key in (
                ("--content-type", "ContentType"),
                ("--cache-control", "CacheControl"),
                ("--server-side-encryption", "ServerSideEncryption"),
            ):
                if argument in arguments:
                    value[response_key] = arguments[arguments.index(argument) + 1]
            self.objects[key] = value
            self.version_objects[(key, str(value["VersionId"]))] = (dict(value), body)
            self.put_order.append(key)
            self.put_arguments[key] = arguments
            return {"ETag": value["ETag"], "VersionId": value["VersionId"]}
        if (service, operation) == ("s3api", "get-object"):
            key = arguments[arguments.index("--key") + 1]
            version_id = arguments[arguments.index("--version-id") + 1]
            versioned = self.version_objects.get((key, version_id))
            if versioned is None:
                raise subprocess.CalledProcessError(1, ["aws", "s3api", "get-object"])
            Path(arguments[-1]).write_bytes(versioned[1])
            return dict(versioned[0])
        if (service, operation) == ("cloudfront", "create-invalidation"):
            batch = json.loads(arguments[arguments.index("--invalidation-batch") + 1])
            caller_reference = batch["CallerReference"]
            items = tuple(batch["Paths"]["Items"])
            assert batch["Paths"]["Quantity"] == len(items)
            assert "/*" not in items
            self.invalidation_paths = items
            if caller_reference not in self.invalidations:
                self.invalidation_count += 1
                self.invalidations[caller_reference] = "INV-ONCE"
            return {"Invalidation": {"Id": self.invalidations[caller_reference]}}
        return super().json(service, operation, *arguments)

    def text(self, service: str, operation: str, *arguments: str) -> str:
        self.operator_calls.append((service, operation, arguments))
        if (service, operation) == ("s3", "cp") and arguments[0].startswith("s3://"):
            return json.dumps({"project": "StockMarketAgent", "product_commit": self.shell_sha})
        if (service, operation) == ("cloudfront", "wait") and self.fail_wait_once:
            self.fail_wait_once = False
            raise subprocess.CalledProcessError(1, ["aws", "cloudfront", "wait"])
        return super().text(service, operation, *arguments)


def _install_existing_shell_object(
    invoker: _UpgradeInvoker,
    *,
    key: str,
    body: bytes,
    content_type: str,
    cache_control: str,
    server_side_encryption: str,
) -> None:
    version_id = f"existing-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
    value: dict[str, object] = {
        "ETag": f'"{hashlib.sha256(body).hexdigest()[:32]}"',
        "VersionId": version_id,
        "ContentLength": len(body),
        "Metadata": {"sha256": hashlib.sha256(body).hexdigest()},
        "ContentType": content_type,
        "CacheControl": cache_control,
        "ServerSideEncryption": server_side_encryption,
    }
    invoker.objects[key] = dict(value)
    invoker.version_objects[(key, version_id)] = (dict(value), body)


def test_existing_shell_release_builds_and_receipts_one_frozen_source_pair_before_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = (tmp_path / "repository").resolve()
    (repository / "apps/web").mkdir(parents=True)
    snapshot = _live_snapshot(tmp_path)
    artifact = _live_artifact(tmp_path)
    invoker = _UpgradeInvoker()
    source_checks: list[tuple[Path, str]] = []
    builder_inputs: list[tuple[Path, Path, str]] = []
    local_events: list[str] = []
    material_builder = aws_module._build_shell_release_material

    def verify_source(*, repository_root: Path, product_sha: str) -> str:
        assert invoker.operator_calls == []
        local_events.append("source")
        source_checks.append((repository_root, product_sha))
        return "b" * 40

    def builder(repository_root: Path, frozen_snapshot: Path, product_sha: str) -> Path:
        local_events.append("build")
        builder_inputs.append((repository_root, frozen_snapshot, product_sha))
        assert frozen_snapshot != snapshot
        assert (frozen_snapshot / "build-metadata.json").is_file()
        return artifact

    def build_material(**kwargs: object) -> object:
        local_events.append("material")
        return material_builder(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(aws_module, "verify_shell_release_source", verify_source)
    monkeypatch.setattr(aws_module, "_build_shell_release_material", build_material)
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    result = aws_module.upgrade_existing_shell(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        repository_root=repository,
        snapshot_dir=snapshot,
        confirmation=confirmation,
        builder=builder,
    )
    assert result["InvalidationId"] == "INV-ONCE"
    assert source_checks == [(repository, "a" * 40), (repository, "a" * 40)]
    assert local_events == ["source", "build", "material", "source"]
    assert len(builder_inputs) == 1
    receipt_keys = [
        key for key in invoker.put_order if key.startswith("ops/live-shell-upgrade/receipts/")
    ]
    assert len(receipt_keys) == 1
    assert invoker.put_order.index(receipt_keys[0]) < invoker.put_order.index(
        aws_module.SHELL_UPGRADE_MARKER_KEY
    )
    marker_metadata = invoker.objects[aws_module.SHELL_UPGRADE_MARKER_KEY]["Metadata"]
    assert isinstance(marker_metadata, dict)
    assert marker_metadata["release-receipt-key"] == receipt_keys[0]
    assert len(marker_metadata["release-receipt-sha256"]) == 64
    assert len(marker_metadata["intent-sha256"]) == 64
    receipt_version = marker_metadata["release-receipt-version-id"]
    receipt_body = invoker.version_objects[(receipt_keys[0], receipt_version)][1]
    receipt_document = json.loads(receipt_body)
    assert receipt_document["intent"]["source_tree_sha"] == "b" * 40
    assert receipt_document["intent"]["deployment_target"] == {
        "expected_account_id": ACCOUNT,
        "stack_id": STACK_ID,
        "bucket": "project-bucket",
        "distribution_id": "EDIST",
    }
    assert result["ShellReleaseReceiptVersionId"] == receipt_version


def test_existing_shell_release_blocks_dirty_or_mismatched_source_before_build_and_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = (tmp_path / "repository").resolve()
    repository.mkdir()
    snapshot = _live_snapshot(tmp_path)
    invoker = _UpgradeInvoker()
    builder_called = False

    def blocked_source(**_kwargs: object) -> None:
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_NOT_CLEAN")

    def builder(_repository: Path, _snapshot: Path, _sha: str) -> Path:
        nonlocal builder_called
        builder_called = True
        raise AssertionError("builder must not start")

    monkeypatch.setattr(aws_module, "verify_shell_release_source", blocked_source)
    with pytest.raises(AwsSafetyError, match="SOURCE_NOT_CLEAN"):
        aws_module.upgrade_existing_shell(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            repository_root=repository,
            snapshot_dir=snapshot,
            confirmation=f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}",
            builder=builder,
        )
    assert builder_called is False
    assert invoker.operator_calls == []


def test_existing_shell_cli_and_make_target_accept_only_repository_and_snapshot() -> None:
    arguments = [
        "--expected-account-id",
        ACCOUNT,
        "upgrade-existing-shell",
        "--old-product-sha",
        "f" * 40,
        "--new-product-sha",
        "a" * 40,
        "--repository-root",
        "/operator/repository",
        "--snapshot-dir",
        "/operator/snapshot",
        "--confirm",
        f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}",
    ]
    parsed = aws_module._parser().parse_args(arguments)
    assert parsed.repository_root == Path("/operator/repository")
    assert parsed.snapshot_dir == Path("/operator/snapshot")
    with pytest.raises(SystemExit):
        aws_module._parser().parse_args([*arguments[:-2], "--artifact-dir", "/operator/out"])

    makefile = Path("Makefile").read_text(encoding="utf-8")
    target = makefile.split("public-demo-upgrade-existing-shell:\n", 1)[1].split(
        "\npublic-demo-smoke:", 1
    )[0]
    assert target.count("-m scripts.public_demo_aws") == 1
    assert "scripts.public_demo_artifacts" not in target
    assert '--repository-root "$(CURDIR)"' in target
    assert '--snapshot-dir "$(PUBLIC_DEMO_SNAPSHOT_DIR)"' in target
    assert "--artifact-dir" not in target


def test_existing_shell_upgrade_is_nondestructive_orders_routes_last_and_is_cli_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _live_artifact(tmp_path)
    invoker = _UpgradeInvoker()
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    result = _run_frozen_shell_upgrade(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation=confirmation,
    )
    assert result["InvalidationId"] == "INV-ONCE"
    assert invoker.invalidation_count == 1
    assert invoker.invalidation_paths == ("/", "/build-metadata.json", "/index.html")
    shell_puts = [key for key in invoker.put_order if not key.startswith("ops/")]
    assert shell_puts[0] == "_next/static/app.js"
    assert shell_puts[-2:] == ["index.html", "build-metadata.json"]
    assert "public,max-age=31536000,immutable" in invoker.put_arguments[shell_puts[0]]
    assert "public,max-age=300,must-revalidate" in invoker.put_arguments["index.html"]
    assert "legacy/keep.txt" in invoker.objects
    marker_metadata = invoker.objects[aws_module.SHELL_UPGRADE_MARKER_KEY]["Metadata"]
    assert isinstance(marker_metadata, dict)
    assert len(marker_metadata["invalidation-batch-sha256"]) == 64
    assert len(marker_metadata["shell-artifact-sha256"]) == 64
    for _service, operation, arguments in invoker.operator_calls:
        assert operation not in {"delete-object", "delete-objects"}
        assert "--delete" not in arguments
        if "--key" in arguments:
            assert not arguments[arguments.index("--key") + 1].startswith("data/")
    puts_before_complete_replay = len(invoker.put_order)
    with pytest.raises(AwsSafetyError, match="ALREADY_COMPLETE"):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    assert invoker.invalidation_count == 1
    assert len(invoker.put_order) == puts_before_complete_replay

    captured: dict[str, object] = {}

    class Cli:
        def __init__(self, executable: str = "aws") -> None:
            self.executable = executable

    monkeypatch.setattr(aws_module, "AwsCli", Cli)
    monkeypatch.setattr(aws_module, "locate_aws_cli", lambda: "aws")
    monkeypatch.setattr(
        aws_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="aws-cli/2.31.0 Python/3.12",
        ),
    )

    def command(invoker: object, **kwargs: object) -> dict[str, str]:
        assert isinstance(invoker, Cli)
        captured.update(kwargs)
        return {"status": "called"}

    monkeypatch.setattr(aws_module, "upgrade_existing_shell", command)
    monkeypatch.setattr(
        aws_module.sys,
        "argv",
        [
            "public_demo_aws.py",
            "--expected-account-id",
            ACCOUNT,
            "upgrade-existing-shell",
            "--old-product-sha",
            "f" * 40,
            "--new-product-sha",
            "a" * 40,
            "--repository-root",
            str(tmp_path.resolve()),
            "--snapshot-dir",
            str(_live_snapshot(tmp_path)),
            "--confirm",
            confirmation,
        ],
    )
    assert aws_module.main() == 0
    assert captured["old_product_sha"] == "f" * 40
    assert captured["new_product_sha"] == "a" * 40
    assert captured["repository_root"] == tmp_path.resolve()
    assert captured["snapshot_dir"] == _live_snapshot(tmp_path)


def test_existing_shell_upgrade_resume_reuses_the_recorded_invalidation(tmp_path: Path) -> None:
    artifact = _live_artifact(tmp_path)
    invoker = _UpgradeInvoker()
    invoker.fail_wait_once = True
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    with pytest.raises(subprocess.CalledProcessError):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    result = _run_frozen_shell_upgrade(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation=confirmation,
    )
    assert result["InvalidationId"] == "INV-ONCE"
    assert invoker.invalidation_count == 1


def test_existing_shell_upgrade_deduplicates_crash_between_invalidation_and_marker(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)
    invoker = _UpgradeInvoker()
    invoker.fail_marker_after_invalidation_once = True
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    with pytest.raises(subprocess.CalledProcessError):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    result = _run_frozen_shell_upgrade(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation=confirmation,
    )
    assert result["InvalidationId"] == "INV-ONCE"
    assert invoker.invalidation_count == 1


def test_existing_shell_upgrade_rejects_changed_artifact_before_resume_writes(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)
    invoker = _UpgradeInvoker()
    invoker.fail_marker_after_invalidation_once = True
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    with pytest.raises(subprocess.CalledProcessError):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    puts_before_retry = len(invoker.put_order)
    (artifact / "index.html").write_text("different live shell", encoding="utf-8")
    with pytest.raises(AwsSafetyError, match="SHELL_UPGRADE_TARGET_MISMATCH"):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    assert len(invoker.put_order) == puts_before_retry
    assert invoker.invalidation_count == 1


def test_existing_shell_upgrade_uploads_captured_bytes_after_first_aws_read_mutation(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)
    original = (artifact / "index.html").read_bytes()
    captured_artifact_paths: list[Path] = []

    class MutatingInvoker(_UpgradeInvoker):
        mutated = False

        def json(self, service: str, operation: str, *arguments: str) -> object:
            response = super().json(service, operation, *arguments)
            if (service, operation) == ("sts", "get-caller-identity") and not self.mutated:
                self.mutated = True
                captured_index = captured_artifact_paths[0] / "index.html"
                captured_index.chmod(0o600)
                captured_index.write_text(
                    "mutation after frozen material and before first AWS read",
                    encoding="utf-8",
                )
            return response

    invoker = MutatingInvoker()
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    _run_frozen_shell_upgrade(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation=confirmation,
        captured_artifact_paths=captured_artifact_paths,
    )
    metadata = _UpgradeInvoker._metadata(invoker.put_arguments["index.html"])
    assert metadata == {"sha256": hashlib.sha256(original).hexdigest()}
    current = invoker.objects["index.html"]
    version_id = current["VersionId"]
    assert invoker.version_objects[("index.html", version_id)][1] == original
    assert invoker.invalidation_count == 1


def test_existing_shell_upgrade_reads_back_each_exact_remote_version_before_invalidation(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)
    invoker = _UpgradeInvoker()
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    _run_frozen_shell_upgrade(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation=confirmation,
    )
    readbacks = [
        arguments
        for service, operation, arguments in invoker.operator_calls
        if (service, operation) == ("s3api", "get-object")
    ]
    readback_keys = {arguments[arguments.index("--key") + 1] for arguments in readbacks}
    assert {
        "_next/static/app.js",
        "index.html",
        "build-metadata.json",
    }.issubset(readback_keys)
    assert (
        len([key for key in readback_keys if key.startswith("ops/live-shell-upgrade/receipts/")])
        == 1
    )
    assert all("--version-id" in arguments for arguments in readbacks)


@pytest.mark.parametrize(
    "field",
    ["ContentType", "CacheControl", "ServerSideEncryption"],
)
def test_existing_immutable_shell_object_with_wrong_remote_policy_fails_without_overwrite(
    tmp_path: Path,
    field: str,
) -> None:
    artifact = _live_artifact(tmp_path)
    body = (artifact / "_next/static/app.js").read_bytes()
    invoker = _UpgradeInvoker()
    policy = {
        "ContentType": "text/javascript; charset=utf-8",
        "CacheControl": "public,max-age=31536000,immutable",
        "ServerSideEncryption": "AES256",
    }
    policy[field] = "wrong-remote-property"
    _install_existing_shell_object(
        invoker,
        key="_next/static/app.js",
        body=body,
        content_type=policy["ContentType"],
        cache_control=policy["CacheControl"],
        server_side_encryption=policy["ServerSideEncryption"],
    )

    with pytest.raises(AwsSafetyError, match="IMMUTABLE_SHELL_COLLISION"):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}",
        )

    assert "_next/static/app.js" not in invoker.put_order
    assert invoker.invalidation_count == 0


def test_existing_byte_identical_legacy_immutable_shell_object_is_reused(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)
    key = "_next/static/app.js"
    body = (artifact / key).read_bytes()
    invoker = _UpgradeInvoker()
    version_id = "legacy-immutable-version"
    value: dict[str, object] = {
        "ETag": '"legacy-immutable-etag"',
        "VersionId": version_id,
        "ContentLength": len(body),
        "Metadata": {},
        "ContentType": "text/javascript",
        "ServerSideEncryption": "AES256",
    }
    invoker.objects[key] = dict(value)
    invoker.version_objects[(key, version_id)] = (dict(value), body)

    receipt = aws_module._ensure_versioned_file(
        invoker,
        bucket="project-bucket",
        relative=key,
        body=body,
        immutable=True,
    )

    assert receipt == value
    assert key not in invoker.put_order


def test_existing_different_legacy_immutable_shell_object_still_fails_closed(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)
    key = "_next/static/app.js"
    body = (artifact / key).read_bytes()
    remote_body = bytes([body[0] ^ 1]) + body[1:]
    invoker = _UpgradeInvoker()
    version_id = "legacy-immutable-version"
    value: dict[str, object] = {
        "ETag": '"legacy-immutable-etag"',
        "VersionId": version_id,
        "ContentLength": len(remote_body),
        "Metadata": {},
        "ContentType": "text/javascript",
        "ServerSideEncryption": "AES256",
    }
    invoker.objects[key] = dict(value)
    invoker.version_objects[(key, version_id)] = (dict(value), remote_body)

    with pytest.raises(AwsSafetyError, match="IMMUTABLE_SHELL_COLLISION"):
        aws_module._ensure_versioned_file(
            invoker,
            bucket="project-bucket",
            relative=key,
            body=body,
            immutable=True,
        )

    assert key not in invoker.put_order


@pytest.mark.parametrize(
    "field",
    ["ContentType", "CacheControl", "ServerSideEncryption"],
)
def test_existing_mutable_shell_object_with_wrong_remote_policy_is_overwritten_and_proven(
    tmp_path: Path,
    field: str,
) -> None:
    artifact = _live_artifact(tmp_path)
    body = (artifact / "index.html").read_bytes()
    invoker = _UpgradeInvoker()
    policy = {
        "ContentType": "text/html; charset=utf-8",
        "CacheControl": "public,max-age=300,must-revalidate",
        "ServerSideEncryption": "AES256",
    }
    policy[field] = "wrong-remote-property"
    _install_existing_shell_object(
        invoker,
        key="index.html",
        body=body,
        content_type=policy["ContentType"],
        cache_control=policy["CacheControl"],
        server_side_encryption=policy["ServerSideEncryption"],
    )

    _run_frozen_shell_upgrade(
        invoker,
        expected_account_id=ACCOUNT,
        old_product_sha="f" * 40,
        new_product_sha="a" * 40,
        artifact_dir=artifact,
        confirmation=f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}",
    )

    assert "index.html" in invoker.put_order
    assert {
        key: invoker.objects["index.html"].get(key)
        for key in ("ContentType", "CacheControl", "ServerSideEncryption")
    } == {
        "ContentType": "text/html; charset=utf-8",
        "CacheControl": "public,max-age=300,must-revalidate",
        "ServerSideEncryption": "AES256",
    }


@pytest.mark.parametrize(
    ("versioned", "message"),
    [(True, "VERSION_READBACK_MISMATCH"), (False, "CURRENT_VERSION_MISMATCH")],
)
@pytest.mark.parametrize(
    "field",
    ["ContentType", "CacheControl", "ServerSideEncryption"],
)
def test_shell_object_wrong_exact_or_current_policy_blocks_invalidation(
    tmp_path: Path,
    versioned: bool,
    message: str,
    field: str,
) -> None:
    invoker = _UpgradeInvoker()
    invoker.corrupt_head_prefix = "index.html"
    invoker.corrupt_head_versioned = versioned
    invoker.corrupt_head_field = field

    with pytest.raises(AwsSafetyError, match=message):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=_live_artifact(tmp_path),
            confirmation=f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}",
        )

    assert invoker.invalidation_count == 0


@pytest.mark.parametrize(
    ("versioned", "message"),
    [(True, "VERSION_READBACK_MISMATCH"), (False, "CURRENT_VERSION_MISMATCH")],
)
@pytest.mark.parametrize(
    "field",
    ["ContentType", "CacheControl", "ServerSideEncryption"],
)
def test_release_receipt_wrong_exact_or_current_policy_blocks_marker_and_invalidation(
    tmp_path: Path,
    versioned: bool,
    message: str,
    field: str,
) -> None:
    invoker = _UpgradeInvoker()
    invoker.corrupt_head_prefix = "ops/live-shell-upgrade/receipts/"
    invoker.corrupt_head_versioned = versioned
    invoker.corrupt_head_field = field

    with pytest.raises(AwsSafetyError, match=message):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=_live_artifact(tmp_path),
            confirmation=f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}",
        )

    assert aws_module.SHELL_UPGRADE_MARKER_KEY not in invoker.put_order
    assert invoker.invalidation_count == 0


def test_existing_shell_upgrade_rejects_untrusted_version_bytes_before_invalidation(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)

    class CorruptingInvoker(_UpgradeInvoker):
        def json(self, service: str, operation: str, *arguments: str) -> object:
            response = super().json(service, operation, *arguments)
            if (service, operation) == ("s3api", "get-object") and arguments[
                arguments.index("--key") + 1
            ] == "index.html":
                Path(arguments[-1]).write_bytes(b"remote corruption")
            return response

    invoker = CorruptingInvoker()
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    with pytest.raises(AwsSafetyError, match="SHELL_OBJECT_VERSION_READBACK_MISMATCH"):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    assert invoker.invalidation_count == 0


def test_existing_shell_upgrade_rejects_current_head_supersession_before_invalidation(
    tmp_path: Path,
) -> None:
    artifact = _live_artifact(tmp_path)

    class SupersedingInvoker(_UpgradeInvoker):
        superseded = False

        def json(self, service: str, operation: str, *arguments: str) -> object:
            response = super().json(service, operation, *arguments)
            if (
                (service, operation) == ("s3api", "get-object")
                and arguments[arguments.index("--key") + 1] == "index.html"
                and not self.superseded
            ):
                self.superseded = True
                self.objects["index.html"] = {
                    **self.objects["index.html"],
                    "ETag": '"concurrent"',
                    "VersionId": "concurrent-version",
                }
            return response

    invoker = SupersedingInvoker()
    confirmation = f"UPGRADE_EXISTING_SHELL:{'f' * 40}:{'a' * 40}"
    with pytest.raises(AwsSafetyError, match="SHELL_OBJECT_CURRENT_VERSION_MISMATCH"):
        _run_frozen_shell_upgrade(
            invoker,
            expected_account_id=ACCOUNT,
            old_product_sha="f" * 40,
            new_product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation=confirmation,
        )
    assert invoker.invalidation_count == 0


@pytest.mark.parametrize(
    "header_value, expected",
    [("synthetic-canary-value", True), ("", False), (None, False), (42, False)],
)
def test_distribution_projection_keeps_header_check_without_returning_value(
    header_value: object, expected: bool
) -> None:
    # Evaluate the CLI's existing JMESPath engine with original synthetic input.
    document = {
        "Distribution": {
            "Id": "EDIST",
            "DistributionConfig": {
                "Origins": {
                    "Quantity": 1,
                    "Items": [
                        {
                            "Id": "runtime-api-origin",
                            "CustomHeaders": {
                                "Quantity": 1,
                                "Items": [
                                    {
                                        "HeaderName": "X-StockMarketAgent-Origin-Verify",
                                        "HeaderValue": header_value,
                                    }
                                ],
                            },
                        }
                    ],
                }
            },
        }
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys,jmespath; data=json.load(sys.stdin); "
            "print(json.dumps(jmespath.search(data['query'], data['document'])))",
        ],
        input=json.dumps(
            {"query": aws_module._DISTRIBUTION_VERIFICATION_QUERY, "document": document}
        ),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    projected = json.loads(result.stdout)
    header = projected["Distribution"]["DistributionConfig"]["Origins"]["Items"][0][
        "CustomHeaders"
    ]["Items"][0]
    assert header == {
        "HeaderName": "X-StockMarketAgent-Origin-Verify",
        "HeaderValuePresent": expected,
    }
    assert "synthetic-canary-value" not in result.stdout

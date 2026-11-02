"""Fail-closed AWS CLI v2 operator for the two-stage R1 preview runtime."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from scripts.public_demo_aws import assert_cloudformation_template_alias_free
from stock_assistant.platform.publication.s3_snapshots import (
    S3SnapshotClient,
    S3SnapshotPublisher,
    SnapshotPublicationError,
)

REGION: Final = "ap-northeast-1"
STACK_NAME: Final = "stockmarketagent-refresh"
TEMPLATE: Final = Path("deploy/aws/continuous-preview.yaml")
SHA: Final = re.compile(r"^[0-9a-f]{40}$")
DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCOUNT: Final = re.compile(r"^[0-9]{12}$")
BUCKET: Final = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
DEEPSEEK_SECRET_ARN: Final = re.compile(
    r"^arn:aws:secretsmanager:ap-northeast-1:([0-9]{12}):"
    r"secret:stockmarketagent/deepseek-[A-Za-z0-9/_+=.@-]{6,}$"
)
RESEARCH_DATA_SECRET_ARN: Final = re.compile(
    r"^arn:aws:secretsmanager:ap-northeast-1:([0-9]{12}):"
    r"secret:stockmarketagent/research-data-(?!promax-)[A-Za-z0-9/_+=.@-]{6,}$"
)
PROMAX_DATA_SECRET_ARN: Final = re.compile(
    r"^arn:aws:secretsmanager:ap-northeast-1:([0-9]{12}):"
    r"secret:stockmarketagent/research-data-promax-[A-Za-z0-9/_+=.@-]{6,}$"
)
CLOUDFRONT_PREFIX_LIST_ID: Final = re.compile(r"^pl-[0-9a-f]{8,17}$")
CLOUDFRONT_ORIGIN_PREFIX_LIST_NAME: Final = "com.amazonaws.global.cloudfront.origin-facing"
SCHEDULE_SUFFIXES: Final = (
    "ingest",
    "publish",
    "catch-up",
    "behavior-intraday",
    "behavior-post-close",
)
SCHEDULE_LOGICAL_IDS: Final = {
    "ingest": "IngestSchedule",
    "publish": "PublishSchedule",
    "catch-up": "CatchUpSchedule",
    "behavior-intraday": "BehaviorIntradaySchedule",
    "behavior-post-close": "BehaviorPostCloseSchedule",
}
SCHEDULE_EXPRESSIONS: Final = {
    "ingest": "cron(55 8 ? * MON-FRI *)",
    "publish": "cron(5 9 ? * MON-FRI *)",
    "catch-up": "cron(20 9 ? * MON-FRI *)",
    "behavior-intraday": "cron(0/10 9-15 ? * MON-FRI *)",
    "behavior-post-close": "cron(10 15 ? * MON-FRI *)",
}
SCHEDULE_COMMANDS: Final = {
    "ingest": "ingest",
    "publish": "publish",
    "catch-up": "catch-up",
    "behavior-intraday": "behavior-intraday",
    "behavior-post-close": "behavior-post-close",
}
SCHEDULE_GROUP: Final = "default"
SCHEDULER_RETRY_FENCE_SECONDS: Final = 900.0
ROLLBACK_DRAIN_POLL_SECONDS: Final = 5.0
ROLLBACK_DRAIN_MAX_POLLS: Final = 12
DATABASE_NAME: Final = "stock_assistant"
MASTER_USERNAME: Final = "stock_admin"
TASK_DEFINITIONS: Final = {
    "ApiTaskDefinition",
    "BootstrapTaskDefinition",
    "RefreshTaskDefinition",
}
ALLOWED_LOGICAL_IDS: Final = {
    "BootstrapTaskDefinition",
    "ApiDatabaseEgress",
    "ApiExecutionRole",
    "ApiHttpsEgress",
    "ApiListener",
    "ApiLoadBalancer",
    "ApiLoadBalancerEgress",
    "ApiLoadBalancerIngress",
    "ApiLoadBalancerSecurityGroup",
    "ApiLogGroup",
    "ApiSecurityGroup",
    "ApiService",
    "ApiTargetGroup",
    "ApiTaskDefinition",
    "ApiTaskRole",
    "BehaviorIntradaySchedule",
    "BehaviorPostCloseSchedule",
    "CatchUpSchedule",
    "DatabaseParameterGroup",
    "DatabaseSecurityGroup",
    "DatabaseSubnetGroup",
    "DatabaseTaskIngress",
    "DatabaseApiIngress",
    "ExecutionRole",
    "IngestSchedule",
    "InternetGateway",
    "InternetGatewayAttachment",
    "PrivateDbSubnetA",
    "PrivateDbSubnetB",
    "PublicDefaultRoute",
    "PublicRouteTable",
    "PublicSubnetA",
    "PublicSubnetARouteAssociation",
    "PublicSubnetB",
    "PublicSubnetBRouteAssociation",
    "PublishSchedule",
    "RefreshCluster",
    "RefreshDatabase",
    "RefreshLogGroup",
    "RefreshRepository",
    "RefreshTaskDefinition",
    "RefreshVpc",
    "RuntimeDatabaseSecret",
    "SchedulerDeliveryQueue",
    "SchedulerDeliveryAlarm",
    "SchedulerRole",
    "TaskDatabaseEgress",
    "TaskFailureAlarm",
    "TaskFailureQueue",
    "TaskFailureQueuePolicy",
    "TaskFailureRule",
    "TaskHttpsEgress",
    "TaskRole",
    "TaskSecurityGroup",
}
STACK_NOT_FOUND_ERROR: Final = re.compile(
    r"^(?:aws: \[ERROR\]: )?An error occurred \(ValidationError\) when calling the "
    r"DescribeStacks operation"
    r"(?: \(reached max retries: [1-9][0-9]*\))?: "
    rf"Stack with id {re.escape(STACK_NAME)} does not exist$"
)
PROTECTED_REPLACEMENT: Final = {
    "RefreshRepository",
    "RefreshVpc",
    "PublicSubnetA",
    "PublicSubnetB",
    "PrivateDbSubnetA",
    "PrivateDbSubnetB",
    "TaskSecurityGroup",
    "DatabaseSecurityGroup",
    "DatabaseSubnetGroup",
    "DatabaseParameterGroup",
    "RefreshDatabase",
    "RuntimeDatabaseSecret",
}


class ContinuousPreviewAwsError(RuntimeError):
    """An AWS response crossed the reviewed deployment boundary."""


class AwsInvoker(Protocol):
    def json(self, service: str, operation: str, *arguments: str) -> object: ...

    def text(self, service: str, operation: str, *arguments: str) -> str: ...


@dataclass(frozen=True, slots=True)
class AwsCli:
    executable: str = "aws"

    def _command(self, service: str, operation: str, *arguments: str) -> list[str]:
        return [
            self.executable,
            service,
            operation,
            *arguments,
            "--region",
            REGION,
            "--no-cli-pager",
        ]

    def text(self, service: str, operation: str, *arguments: str) -> str:
        timeout = 3600 if operation == "wait" else 180
        result = subprocess.run(  # noqa: S603 - argv only; no shell.
            self._command(service, operation, *arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout

    def json(self, service: str, operation: str, *arguments: str) -> object:
        try:
            return json.loads(self.text(service, operation, *arguments, "--output", "json"))
        except json.JSONDecodeError as error:
            raise ContinuousPreviewAwsError("AWS_CLI_INVALID_JSON") from error


def verify_cli_v2(output: str) -> None:
    if not output.startswith("aws-cli/2."):
        raise ContinuousPreviewAwsError("AWS_CLI_V2_REQUIRED")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContinuousPreviewAwsError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ContinuousPreviewAwsError(f"{label} must be an array")
    return value


def verify_identity(payload: object, *, expected_account: str) -> None:
    if ACCOUNT.fullmatch(expected_account) is None:
        raise ContinuousPreviewAwsError("EXPECTED_ACCOUNT_INVALID")
    identity = _object(payload, "identity")
    if identity.get("Account") != expected_account:
        raise ContinuousPreviewAwsError("AWS_ACCOUNT_MISMATCH")
    arn = identity.get("Arn")
    if not isinstance(arn, str) or f"::{expected_account}:" not in arn:
        raise ContinuousPreviewAwsError("AWS_PRINCIPAL_INVALID")


def validate_deepseek_secret(payload: object, *, expected_account: str, expected_arn: str) -> None:
    """Verify metadata for the exact secret ARN without ever retrieving its value."""

    match = DEEPSEEK_SECRET_ARN.fullmatch(expected_arn)
    if match is None or match.group(1) != expected_account:
        raise ContinuousPreviewAwsError("DEEPSEEK_SECRET_ARN_INVALID")
    secret = _object(payload, "DeepSeek secret")
    if secret.get("ARN") != expected_arn or secret.get("DeletedDate") is not None:
        raise ContinuousPreviewAwsError("DEEPSEEK_SECRET_IDENTITY_MISMATCH")
    name = secret.get("Name")
    if not isinstance(name, str) or not name.startswith("stockmarketagent/deepseek-"):
        raise ContinuousPreviewAwsError("DEEPSEEK_SECRET_IDENTITY_MISMATCH")


def validate_research_data_secret(
    payload: object,
    *,
    expected_account: str,
    expected_arn: str,
) -> None:
    """Verify the exact research-data secret metadata without reading its value."""

    match = RESEARCH_DATA_SECRET_ARN.fullmatch(expected_arn)
    if match is None or match.group(1) != expected_account:
        raise ContinuousPreviewAwsError("RESEARCH_DATA_SECRET_ARN_INVALID")
    secret = _object(payload, "research data secret")
    if secret.get("ARN") != expected_arn or secret.get("DeletedDate") is not None:
        raise ContinuousPreviewAwsError("RESEARCH_DATA_SECRET_IDENTITY_MISMATCH")
    name = secret.get("Name")
    if not isinstance(name, str) or not name.startswith("stockmarketagent/research-data-"):
        raise ContinuousPreviewAwsError("RESEARCH_DATA_SECRET_IDENTITY_MISMATCH")


def validate_promax_data_secret(
    payload: object,
    *,
    expected_account: str,
    expected_arn: str,
) -> None:
    """Verify the exact ProMax secret metadata without reading its value."""

    match = PROMAX_DATA_SECRET_ARN.fullmatch(expected_arn)
    if match is None or match.group(1) != expected_account:
        raise ContinuousPreviewAwsError("PROMAX_SECRET_ARN_INVALID")
    secret = _object(payload, "ProMax data secret")
    if secret.get("ARN") != expected_arn or secret.get("DeletedDate") is not None:
        raise ContinuousPreviewAwsError("PROMAX_SECRET_IDENTITY_MISMATCH")
    name = secret.get("Name")
    if not isinstance(name, str) or not name.startswith("stockmarketagent/research-data-promax-"):
        raise ContinuousPreviewAwsError("PROMAX_SECRET_IDENTITY_MISMATCH")


def validate_cloudfront_origin_prefix_list(
    payload: object, *, expected_prefix_list_id: str
) -> None:
    """Bind ALB ingress to the exact regional AWS-managed CloudFront origin list."""

    if CLOUDFRONT_PREFIX_LIST_ID.fullmatch(expected_prefix_list_id) is None:
        raise ContinuousPreviewAwsError("CLOUDFRONT_ORIGIN_PREFIX_LIST_ID_INVALID")
    response = _object(payload, "CloudFront origin prefix list")
    prefix_lists = _array(response.get("PrefixLists"), "CloudFront origin prefix lists")
    if len(prefix_lists) != 1:
        raise ContinuousPreviewAwsError("CLOUDFRONT_ORIGIN_PREFIX_LIST_IDENTITY_MISMATCH")
    prefix_list = _object(prefix_lists[0], "CloudFront origin prefix list")
    if (
        prefix_list.get("PrefixListId") != expected_prefix_list_id
        or prefix_list.get("PrefixListName") != CLOUDFRONT_ORIGIN_PREFIX_LIST_NAME
        or prefix_list.get("OwnerId") != "AWS"
        or prefix_list.get("AddressFamily") != "IPv4"
        or prefix_list.get("State") != "create-complete"
    ):
        raise ContinuousPreviewAwsError("CLOUDFRONT_ORIGIN_PREFIX_LIST_IDENTITY_MISMATCH")


def validate_change_set(payload: object) -> tuple[dict[str, str], ...]:
    """Reject removal, unknown replacement and every retained-resource replacement."""

    change_set = _object(payload, "change set")
    if change_set.get("Status") != "CREATE_COMPLETE":
        raise ContinuousPreviewAwsError("CHANGE_SET_NOT_READY")
    normalized: list[dict[str, str]] = []
    for raw in _array(change_set.get("Changes"), "changes"):
        resource = _object(_object(raw, "change").get("ResourceChange"), "resource change")
        action = resource.get("Action")
        logical_id = resource.get("LogicalResourceId")
        replacement = resource.get("Replacement", "False")
        if action not in {"Add", "Modify"} or not isinstance(logical_id, str):
            raise ContinuousPreviewAwsError("DESTRUCTIVE_CHANGE_FORBIDDEN")
        if logical_id not in ALLOWED_LOGICAL_IDS:
            raise ContinuousPreviewAwsError(f"UNKNOWN_LOGICAL_ID:{logical_id}")
        replacement_requested = replacement in {True, "True", "Conditional"}
        if logical_id in PROTECTED_REPLACEMENT and replacement_requested:
            raise ContinuousPreviewAwsError(f"PROTECTED_REPLACEMENT:{logical_id}")
        if replacement_requested and logical_id not in TASK_DEFINITIONS:
            raise ContinuousPreviewAwsError(f"REPLACEMENT_FORBIDDEN:{logical_id}")
        normalized.append(
            {
                "action": str(action),
                "logical_id": logical_id,
                "replacement": "True" if replacement_requested else "False",
            }
        )
    return tuple(sorted(normalized, key=lambda item: (item["logical_id"], item["action"])))


def verify_local_release_source(*, product_sha: str, template: Path = TEMPLATE) -> bytes:
    """Bind deployment bytes to a clean worktree at the exact ProductSha."""

    resolved = template.resolve()
    if not resolved.is_file() or resolved.name != "continuous-preview.yaml":
        raise ContinuousPreviewAwsError("CONTINUOUS_PREVIEW_TEMPLATE_MISSING")
    root = resolved.parents[2]
    try:
        status = subprocess.run(  # noqa: S603 - fixed git argv only.
            [  # noqa: S607 - resolved by the operator environment.
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        head = subprocess.run(  # noqa: S603 - fixed git argv only.
            [  # noqa: S607 - resolved by the operator environment.
                "git",
                "-C",
                str(root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        committed = subprocess.run(  # noqa: S603 - fixed git argv only.
            [  # noqa: S607 - resolved by the operator environment.
                "git",
                "-C",
                str(root),
                "show",
                f"{product_sha}:deploy/aws/continuous-preview.yaml",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ContinuousPreviewAwsError("LOCAL_RELEASE_SOURCE_UNVERIFIED") from error
    if status:
        raise ContinuousPreviewAwsError("DEPLOY_WORKTREE_NOT_CLEAN")
    if head != product_sha:
        raise ContinuousPreviewAwsError("DEPLOY_HEAD_PRODUCT_SHA_MISMATCH")
    template_bytes = resolved.read_bytes()
    if not isinstance(committed, bytes) or committed != template_bytes:
        raise ContinuousPreviewAwsError("DEPLOY_TEMPLATE_GIT_BLOB_MISMATCH")
    try:
        assert_cloudformation_template_alias_free(template_bytes)
    except ValueError as error:
        raise ContinuousPreviewAwsError(str(error)) from error
    return template_bytes


def validate_ecr_product_image(
    payload: object,
    *,
    expected_account: str,
    repository_uri: str,
    product_sha: str,
    image_digest: str,
) -> None:
    prefix = f"{expected_account}.dkr.ecr.{REGION}.amazonaws.com/"
    if not repository_uri.startswith(prefix):
        raise ContinuousPreviewAwsError("ECR_REPOSITORY_IDENTITY_MISMATCH")
    repository_name = repository_uri.removeprefix(prefix)
    if repository_name != "stockmarketagent-refresh":
        raise ContinuousPreviewAwsError("ECR_REPOSITORY_IDENTITY_MISMATCH")
    response = _object(payload, "describe images")
    details = _array(response.get("imageDetails"), "image details")
    if len(details) != 1:
        raise ContinuousPreviewAwsError("ECR_PRODUCT_TAG_NOT_UNIQUE")
    detail = _object(details[0], "image detail")
    tags = _array(detail.get("imageTags"), "image tags")
    if (
        detail.get("registryId") != expected_account
        or detail.get("repositoryName") != repository_name
        or detail.get("imageDigest") != image_digest
        or product_sha not in tags
    ):
        raise ContinuousPreviewAwsError("ECR_PRODUCT_DIGEST_MISMATCH")


def validate_change_set_binding(
    payload: object,
    *,
    expected_account: str,
    expected_change_set_id: str,
    expected_stack_id: str,
    expected_change_set_type: str,
    expected_parameters: dict[str, str],
) -> None:
    value = _object(payload, "change set")
    stack_id = value.get("StackId")
    change_set_id = value.get("ChangeSetId")
    if (
        value.get("StackName") != STACK_NAME
        or stack_id != expected_stack_id
        or not expected_stack_id.startswith(
            f"arn:aws:cloudformation:{REGION}:{expected_account}:stack/{STACK_NAME}/"
        )
        or change_set_id != expected_change_set_id
        or not expected_change_set_id.startswith(
            f"arn:aws:cloudformation:{REGION}:{expected_account}:changeSet/"
        )
    ):
        raise ContinuousPreviewAwsError("CHANGE_SET_IDENTITY_MISMATCH")
    if expected_change_set_type not in {"CREATE", "UPDATE"}:
        raise ContinuousPreviewAwsError("CHANGE_SET_TYPE_INVALID")
    if (
        value.get("Status") != "CREATE_COMPLETE"
        or value.get("ExecutionStatus") != "AVAILABLE"
        or ("ChangeSetType" in value and value.get("ChangeSetType") != expected_change_set_type)
        or value.get("Capabilities") != ["CAPABILITY_NAMED_IAM"]
    ):
        raise ContinuousPreviewAwsError("CHANGE_SET_NOT_EXECUTABLE")
    tags: dict[str, str] = {}
    for raw in _array(value.get("Tags"), "change set tags"):
        tag = _object(raw, "change set tag")
        key, tag_value = tag.get("Key"), tag.get("Value")
        if not isinstance(key, str) or not isinstance(tag_value, str) or key in tags:
            raise ContinuousPreviewAwsError("CHANGE_SET_TAG_INVALID")
        tags[key] = tag_value
    if tags != {
        "Project": "stockmarketagent-refresh",
        "ManagedBy": "StockMarketAgent",
    }:
        raise ContinuousPreviewAwsError("CHANGE_SET_TAG_MISMATCH")
    actual: dict[str, str] = {}
    for raw in _array(value.get("Parameters"), "change set parameters"):
        item = _object(raw, "change set parameter")
        key, parameter_value = item.get("ParameterKey"), item.get("ParameterValue")
        if not isinstance(key, str) or not isinstance(parameter_value, str) or key in actual:
            raise ContinuousPreviewAwsError("CHANGE_SET_PARAMETER_INVALID")
        actual[key] = parameter_value
    if actual != expected_parameters:
        raise ContinuousPreviewAwsError("CHANGE_SET_PARAMETER_MISMATCH")


def deploy(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    product_sha: str,
    bucket: str,
    public_base_url: str,
    image_digest: str,
    confirmation: str,
    deepseek_secret_arn: str = "",
    research_data_secret_arn: str = "",
    promax_secret_arn: str = "",
    cloudfront_origin_prefix_list_id: str = "",
) -> tuple[dict[str, str], ...]:
    """Create and execute one reviewed base or digest-bound change set."""

    if confirmation != "DEPLOY_CONTINUOUS_PREVIEW":
        raise ContinuousPreviewAwsError("DEPLOY_CONFIRMATION_REQUIRED")
    if SHA.fullmatch(product_sha) is None:
        raise ContinuousPreviewAwsError("PRODUCT_SHA_INVALID")
    if image_digest and DIGEST.fullmatch(image_digest) is None:
        raise ContinuousPreviewAwsError("IMAGE_DIGEST_INVALID")
    if image_digest and not cloudfront_origin_prefix_list_id:
        raise ContinuousPreviewAwsError("CLOUDFRONT_ORIGIN_PREFIX_LIST_REQUIRED")
    if image_digest and not research_data_secret_arn:
        raise ContinuousPreviewAwsError("RESEARCH_DATA_SECRET_REQUIRED")
    if image_digest and not promax_secret_arn:
        raise ContinuousPreviewAwsError("PROMAX_SECRET_REQUIRED")
    if research_data_secret_arn and research_data_secret_arn == promax_secret_arn:
        raise ContinuousPreviewAwsError("RESEARCH_DATA_SECRET_ARNS_MUST_BE_DISTINCT")
    if (
        cloudfront_origin_prefix_list_id
        and CLOUDFRONT_PREFIX_LIST_ID.fullmatch(cloudfront_origin_prefix_list_id) is None
    ):
        raise ContinuousPreviewAwsError("CLOUDFRONT_ORIGIN_PREFIX_LIST_ID_INVALID")
    if ACCOUNT.fullmatch(expected_account) is None:
        raise ContinuousPreviewAwsError("EXPECTED_ACCOUNT_INVALID")
    if BUCKET.fullmatch(bucket) is None or ".." in bucket:
        raise ContinuousPreviewAwsError("PUBLIC_SNAPSHOT_BUCKET_INVALID")
    try:
        public_target = urlsplit(public_base_url)
        public_port = public_target.port
    except ValueError as error:
        raise ContinuousPreviewAwsError("PUBLIC_BASE_URL_INVALID") from error
    if (
        public_target.scheme != "https"
        or public_target.hostname is None
        or public_target.username is not None
        or public_target.password is not None
        or public_port is not None
        or public_target.path != ""
        or public_target.query
        or public_target.fragment
    ):
        raise ContinuousPreviewAwsError("PUBLIC_BASE_URL_INVALID")
    template_bytes = verify_local_release_source(product_sha=product_sha)
    try:
        template_body = template_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContinuousPreviewAwsError("CONTINUOUS_PREVIEW_TEMPLATE_UTF8_REQUIRED") from error
    verify_identity(
        invoker.json("sts", "get-caller-identity"),
        expected_account=expected_account,
    )
    if deepseek_secret_arn:
        validate_deepseek_secret(
            invoker.json(
                "secretsmanager",
                "describe-secret",
                "--secret-id",
                deepseek_secret_arn,
            ),
            expected_account=expected_account,
            expected_arn=deepseek_secret_arn,
        )
    if research_data_secret_arn:
        validate_research_data_secret(
            invoker.json(
                "secretsmanager",
                "describe-secret",
                "--secret-id",
                research_data_secret_arn,
            ),
            expected_account=expected_account,
            expected_arn=research_data_secret_arn,
        )
    if promax_secret_arn:
        validate_promax_data_secret(
            invoker.json(
                "secretsmanager",
                "describe-secret",
                "--secret-id",
                promax_secret_arn,
            ),
            expected_account=expected_account,
            expected_arn=promax_secret_arn,
        )
    if cloudfront_origin_prefix_list_id:
        validate_cloudfront_origin_prefix_list(
            invoker.json(
                "ec2",
                "describe-managed-prefix-lists",
                "--prefix-list-ids",
                cloudfront_origin_prefix_list_id,
            ),
            expected_prefix_list_id=cloudfront_origin_prefix_list_id,
        )
    invoker.json(
        "cloudformation",
        "validate-template",
        "--template-body",
        template_body,
    )
    existing = False
    try:
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
    except subprocess.CalledProcessError as error:
        if STACK_NOT_FOUND_ERROR.fullmatch((error.stderr or "").strip()) is None:
            raise ContinuousPreviewAwsError("STACK_DISCOVERY_FAILED") from error
        change_set_type = "CREATE"
    else:
        existing = True
        change_set_type = "UPDATE"
    if image_digest:
        if not existing:
            raise ContinuousPreviewAwsError("DIGEST_REQUIRES_BASE_STACK")
        outputs = stack_outputs(invoker, expected_account=expected_account)
        repository_uri = outputs.get("RepositoryUri")
        if repository_uri is None:
            raise ContinuousPreviewAwsError("ECR_REPOSITORY_OUTPUT_MISSING")
        image = invoker.json(
            "ecr",
            "describe-images",
            "--repository-name",
            "stockmarketagent-refresh",
            "--image-ids",
            f"imageTag={product_sha}",
        )
        validate_ecr_product_image(
            image,
            expected_account=expected_account,
            repository_uri=repository_uri,
            product_sha=product_sha,
            image_digest=image_digest,
        )
    name = f"continuous-preview-{uuid4().hex[:12]}"
    created = _object(
        invoker.json(
            "cloudformation",
            "create-change-set",
            "--stack-name",
            STACK_NAME,
            "--change-set-name",
            name,
            "--change-set-type",
            change_set_type,
            "--template-body",
            template_body,
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--parameters",
            f"ParameterKey=ProductSha,ParameterValue={product_sha}",
            f"ParameterKey=PublicSnapshotBucketName,ParameterValue={bucket}",
            f"ParameterKey=PublicBaseUrl,ParameterValue={public_base_url}",
            f"ParameterKey=ImageDigest,ParameterValue={image_digest}",
            f"ParameterKey=DatabaseName,ParameterValue={DATABASE_NAME}",
            f"ParameterKey=MasterUsername,ParameterValue={MASTER_USERNAME}",
            f"ParameterKey=DeepSeekApiSecretArn,ParameterValue={deepseek_secret_arn}",
            (f"ParameterKey=ResearchDataApiSecretArn,ParameterValue={research_data_secret_arn}"),
            f"ParameterKey=PromaxApiSecretArn,ParameterValue={promax_secret_arn}",
            (
                "ParameterKey=CloudFrontOriginFacingPrefixListId,"
                f"ParameterValue={cloudfront_origin_prefix_list_id}"
            ),
            "--tags",
            "Key=Project,Value=stockmarketagent-refresh",
            "Key=ManagedBy,Value=StockMarketAgent",
        ),
        "create change set",
    )
    change_set_id = created.get("Id")
    created_stack_id = created.get("StackId")
    if (
        not isinstance(change_set_id, str)
        or not change_set_id.startswith(
            f"arn:aws:cloudformation:{REGION}:{expected_account}:changeSet/{name}/"
        )
        or not isinstance(created_stack_id, str)
        or not created_stack_id.startswith(
            f"arn:aws:cloudformation:{REGION}:{expected_account}:stack/{STACK_NAME}/"
        )
    ):
        raise ContinuousPreviewAwsError("CREATED_CHANGE_SET_IDENTITY_MISMATCH")
    invoker.text(
        "cloudformation",
        "wait",
        "change-set-create-complete",
        "--change-set-name",
        change_set_id,
    )
    described = invoker.json(
        "cloudformation",
        "describe-change-set",
        "--change-set-name",
        change_set_id,
    )
    expected_parameters = {
        "ProductSha": product_sha,
        "PublicSnapshotBucketName": bucket,
        "PublicBaseUrl": public_base_url,
        "ImageDigest": image_digest,
        "DatabaseName": DATABASE_NAME,
        "MasterUsername": MASTER_USERNAME,
        "DeepSeekApiSecretArn": deepseek_secret_arn,
        "ResearchDataApiSecretArn": research_data_secret_arn,
        "PromaxApiSecretArn": promax_secret_arn,
        "CloudFrontOriginFacingPrefixListId": cloudfront_origin_prefix_list_id,
    }
    validate_change_set_binding(
        described,
        expected_account=expected_account,
        expected_change_set_id=change_set_id,
        expected_stack_id=created_stack_id,
        expected_change_set_type=change_set_type,
        expected_parameters=expected_parameters,
    )
    changes = validate_change_set(described)
    invoker.text(
        "cloudformation",
        "execute-change-set",
        "--change-set-name",
        change_set_id,
    )
    waiter = "stack-create-complete" if change_set_type == "CREATE" else "stack-update-complete"
    invoker.text("cloudformation", "wait", waiter, "--stack-name", STACK_NAME)
    outputs = stack_outputs(invoker, expected_account=expected_account)
    if (
        outputs.get("ProductSha") != product_sha
        or outputs.get("PublicSnapshotBucketName") != bucket
        or outputs.get("PublicBaseUrl") != public_base_url
        or (image_digest and outputs.get("ImageDigest") != image_digest)
        or (not image_digest and "ImageDigest" in outputs)
    ):
        raise ContinuousPreviewAwsError("DEPLOYED_OUTPUTS_MISMATCH")
    return changes


def stack_outputs(
    invoker: AwsInvoker,
    *,
    expected_account: str | None = None,
) -> dict[str, str]:
    response = _object(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME),
        "describe stacks",
    )
    stacks = _array(response.get("Stacks"), "stacks")
    if len(stacks) != 1:
        raise ContinuousPreviewAwsError("STACK_IDENTITY_INVALID")
    stack = _object(stacks[0], "stack")
    if stack.get("StackName") != STACK_NAME or stack.get("StackStatus") not in {
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
    }:
        raise ContinuousPreviewAwsError("STACK_NOT_READY")
    if expected_account is not None:
        stack_id = stack.get("StackId")
        stack_id_pattern = re.compile(
            rf"^arn:aws:cloudformation:{re.escape(REGION)}:{expected_account}:"
            rf"stack/{re.escape(STACK_NAME)}/[A-Za-z0-9-]+$"
        )
        if not isinstance(stack_id, str) or stack_id_pattern.fullmatch(stack_id) is None:
            raise ContinuousPreviewAwsError("STACK_IDENTITY_INVALID")
    values: dict[str, str] = {}
    for raw in _array(stack.get("Outputs"), "outputs"):
        item = _object(raw, "output")
        key, value = item.get("OutputKey"), item.get("OutputValue")
        if not isinstance(key, str) or not isinstance(value, str) or key in values:
            raise ContinuousPreviewAwsError("STACK_OUTPUT_INVALID")
        values[key] = value
    return values


def _candidate_stack_outputs(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    product_sha: str,
    image_digest: str,
) -> dict[str, str]:
    """Bind a runtime write to one exact account, stack and immutable candidate."""

    if ACCOUNT.fullmatch(expected_account) is None:
        raise ContinuousPreviewAwsError("EXPECTED_ACCOUNT_INVALID")
    if SHA.fullmatch(product_sha) is None:
        raise ContinuousPreviewAwsError("PRODUCT_SHA_INVALID")
    if DIGEST.fullmatch(image_digest) is None:
        raise ContinuousPreviewAwsError("IMAGE_DIGEST_INVALID")
    verify_identity(
        invoker.json("sts", "get-caller-identity"),
        expected_account=expected_account,
    )
    outputs = stack_outputs(invoker, expected_account=expected_account)
    if outputs.get("ProductSha") != product_sha or outputs.get("ImageDigest") != image_digest:
        raise ContinuousPreviewAwsError("RUNTIME_CANDIDATE_MISMATCH")
    return outputs


def run_task(
    invoker: AwsInvoker,
    *,
    operation: str,
    expected_account: str,
    product_sha: str,
    image_digest: str,
) -> str:
    """Run one task and prove the essential container reached exit code zero."""

    if operation not in {
        "migrate",
        "seed",
        "seed-last-session",
        "behavior-last-session",
        "ingest",
        "publish",
        "catch-up",
        "llm-smoke",
        "source-smoke",
    }:
        raise ContinuousPreviewAwsError("TASK_OPERATION_INVALID")
    outputs = _candidate_stack_outputs(
        invoker,
        expected_account=expected_account,
        product_sha=product_sha,
        image_digest=image_digest,
    )
    required_outputs = {
        "RefreshClusterArn",
        "PublicSubnetIds",
        "TaskSecurityGroupId",
        "BootstrapTaskDefinitionArn" if operation == "migrate" else "RefreshTaskDefinitionArn",
    }
    if not required_outputs <= outputs.keys():
        raise ContinuousPreviewAwsError("TASK_STACK_OUTPUT_MISSING")
    task_definition = outputs[
        "BootstrapTaskDefinitionArn" if operation == "migrate" else "RefreshTaskDefinitionArn"
    ]
    response = _object(
        invoker.json(
            "ecs",
            "run-task",
            "--cluster",
            outputs["RefreshClusterArn"],
            "--task-definition",
            task_definition,
            "--launch-type",
            "FARGATE",
            "--platform-version",
            "1.4.0",
            "--network-configuration",
            json.dumps(
                {
                    "awsvpcConfiguration": {
                        "subnets": outputs["PublicSubnetIds"].split(","),
                        "securityGroups": [outputs["TaskSecurityGroupId"]],
                        "assignPublicIp": "ENABLED",
                    }
                },
                separators=(",", ":"),
            ),
            "--overrides",
            json.dumps(
                {"containerOverrides": [{"name": "refresh", "command": [operation]}]},
                separators=(",", ":"),
            ),
            "--count",
            "1",
        ),
        "run task",
    )
    failures = _array(response.get("failures"), "run task failures")
    tasks = _array(response.get("tasks"), "run tasks")
    if failures or len(tasks) != 1:
        raise ContinuousPreviewAwsError("ECS_TASK_START_FAILED")
    task_arn = _object(tasks[0], "task").get("taskArn")
    if not isinstance(task_arn, str) or not task_arn:
        raise ContinuousPreviewAwsError("ECS_TASK_ARN_MISSING")
    invoker.text(
        "ecs",
        "wait",
        "tasks-stopped",
        "--cluster",
        outputs["RefreshClusterArn"],
        "--tasks",
        task_arn,
    )
    stopped = _object(
        invoker.json(
            "ecs",
            "describe-tasks",
            "--cluster",
            outputs["RefreshClusterArn"],
            "--tasks",
            task_arn,
        ),
        "describe tasks",
    )
    stopped_tasks = _array(stopped.get("tasks"), "stopped tasks")
    if len(stopped_tasks) != 1:
        raise ContinuousPreviewAwsError("ECS_STOPPED_TASK_MISSING")
    task = _object(stopped_tasks[0], "stopped task")
    containers = _array(task.get("containers"), "containers")
    essential = [item for item in containers if _object(item, "container").get("name") == "refresh"]
    if (
        task.get("lastStatus") != "STOPPED"
        or len(essential) != 1
        or _object(essential[0], "refresh container").get("exitCode") != 0
    ):
        raise ContinuousPreviewAwsError("ECS_TASK_NONZERO_EXIT")
    return task_arn


def _schedule_arn(*, expected_account: str, name: str) -> str:
    return f"arn:aws:scheduler:{REGION}:{expected_account}:schedule/{SCHEDULE_GROUP}/{name}"


def _validate_schedule(
    value: object,
    *,
    expected_account: str,
    name: str,
    expression: str,
    expected_target: dict[str, object],
) -> dict[str, object]:
    schedule = _object(value, "schedule")
    if (
        schedule.get("Name") != name
        or schedule.get("GroupName") != SCHEDULE_GROUP
        or schedule.get("Arn") != _schedule_arn(expected_account=expected_account, name=name)
        or schedule.get("State") not in {"ENABLED", "DISABLED"}
        or schedule.get("FlexibleTimeWindow") != {"Mode": "OFF"}
        or schedule.get("ScheduleExpression") != expression
        or schedule.get("ScheduleExpressionTimezone") != "Asia/Shanghai"
        or schedule.get("Target") != expected_target
    ):
        raise ContinuousPreviewAwsError(f"SCHEDULE_IDENTITY_MISMATCH:{name}")
    return schedule


def _load_bound_schedules(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    outputs: dict[str, str],
) -> dict[str, dict[str, object]]:
    required_outputs = {
        "RefreshClusterArn",
        "RefreshTaskDefinitionArn",
        "PublicSubnetIds",
        "TaskSecurityGroupId",
        "SchedulerRoleArn",
        "SchedulerDeliveryQueueArn",
    }
    if not required_outputs <= outputs.keys():
        raise ContinuousPreviewAwsError("SCHEDULE_STACK_OUTPUT_MISSING")
    if not outputs["SchedulerRoleArn"].startswith(
        f"arn:aws:iam::{expected_account}:role/"
    ) or not outputs["SchedulerDeliveryQueueArn"].startswith(
        f"arn:aws:sqs:{REGION}:{expected_account}:"
    ):
        raise ContinuousPreviewAwsError("SCHEDULE_STACK_OUTPUT_IDENTITY_MISMATCH")
    resources = _object(
        invoker.json(
            "cloudformation",
            "describe-stack-resources",
            "--stack-name",
            STACK_NAME,
        ),
        "stack resources",
    )
    by_logical_id: dict[str, dict[str, object]] = {}
    for raw in _array(resources.get("StackResources"), "stack resources"):
        resource_entry = _object(raw, "stack resource")
        logical_id = resource_entry.get("LogicalResourceId")
        if isinstance(logical_id, str) and logical_id not in by_logical_id:
            by_logical_id[logical_id] = resource_entry
        elif isinstance(logical_id, str):
            raise ContinuousPreviewAwsError(f"STACK_RESOURCE_DUPLICATE:{logical_id}")
    supporting_resources = {
        "SchedulerRole": ("AWS::IAM::Role", "scheduler role"),
        "SchedulerDeliveryQueue": ("AWS::SQS::Queue", "scheduler delivery queue"),
    }
    for logical_id, (resource_type, label) in supporting_resources.items():
        supporting_resource = by_logical_id.get(logical_id)
        physical_id = (
            None if supporting_resource is None else supporting_resource.get("PhysicalResourceId")
        )
        if (
            supporting_resource is None
            or supporting_resource.get("ResourceType") != resource_type
            or supporting_resource.get("ResourceStatus")
            not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
            or not isinstance(physical_id, str)
            or not physical_id
        ):
            raise ContinuousPreviewAwsError(f"SCHEDULE_STACK_BINDING_MISMATCH:{label}")
    schedules: dict[str, dict[str, object]] = {}
    for suffix in SCHEDULE_SUFFIXES:
        name = f"{STACK_NAME}-{suffix}"
        logical_id = SCHEDULE_LOGICAL_IDS[suffix]
        schedule_resource = by_logical_id.get(logical_id)
        physical_id = (
            None if schedule_resource is None else schedule_resource.get("PhysicalResourceId")
        )
        expected_physical_ids = {
            name,
            _schedule_arn(expected_account=expected_account, name=name),
        }
        if (
            schedule_resource is None
            or not isinstance(physical_id, str)
            or physical_id not in expected_physical_ids
            or schedule_resource.get("ResourceType") != "AWS::Scheduler::Schedule"
            or schedule_resource.get("ResourceStatus") not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
        ):
            raise ContinuousPreviewAwsError(f"SCHEDULE_STACK_BINDING_MISMATCH:{name}")
        command = SCHEDULE_COMMANDS[suffix]
        expected_target: dict[str, object] = {
            "Arn": outputs["RefreshClusterArn"],
            "DeadLetterConfig": {"Arn": outputs["SchedulerDeliveryQueueArn"]},
            "EcsParameters": {
                "LaunchType": "FARGATE",
                "NetworkConfiguration": {
                    "awsvpcConfiguration": {
                        "AssignPublicIp": "ENABLED",
                        "SecurityGroups": [outputs["TaskSecurityGroupId"]],
                        "Subnets": outputs["PublicSubnetIds"].split(","),
                    }
                },
                "PlatformVersion": "1.4.0",
                "TaskCount": 1,
                "TaskDefinitionArn": outputs["RefreshTaskDefinitionArn"],
            },
            "Input": json.dumps(
                {"containerOverrides": [{"name": "refresh", "command": [command]}]},
                separators=(",", ":"),
            ),
            "RetryPolicy": {
                "MaximumEventAgeInSeconds": 900,
                "MaximumRetryAttempts": 2,
            },
            "RoleArn": outputs["SchedulerRoleArn"],
        }
        schedules[suffix] = _validate_schedule(
            invoker.json(
                "scheduler",
                "get-schedule",
                "--name",
                name,
                "--group-name",
                SCHEDULE_GROUP,
            ),
            expected_account=expected_account,
            name=name,
            expression=SCHEDULE_EXPRESSIONS[suffix],
            expected_target=expected_target,
        )
    return schedules


def _validate_emergency_schedule(
    value: object,
    *,
    expected_account: str,
    name: str,
) -> dict[str, object]:
    """Validate only immutable schedule identity and updateable payload shape."""

    schedule = _object(value, "schedule")
    suffix = name.removeprefix(f"{STACK_NAME}-")
    flexible = schedule.get("FlexibleTimeWindow")
    expression = schedule.get("ScheduleExpression")
    target = schedule.get("Target")
    if (
        suffix not in SCHEDULE_SUFFIXES
        or schedule.get("Name") != name
        or schedule.get("GroupName") != SCHEDULE_GROUP
        or schedule.get("Arn") != _schedule_arn(expected_account=expected_account, name=name)
        or schedule.get("State") not in {"ENABLED", "DISABLED"}
        or not isinstance(flexible, dict)
        or not isinstance(expression, str)
        or not expression.strip()
        or not isinstance(target, dict)
        or not target
    ):
        raise ContinuousPreviewAwsError(f"SCHEDULE_IDENTITY_MISMATCH:{name}")
    return schedule


def _get_emergency_schedule(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    suffix: str,
) -> dict[str, object]:
    name = f"{STACK_NAME}-{suffix}"
    return _validate_emergency_schedule(
        invoker.json(
            "scheduler",
            "get-schedule",
            "--name",
            name,
            "--group-name",
            SCHEDULE_GROUP,
        ),
        expected_account=expected_account,
        name=name,
    )


def _audit_emergency_schedules(
    invoker: AwsInvoker,
    *,
    expected_account: str,
) -> tuple[dict[str, dict[str, object]], tuple[str, ...], tuple[str, ...]]:
    schedules: dict[str, dict[str, object]] = {}
    unknown: set[str] = set()
    enabled: set[str] = set()
    for suffix in SCHEDULE_SUFFIXES:
        name = f"{STACK_NAME}-{suffix}"
        try:
            schedule = _get_emergency_schedule(
                invoker,
                expected_account=expected_account,
                suffix=suffix,
            )
        except (
            ContinuousPreviewAwsError,
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            unknown.add(name)
            enabled.add(name)
            continue
        schedules[suffix] = schedule
        if schedule["State"] != "DISABLED":
            enabled.add(name)
    return schedules, tuple(sorted(unknown)), tuple(sorted(enabled))


def _disable_schedules_best_effort(
    invoker: AwsInvoker,
    *,
    expected_account: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    for suffix in SCHEDULE_SUFFIXES:
        try:
            schedule = _get_emergency_schedule(
                invoker,
                expected_account=expected_account,
                suffix=suffix,
            )
            if schedule["State"] != "DISABLED":
                _update_schedule_state(
                    invoker,
                    schedule=schedule,
                    state="DISABLED",
                    expected_account=expected_account,
                    strict=False,
                )
        except (
            ContinuousPreviewAwsError,
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            continue
    _, unknown, enabled = _audit_emergency_schedules(
        invoker,
        expected_account=expected_account,
    )
    return unknown, enabled


def _schedule_residual(label: str, unknown: tuple[str, ...], enabled: tuple[str, ...]) -> str:
    return (
        f"{label}:unknown={','.join(unknown) if unknown else '-'};"
        f"enabled={','.join(enabled) if enabled else '-'}"
    )


def _schedule_update_payload(schedule: dict[str, object], *, state: str) -> dict[str, object]:
    payload = {
        key: value
        for key, value in schedule.items()
        if key
        in {
            "ActionAfterCompletion",
            "Description",
            "EndDate",
            "FlexibleTimeWindow",
            "GroupName",
            "KmsKeyArn",
            "Name",
            "ScheduleExpression",
            "ScheduleExpressionTimezone",
            "StartDate",
            "Target",
        }
    }
    payload["State"] = state
    return payload


def _update_schedule_state(
    invoker: AwsInvoker,
    *,
    schedule: dict[str, object],
    state: str,
    expected_account: str,
    strict: bool,
) -> dict[str, object]:
    name = str(schedule["Name"])
    response = _object(
        invoker.json(
            "scheduler",
            "update-schedule",
            "--cli-input-json",
            json.dumps(
                _schedule_update_payload(schedule, state=state),
                separators=(",", ":"),
            ),
        ),
        "update schedule response",
    )
    if response.get("ScheduleArn") != _schedule_arn(
        expected_account=expected_account,
        name=name,
    ):
        raise ContinuousPreviewAwsError(f"SCHEDULE_UPDATE_RESPONSE_MISMATCH:{name}")
    readback = invoker.json(
        "scheduler",
        "get-schedule",
        "--name",
        name,
        "--group-name",
        SCHEDULE_GROUP,
    )
    if strict:
        updated = _validate_schedule(
            readback,
            expected_account=expected_account,
            name=name,
            expression=str(schedule["ScheduleExpression"]),
            expected_target=_object(schedule["Target"], "schedule target"),
        )
    else:
        updated = _validate_emergency_schedule(
            readback,
            expected_account=expected_account,
            name=name,
        )
    if updated.get("State") != state:
        raise ContinuousPreviewAwsError(f"SCHEDULE_UPDATE_READBACK_MISMATCH:{name}")
    return updated


def set_schedules(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    state: str,
    confirmation: str,
    product_sha: str | None = None,
    image_digest: str | None = None,
) -> None:
    """Mutate every stack-bound schedule with state-specific compensation."""

    if state not in {"ENABLED", "DISABLED"}:
        raise ContinuousPreviewAwsError("SCHEDULE_STATE_INVALID")
    expected_confirmation = (
        "ENABLE_CONTINUOUS_PREVIEW_SCHEDULES"
        if state == "ENABLED"
        else "DISABLE_CONTINUOUS_PREVIEW_SCHEDULES"
    )
    if confirmation != expected_confirmation:
        raise ContinuousPreviewAwsError("SCHEDULE_CONFIRMATION_REQUIRED")
    if ACCOUNT.fullmatch(expected_account) is None:
        raise ContinuousPreviewAwsError("EXPECTED_ACCOUNT_INVALID")
    if state == "ENABLED":
        if product_sha is None or image_digest is None:
            raise ContinuousPreviewAwsError("RUNTIME_CANDIDATE_REQUIRED")
        outputs = _candidate_stack_outputs(
            invoker,
            expected_account=expected_account,
            product_sha=product_sha,
            image_digest=image_digest,
        )
        original = _load_bound_schedules(
            invoker,
            expected_account=expected_account,
            outputs=outputs,
        )
        try:
            for suffix in SCHEDULE_SUFFIXES:
                schedule = original[suffix]
                if schedule["State"] == state:
                    continue
                _update_schedule_state(
                    invoker,
                    schedule=schedule,
                    state=state,
                    expected_account=expected_account,
                    strict=True,
                )
            final_outputs = _candidate_stack_outputs(
                invoker,
                expected_account=expected_account,
                product_sha=product_sha,
                image_digest=image_digest,
            )
            final = _load_bound_schedules(
                invoker,
                expected_account=expected_account,
                outputs=final_outputs,
            )
            wrong = [
                str(schedule["Name"]) for schedule in final.values() if schedule["State"] != state
            ]
            if wrong:
                raise ContinuousPreviewAwsError(
                    f"SCHEDULE_ENABLE_INCOMPLETE:{','.join(sorted(wrong))}"
                )
        except (
            ContinuousPreviewAwsError,
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as error:
            unknown, enabled = _disable_schedules_best_effort(
                invoker,
                expected_account=expected_account,
            )
            if unknown or enabled:
                raise ContinuousPreviewAwsError(
                    _schedule_residual(
                        "SCHEDULE_ENABLE_COMPENSATION_FAILED",
                        unknown,
                        enabled,
                    )
                ) from error
            raise ContinuousPreviewAwsError("SCHEDULE_ENABLE_FAILED") from error
        return

    verify_identity(
        invoker.json("sts", "get-caller-identity"),
        expected_account=expected_account,
    )
    unknown, enabled = _disable_schedules_best_effort(
        invoker,
        expected_account=expected_account,
    )
    if unknown or enabled:
        raise ContinuousPreviewAwsError(
            _schedule_residual("SCHEDULE_DISABLE_INCOMPLETE", unknown, enabled)
        )


def assert_schedules_disabled(
    invoker: AwsInvoker,
    *,
    expected_account: str,
) -> dict[str, dict[str, object]]:
    """Fail closed unless every stack-bound schedule is disabled."""

    if ACCOUNT.fullmatch(expected_account) is None:
        raise ContinuousPreviewAwsError("EXPECTED_ACCOUNT_INVALID")
    verify_identity(
        invoker.json("sts", "get-caller-identity"),
        expected_account=expected_account,
    )
    schedules, unknown, enabled = _audit_emergency_schedules(
        invoker,
        expected_account=expected_account,
    )
    if unknown:
        raise ContinuousPreviewAwsError(f"ROLLBACK_SCHEDULE_STATE_UNKNOWN:{','.join(unknown)}")
    if enabled:
        raise ContinuousPreviewAwsError(f"ROLLBACK_SCHEDULE_NOT_DISABLED:{','.join(enabled)}")
    return schedules


def _refresh_cluster_arn(expected_account: str) -> str:
    return f"arn:aws:ecs:{REGION}:{expected_account}:cluster/{STACK_NAME}-cluster"


def _assert_refresh_cluster(
    invoker: AwsInvoker,
    *,
    expected_account: str,
) -> tuple[str, int, int]:
    expected_arn = _refresh_cluster_arn(expected_account)
    response = _object(
        invoker.json(
            "ecs",
            "describe-clusters",
            "--clusters",
            expected_arn,
        ),
        "describe refresh cluster",
    )
    if _array(response.get("failures"), "cluster failures"):
        raise ContinuousPreviewAwsError("ROLLBACK_CLUSTER_IDENTITY_INVALID")
    clusters = _array(response.get("clusters"), "refresh clusters")
    if len(clusters) != 1:
        raise ContinuousPreviewAwsError("ROLLBACK_CLUSTER_IDENTITY_INVALID")
    cluster = _object(clusters[0], "refresh cluster")
    pending_count = cluster.get("pendingTasksCount")
    running_count = cluster.get("runningTasksCount")
    if (
        cluster.get("clusterArn") != expected_arn
        or cluster.get("clusterName") != f"{STACK_NAME}-cluster"
        or cluster.get("status") != "ACTIVE"
        or not isinstance(pending_count, int)
        or isinstance(pending_count, bool)
        or pending_count < 0
        or not isinstance(running_count, int)
        or isinstance(running_count, bool)
        or running_count < 0
    ):
        raise ContinuousPreviewAwsError("ROLLBACK_CLUSTER_IDENTITY_INVALID")
    return expected_arn, pending_count, running_count


def _list_refresh_task_arns(
    invoker: AwsInvoker,
    *,
    cluster_arn: str,
) -> tuple[str, ...]:
    response = _object(
        invoker.json(
            "ecs",
            "list-tasks",
            "--cluster",
            cluster_arn,
            "--desired-status",
            "RUNNING",
        ),
        "list active refresh tasks",
    )
    task_arns: list[str] = []
    for value in _array(response.get("taskArns"), "refresh task ARNs"):
        if not isinstance(value, str) or not value:
            raise ContinuousPreviewAwsError("ROLLBACK_TASK_IDENTITY_INVALID")
        task_arns.append(value)
    if len(task_arns) != len(set(task_arns)):
        raise ContinuousPreviewAwsError("ROLLBACK_TASK_IDENTITY_INVALID")
    return tuple(task_arns)


def _active_refresh_tasks(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    cluster_arn: str,
) -> tuple[str, ...]:
    listed = set(_list_refresh_task_arns(invoker, cluster_arn=cluster_arn))
    if not listed:
        return ()
    task_prefix = f"arn:aws:ecs:{REGION}:{expected_account}:task/{STACK_NAME}-cluster/"
    if any(not task_arn.startswith(task_prefix) for task_arn in listed):
        raise ContinuousPreviewAwsError("ROLLBACK_TASK_IDENTITY_INVALID")
    response = _object(
        invoker.json(
            "ecs",
            "describe-tasks",
            "--cluster",
            cluster_arn,
            "--tasks",
            *sorted(listed),
        ),
        "describe refresh tasks",
    )
    if _array(response.get("failures"), "refresh task failures"):
        raise ContinuousPreviewAwsError("ROLLBACK_TASK_IDENTITY_INVALID")
    tasks = [_object(value, "refresh task") for value in _array(response.get("tasks"), "tasks")]
    by_arn = {task.get("taskArn"): task for task in tasks}
    if set(by_arn) != listed or len(by_arn) != len(tasks):
        raise ContinuousPreviewAwsError("ROLLBACK_TASK_IDENTITY_INVALID")
    task_definition = re.compile(
        rf"^arn:aws:ecs:{re.escape(REGION)}:{expected_account}:task-definition/"
        rf"{re.escape(STACK_NAME)}-(?:bootstrap|refresh):[1-9][0-9]*$"
    )
    active: list[str] = []
    for task_arn, task in by_arn.items():
        last_status = task.get("lastStatus")
        if (
            task.get("clusterArn") != cluster_arn
            or not isinstance(task.get("taskDefinitionArn"), str)
            or task_definition.fullmatch(str(task["taskDefinitionArn"])) is None
            or last_status not in {"PENDING", "RUNNING", "STOPPED"}
        ):
            raise ContinuousPreviewAwsError("ROLLBACK_TASK_IDENTITY_INVALID")
        if last_status in {"PENDING", "RUNNING"}:
            active.append(cast(str, task_arn))
    return tuple(sorted(active))


def _wait_for_rollback_quiescence(
    invoker: AwsInvoker,
    *,
    expected_account: str,
    fence_started_at: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    cluster_arn, _, _ = _assert_refresh_cluster(
        invoker,
        expected_account=expected_account,
    )
    fence_deadline = fence_started_at + SCHEDULER_RETRY_FENCE_SECONDS
    remaining = fence_deadline - monotonic()
    if remaining > 0:
        sleep(remaining)
    if monotonic() < fence_deadline:
        raise ContinuousPreviewAwsError("ROLLBACK_RETRY_FENCE_INCOMPLETE")

    empty_sweeps = 0
    for poll in range(ROLLBACK_DRAIN_MAX_POLLS):
        rebound_cluster_arn, pending_count, running_count = _assert_refresh_cluster(
            invoker,
            expected_account=expected_account,
        )
        if rebound_cluster_arn != cluster_arn:
            raise ContinuousPreviewAwsError("ROLLBACK_CLUSTER_IDENTITY_INVALID")
        active = _active_refresh_tasks(
            invoker,
            expected_account=expected_account,
            cluster_arn=cluster_arn,
        )
        empty = not active and pending_count == 0 and running_count == 0
        empty_sweeps = empty_sweeps + 1 if empty else 0
        if empty_sweeps >= 2:
            return
        if poll + 1 < ROLLBACK_DRAIN_MAX_POLLS:
            sleep(ROLLBACK_DRAIN_POLL_SECONDS)
    raise ContinuousPreviewAwsError("ROLLBACK_TASK_DRAIN_TIMEOUT")


def rollback_pointer(
    invoker: AwsInvoker,
    *,
    client: S3SnapshotClient,
    expected_account: str,
    bucket: str,
    pointer_version_id: str,
    expected_snapshot_id: str,
    confirmation: str,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str | None]:
    """Restore a validated versioned pointer only after every trigger is disabled."""

    if confirmation != "RESTORE_PREVIOUS_POINTER":
        raise ContinuousPreviewAwsError("ROLLBACK_CONFIRMATION_REQUIRED")
    try:
        canonical_snapshot_id = str(UUID(expected_snapshot_id))
    except ValueError as error:
        raise ContinuousPreviewAwsError("ROLLBACK_SNAPSHOT_ID_INVALID") from error
    if canonical_snapshot_id != expected_snapshot_id:
        raise ContinuousPreviewAwsError("ROLLBACK_SNAPSHOT_ID_INVALID")
    assert_schedules_disabled(invoker, expected_account=expected_account)
    fence_started_at = monotonic()
    _wait_for_rollback_quiescence(
        invoker,
        expected_account=expected_account,
        fence_started_at=fence_started_at,
        monotonic=monotonic,
        sleep=sleep,
    )
    assert_schedules_disabled(invoker, expected_account=expected_account)
    target, restored_version = S3SnapshotPublisher(
        client=client,
        bucket=bucket,
    ).restore_pointer_version(
        version_id=pointer_version_id,
        expected_snapshot_id=canonical_snapshot_id,
    )
    return {
        "snapshot_id": str(target.validated.pointer.snapshot_id),
        "product_commit": target.validated.pointer.product_commit,
        "restored_pointer_version_id": restored_version,
    }


def _cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    deploy_parser = sub.add_parser("deploy")
    deploy_parser.add_argument("--expected-account", required=True)
    deploy_parser.add_argument("--product-sha", required=True)
    deploy_parser.add_argument("--bucket", required=True)
    deploy_parser.add_argument("--public-base-url", required=True)
    deploy_parser.add_argument("--image-digest", default="")
    deploy_parser.add_argument("--deepseek-secret-arn", default="")
    deploy_parser.add_argument("--research-data-secret-arn", default="")
    deploy_parser.add_argument("--promax-secret-arn", default="")
    deploy_parser.add_argument("--cloudfront-origin-prefix-list-id", default="")
    deploy_parser.add_argument("--confirm", required=True)
    task_parser = sub.add_parser("run-task")
    task_parser.add_argument(
        "operation",
        choices=(
            "migrate",
            "seed",
            "seed-last-session",
            "behavior-last-session",
            "ingest",
            "publish",
            "catch-up",
            "llm-smoke",
            "source-smoke",
        ),
    )
    task_parser.add_argument("--expected-account", required=True)
    task_parser.add_argument("--product-sha", required=True)
    task_parser.add_argument("--image-digest", required=True)
    schedule_parser = sub.add_parser("set-schedules")
    schedule_parser.add_argument("state", choices=("ENABLED", "DISABLED"))
    schedule_parser.add_argument("--expected-account", required=True)
    schedule_parser.add_argument("--product-sha")
    schedule_parser.add_argument("--image-digest")
    schedule_parser.add_argument("--confirm", required=True)
    rollback_parser = sub.add_parser("rollback-pointer")
    rollback_parser.add_argument("--expected-account", required=True)
    rollback_parser.add_argument("--bucket", required=True)
    rollback_parser.add_argument("--pointer-version-id", required=True)
    rollback_parser.add_argument("--snapshot-id", required=True)
    rollback_parser.add_argument("--confirm", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _cli().parse_args(argv)
    invoker = AwsCli()
    version = subprocess.run(  # noqa: S603 - exact AWS executable and flag only.
        [invoker.executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    verify_cli_v2(version.stdout or version.stderr)
    if arguments.command == "deploy":
        changes = deploy(
            invoker,
            expected_account=arguments.expected_account,
            product_sha=arguments.product_sha,
            bucket=arguments.bucket,
            public_base_url=arguments.public_base_url,
            image_digest=arguments.image_digest,
            confirmation=arguments.confirm,
            deepseek_secret_arn=arguments.deepseek_secret_arn,
            research_data_secret_arn=arguments.research_data_secret_arn,
            promax_secret_arn=arguments.promax_secret_arn,
            cloudfront_origin_prefix_list_id=arguments.cloudfront_origin_prefix_list_id,
        )
        print(json.dumps(changes, sort_keys=True))
    elif arguments.command == "run-task":
        print(
            json.dumps(
                {
                    "task_arn": run_task(
                        invoker,
                        operation=arguments.operation,
                        expected_account=arguments.expected_account,
                        product_sha=arguments.product_sha,
                        image_digest=arguments.image_digest,
                    )
                }
            )
        )
    elif arguments.command == "set-schedules":
        set_schedules(
            invoker,
            expected_account=arguments.expected_account,
            state=arguments.state,
            confirmation=arguments.confirm,
            product_sha=arguments.product_sha,
            image_digest=arguments.image_digest,
        )
        print(json.dumps({"schedules": arguments.state}))
    else:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            region_name=REGION,
            config=Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"mode": "standard", "max_attempts": 3},
                request_checksum_calculation="when_supported",
                response_checksum_validation="when_supported",
            ),
        )
        print(
            json.dumps(
                rollback_pointer(
                    invoker,
                    client=client,
                    expected_account=arguments.expected_account,
                    bucket=arguments.bucket,
                    pointer_version_id=arguments.pointer_version_id,
                    expected_snapshot_id=arguments.snapshot_id,
                    confirmation=arguments.confirm,
                ),
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        ContinuousPreviewAwsError,
        SnapshotPublicationError,
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"Continuous preview AWS operation blocked: {error}", file=sys.stderr)
        sys.exit(2)

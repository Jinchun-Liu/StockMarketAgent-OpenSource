"""Receipt-bound AWS operator for the private synthetic R2 preview."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Final, Protocol
from urllib.parse import quote
from uuid import uuid4

from scripts.public_demo_aws import assert_cloudformation_template_alias_free

REGION: Final = "ap-northeast-1"
STACK_NAME: Final = "stockmarketagent-r2-preview"
PUBLIC_STACK_NAME: Final = "stockmarketagent-public-demo-tokyo"
RUNTIME_REPOSITORY_NAME: Final = "stockmarketagent-r2-preview"
TEMPLATE: Final = Path("deploy/aws/r2-private-preview.yaml")
SHA: Final = re.compile(r"^[0-9a-f]{40}$")
DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCOUNT: Final = re.compile(r"^[0-9]{12}$")
PREFIX_LIST: Final = re.compile(r"^pl-[0-9a-f]{8,17}$")
COGNITO_PREFIX: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
PUBLIC_URL: Final = re.compile(r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?/$")
SECRET_ARN: Final = re.compile(
    r"^arn:aws:secretsmanager:ap-northeast-1:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$"
)
PLAN_SCHEMA: Final = "1.0"
INLINE_TEMPLATE_LIMIT: Final = 51_200
TEMPLATE_KEY_SEGMENT: Final = "deployment-templates"
PROJECT_TAGS: Final = {"Project": "stockmarketagent-r2-preview", "ManagedBy": "StockMarketAgent"}
PROTECTED_REPLACEMENT: Final = {
    "CloudFrontOriginVerifySecret",
    "PreviewVpc",
    "ResearchArtifactBucket",
    "ResearchDatabase",
    "ResearchValkey",
    "RuntimeRepository",
    "UserPool",
    "ValkeyAuthSecret",
}
REPLACEABLE_RESOURCES: Final = {
    "ApiIngressFromLoadBalancer": "AWS::EC2::SecurityGroupIngress",
    "ApiTaskDefinition": "AWS::ECS::TaskDefinition",
    "EgressProxyTaskDefinition": "AWS::ECS::TaskDefinition",
    "LoadBalancerEgressToApi": "AWS::EC2::SecurityGroupEgress",
    "MigrationTaskDefinition": "AWS::ECS::TaskDefinition",
    "SchedulerTaskDefinition": "AWS::ECS::TaskDefinition",
    "WorkerTaskDefinition": "AWS::ECS::TaskDefinition",
}
REMOVABLE_RESOURCES: Final = {
    "DatabaseIngressFromTasks": "AWS::EC2::SecurityGroupIngress",
    "TaskDatabaseEgress": "AWS::EC2::SecurityGroupEgress",
    "TaskDnsTcpEgress": "AWS::EC2::SecurityGroupEgress",
    "TaskDnsUdpEgress": "AWS::EC2::SecurityGroupEgress",
    "TaskHttpsEgress": "AWS::EC2::SecurityGroupEgress",
    "TaskValkeyEgress": "AWS::EC2::SecurityGroupEgress",
    "ValkeyIngressFromTasks": "AWS::EC2::SecurityGroupIngress",
}


class PreviewAwsError(RuntimeError):
    """An AWS response crossed the reviewed private-preview boundary."""


class AwsInvoker(Protocol):
    def json(self, service: str, operation: str, *arguments: str) -> object: ...

    def text(self, service: str, operation: str, *arguments: str) -> str: ...


def locate_aws_cli() -> str:
    """Use an explicit path, PATH, then documented macOS AWS CLI v2 locations."""

    candidates = [
        os.getenv("STOCK_ASSISTANT_AWS_CLI"),
        which("aws"),
        "/usr/local/bin/aws",
        "/usr/local/aws-cli/v2/current/bin/aws",
        "/opt/homebrew/bin/aws",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise PreviewAwsError("AWS_CLI_V2_NOT_FOUND")


@dataclass(frozen=True, slots=True)
class AwsCli:
    executable: str

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
        timeout = 3600 if operation in {"wait", "execute-change-set"} else 180
        completed = subprocess.run(  # noqa: S603 - argv only; no shell expansion.
            self._command(service, operation, *arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout

    def json(self, service: str, operation: str, *arguments: str) -> object:
        try:
            return json.loads(self.text(service, operation, *arguments, "--output", "json"))
        except json.JSONDecodeError as error:
            raise PreviewAwsError("AWS_CLI_INVALID_JSON") from error


def verify_cli_v2(executable: str) -> None:
    completed = subprocess.run(  # noqa: S603 - fixed executable and one inert flag.
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = (completed.stdout or completed.stderr).strip()
    if not output.startswith("aws-cli/2."):
        raise PreviewAwsError("AWS_CLI_V2_REQUIRED")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PreviewAwsError(f"{label}:OBJECT_REQUIRED")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise PreviewAwsError(f"{label}:ARRAY_REQUIRED")
    return value


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _large_template_reference(
    invoker: AwsInvoker,
    *,
    existing_stack: dict[str, object] | None,
    product_sha: str,
    template: Path,
    template_bytes: bytes,
    template_sha256: str,
) -> tuple[tuple[str, str], dict[str, str]]:
    if existing_stack is None:
        raise PreviewAwsError("LARGE_TEMPLATE_REQUIRES_EXISTING_ARTIFACT_BUCKET")
    bucket = _outputs(existing_stack).get("ArtifactBucketName", "")
    if not bucket or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise PreviewAwsError("ARTIFACT_BUCKET_OUTPUT_INVALID")
    key = f"releases/{product_sha}/{TEMPLATE_KEY_SEGMENT}/{template_sha256}.yaml"
    checksum = base64.b64encode(bytes.fromhex(template_sha256)).decode("ascii")
    uploaded = _object(
        invoker.json(
            "s3api",
            "put-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--body",
            str(template),
            "--server-side-encryption",
            "AES256",
            "--checksum-sha256",
            checksum,
            "--metadata",
            f"productsha={product_sha},templatesha256={template_sha256}",
        ),
        "template upload",
    )
    version_id = uploaded.get("VersionId")
    if not isinstance(version_id, str) or not version_id.strip():
        raise PreviewAwsError("VERSIONED_TEMPLATE_UPLOAD_NOT_CONFIRMED")
    source = {
        "type": "s3_version",
        "bucket": bucket,
        "key": key,
        "version_id": version_id,
        "sha256": template_sha256,
        "product_sha": product_sha,
    }
    _verify_large_template_reference(invoker, source=source, expected_size=len(template_bytes))
    url = (
        f"https://s3.{REGION}.amazonaws.com/{quote(bucket, safe='')}/{quote(key, safe='/')}"
        f"?versionId={quote(version_id, safe='')}"
    )
    return ("--template-url", url), source


def _verify_large_template_reference(
    invoker: AwsInvoker,
    *,
    source: dict[str, str],
    expected_size: int,
) -> None:
    required = {"bucket", "key", "version_id", "sha256", "product_sha"}
    if source.get("type") != "s3_version" or not required.issubset(source):
        raise PreviewAwsError("TEMPLATE_SOURCE_INVALID")
    expected_key = (
        f"releases/{source['product_sha']}/{TEMPLATE_KEY_SEGMENT}/{source['sha256']}.yaml"
    )
    if (
        SHA.fullmatch(source["product_sha"]) is None
        or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
        or source["key"] != expected_key
        or not source["version_id"].strip()
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", source["bucket"])
    ):
        raise PreviewAwsError("TEMPLATE_SOURCE_INVALID")
    head = _object(
        invoker.json(
            "s3api",
            "head-object",
            "--bucket",
            source["bucket"],
            "--key",
            source["key"],
            "--version-id",
            source["version_id"],
            "--checksum-mode",
            "ENABLED",
        ),
        "template readback",
    )
    metadata = _object(head.get("Metadata"), "template metadata")
    if (
        head.get("VersionId") != source["version_id"]
        or head.get("ContentLength") != expected_size
        or head.get("ServerSideEncryption") != "AES256"
        or head.get("ChecksumSHA256")
        != base64.b64encode(bytes.fromhex(source["sha256"])).decode("ascii")
        or metadata.get("productsha") != source["product_sha"]
        or metadata.get("templatesha256") != source["sha256"]
    ):
        raise PreviewAwsError("VERSIONED_TEMPLATE_READBACK_MISMATCH")


def verify_identity(payload: object, expected_account_id: str) -> dict[str, object]:
    if ACCOUNT.fullmatch(expected_account_id) is None:
        raise PreviewAwsError("EXPECTED_ACCOUNT_INVALID")
    identity = _object(payload, "identity")
    arn = identity.get("Arn")
    if identity.get("Account") != expected_account_id:
        raise PreviewAwsError("AWS_ACCOUNT_MISMATCH")
    if not isinstance(arn, str) or f"::{expected_account_id}:" not in arn:
        raise PreviewAwsError("AWS_PRINCIPAL_INVALID")
    return identity


def verify_image_binding(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    image_digest: str,
) -> dict[str, object]:
    """Require one immutable ECR image to carry the exact candidate SHA tag."""

    if SHA.fullmatch(product_sha) is None or DIGEST.fullmatch(image_digest) is None:
        raise PreviewAwsError("IMAGE_BINDING_INPUT_INVALID")
    payload = _object(
        invoker.json(
            "ecr",
            "describe-images",
            "--repository-name",
            RUNTIME_REPOSITORY_NAME,
            "--image-ids",
            f"imageTag={product_sha},imageDigest={image_digest}",
        ),
        "runtime image",
    )
    details = _array(payload.get("imageDetails"), "runtime image details")
    detail = _object(details[0], "runtime image detail") if len(details) == 1 else {}
    tags = detail.get("imageTags")
    if (
        detail.get("registryId") != expected_account_id
        or detail.get("repositoryName") != RUNTIME_REPOSITORY_NAME
        or detail.get("imageDigest") != image_digest
        or not isinstance(tags, list)
        or product_sha not in tags
        or not all(isinstance(tag, str) for tag in tags)
    ):
        raise PreviewAwsError("ECR_IMAGE_SHA_DIGEST_MISMATCH")
    return detail


def _stack_or_none(invoker: AwsInvoker, stack_name: str) -> dict[str, object] | None:
    try:
        payload = _object(
            invoker.json("cloudformation", "describe-stacks", "--stack-name", stack_name),
            "describe stacks",
        )
    except subprocess.CalledProcessError as error:
        detail = f"{error.stderr or ''} {error.stdout or ''}"
        if "does not exist" in detail:
            return None
        raise
    stacks = _array(payload.get("Stacks"), "stacks")
    if len(stacks) != 1:
        raise PreviewAwsError("STACK_IDENTITY_AMBIGUOUS")
    stack = _object(stacks[0], "stack")
    if stack.get("StackName") != stack_name:
        raise PreviewAwsError("STACK_NAME_MISMATCH")
    return stack


def _outputs(stack: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in _array(stack.get("Outputs"), "stack outputs"):
        item = _object(raw, "stack output")
        key, value = item.get("OutputKey"), item.get("OutputValue")
        if not isinstance(key, str) or not isinstance(value, str) or key in result:
            raise PreviewAwsError("STACK_OUTPUT_INVALID")
        result[key] = value
    return result


def preflight(invoker: AwsInvoker, *, expected_account_id: str) -> dict[str, object]:
    identity = verify_identity(invoker.json("sts", "get-caller-identity"), expected_account_id)

    def managed_prefix_list(name: str, error_prefix: str) -> str:
        payload = _object(
            invoker.json(
                "ec2",
                "describe-managed-prefix-lists",
                "--filters",
                f"Name=prefix-list-name,Values={name}",
            ),
            "managed prefix lists",
        )
        prefix_lists = _array(payload.get("PrefixLists"), "prefix lists")
        if len(prefix_lists) != 1:
            raise PreviewAwsError(f"{error_prefix}_PREFIX_LIST_NOT_UNIQUE")
        prefix_list = _object(prefix_lists[0], "prefix list")
        prefix_id = prefix_list.get("PrefixListId")
        if not isinstance(prefix_id, str) or PREFIX_LIST.fullmatch(prefix_id) is None:
            raise PreviewAwsError(f"{error_prefix}_PREFIX_LIST_INVALID")
        return prefix_id

    cloudfront_prefix_id = managed_prefix_list(
        "com.amazonaws.global.cloudfront.origin-facing", "CLOUDFRONT"
    )
    s3_prefix_id = managed_prefix_list(f"com.amazonaws.{REGION}.s3", "S3")
    runtime = _stack_or_none(invoker, STACK_NAME)
    public = _stack_or_none(invoker, PUBLIC_STACK_NAME)
    return {
        "schema_version": "1.0",
        "account_id": expected_account_id,
        "principal_arn": identity["Arn"],
        "region": REGION,
        "cloudfront_origin_prefix_list_id": cloudfront_prefix_id,
        "s3_managed_prefix_list_id": s3_prefix_id,
        "runtime_stack": None
        if runtime is None
        else {
            "status": runtime.get("StackStatus"),
            "outputs": _outputs(runtime),
        },
        "public_shell_stack": None
        if public is None
        else {"status": public.get("StackStatus"), "outputs": _outputs(public)},
        "observed_at": datetime.now(UTC).isoformat(),
    }


def _parameters(
    *,
    product_sha: str,
    image_digest: str,
    desired_count: int,
    public_base_url: str,
    cognito_domain_prefix: str,
    prefix_list_id: str,
    s3_prefix_list_id: str,
    deepseek_api_secret_arn: str = "",
    tavily_api_secret_arn: str = "",
) -> dict[str, str]:
    if SHA.fullmatch(product_sha) is None:
        raise PreviewAwsError("PRODUCT_SHA_INVALID")
    if image_digest and DIGEST.fullmatch(image_digest) is None:
        raise PreviewAwsError("IMAGE_DIGEST_INVALID")
    if desired_count not in {0, 1} or (desired_count == 1 and not image_digest):
        raise PreviewAwsError("RUNTIME_ACTIVATION_INVALID")
    if PUBLIC_URL.fullmatch(public_base_url) is None:
        raise PreviewAwsError("PUBLIC_BASE_URL_MUST_END_WITH_SLASH")
    if COGNITO_PREFIX.fullmatch(cognito_domain_prefix) is None:
        raise PreviewAwsError("COGNITO_DOMAIN_PREFIX_INVALID")
    if PREFIX_LIST.fullmatch(prefix_list_id) is None:
        raise PreviewAwsError("CLOUDFRONT_PREFIX_LIST_INVALID")
    if PREFIX_LIST.fullmatch(s3_prefix_list_id) is None:
        raise PreviewAwsError("S3_PREFIX_LIST_INVALID")
    if image_digest and (
        SECRET_ARN.fullmatch(deepseek_api_secret_arn) is None
        or SECRET_ARN.fullmatch(tavily_api_secret_arn) is None
    ):
        raise PreviewAwsError("RUNTIME_MODEL_SECRETS_INVALID")
    if any(
        value and SECRET_ARN.fullmatch(value) is None
        for value in (deepseek_api_secret_arn, tavily_api_secret_arn)
    ):
        raise PreviewAwsError("RUNTIME_MODEL_SECRETS_INVALID")
    return {
        "ProductSha": product_sha,
        "ImageDigest": image_digest,
        "RuntimeDesiredCount": str(desired_count),
        "PublicBaseUrl": public_base_url,
        "CognitoDomainPrefix": cognito_domain_prefix,
        "CloudFrontOriginFacingPrefixListId": prefix_list_id,
        "S3ManagedPrefixListId": s3_prefix_list_id,
        "DatabaseName": "stock_assistant",
        "MasterUsername": "stock_admin",
        "DeepSeekApiSecretArn": deepseek_api_secret_arn,
        "TavilyApiSecretArn": tavily_api_secret_arn,
    }


def _normalize_changes(payload: dict[str, object]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in _array(payload.get("Changes"), "change set changes"):
        change = _object(raw, "change")
        resource = _object(change.get("ResourceChange"), "resource change")
        action = resource.get("Action")
        logical_id = resource.get("LogicalResourceId")
        resource_type = resource.get("ResourceType")
        replacement = resource.get("Replacement")
        if action not in {"Add", "Modify", "Remove"}:
            raise PreviewAwsError("CHANGE_SET_DELETE_FORBIDDEN")
        if not isinstance(logical_id, str) or not isinstance(resource_type, str):
            raise PreviewAwsError("CHANGE_SET_RESOURCE_INVALID")
        if action == "Remove" and REMOVABLE_RESOURCES.get(logical_id) != resource_type:
            raise PreviewAwsError("CHANGE_SET_DELETE_FORBIDDEN")
        if replacement not in {None, False, "False"}:
            if logical_id in PROTECTED_REPLACEMENT:
                raise PreviewAwsError(f"PROTECTED_REPLACEMENT:{logical_id}")
            if action != "Modify" or REPLACEABLE_RESOURCES.get(logical_id) != resource_type:
                raise PreviewAwsError(f"UNREVIEWED_REPLACEMENT:{logical_id}")
        normalized.append(
            {
                "action": action,
                "logical_id": logical_id,
                "resource_type": resource_type,
                "replacement": (
                    "False" if replacement in {None, False, "False"} else str(replacement)
                ),
            }
        )
    return sorted(normalized, key=lambda item: (item["logical_id"], item["action"]))


def _validate_described_change_set(
    payload: object,
    *,
    expected_account_id: str,
    expected_arn: str,
    expected_parameters: dict[str, str],
    expected_execution_status: str,
) -> list[dict[str, str]]:
    described = _object(payload, "change set")
    if (
        described.get("ChangeSetId") != expected_arn
        or described.get("StackName") != STACK_NAME
        or not expected_arn.startswith(
            f"arn:aws:cloudformation:{REGION}:{expected_account_id}:changeSet/"
        )
        or described.get("Status") != "CREATE_COMPLETE"
        or described.get("ExecutionStatus") != expected_execution_status
    ):
        raise PreviewAwsError("CHANGE_SET_IDENTITY_OR_STATE_MISMATCH")
    actual: dict[str, str] = {}
    for raw in _array(described.get("Parameters"), "change set parameters"):
        item = _object(raw, "change set parameter")
        key, value = item.get("ParameterKey"), item.get("ParameterValue")
        if not isinstance(key, str) or not isinstance(value, str) or key in actual:
            raise PreviewAwsError("CHANGE_SET_PARAMETER_INVALID")
        actual[key] = value
    if actual != expected_parameters:
        raise PreviewAwsError("CHANGE_SET_PARAMETERS_MISMATCH")
    return _normalize_changes(described)


def plan(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    template: Path,
    output: Path,
    parameters: dict[str, str],
    migration_receipt: Path | None,
) -> dict[str, object]:
    verify_identity(invoker.json("sts", "get-caller-identity"), expected_account_id)
    resolved_template = template.resolve()
    if resolved_template != template or not template.is_file():
        raise PreviewAwsError("TEMPLATE_CANONICAL_FILE_REQUIRED")
    template_bytes = template.read_bytes()
    template_sha256 = hashlib.sha256(template_bytes).hexdigest()
    assert_cloudformation_template_alias_free(template_bytes)
    desired_count = int(parameters["RuntimeDesiredCount"])
    receipt_hash = ""
    if desired_count == 1:
        if migration_receipt is None:
            raise PreviewAwsError("MIGRATION_RECEIPT_REQUIRED")
        receipt = _object(json.loads(migration_receipt.read_text()), "migration receipt")
        receipt_hash = str(receipt.get("receipt_sha256", ""))
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if (
            receipt_hash != _canonical_hash(unsigned)
            or receipt.get("account_id") != expected_account_id
            or receipt.get("product_sha") != parameters["ProductSha"]
            or receipt.get("image_digest") != parameters["ImageDigest"]
            or receipt.get("exit_code") != 0
        ):
            raise PreviewAwsError("MIGRATION_RECEIPT_INVALID")
    image_digest = parameters["ImageDigest"]
    if image_digest:
        verify_image_binding(
            invoker,
            expected_account_id=expected_account_id,
            product_sha=parameters["ProductSha"],
            image_digest=image_digest,
        )
    existing = _stack_or_none(invoker, STACK_NAME)
    template_source: dict[str, str] = {
        "type": "inline",
        "sha256": template_sha256,
    }
    template_argument = ("--template-body", f"file://{template}")
    if len(template_bytes) > INLINE_TEMPLATE_LIMIT:
        template_argument, template_source = _large_template_reference(
            invoker,
            existing_stack=existing,
            product_sha=parameters["ProductSha"],
            template=template,
            template_bytes=template_bytes,
            template_sha256=template_sha256,
        )
    invoker.json("cloudformation", "validate-template", *template_argument)
    change_type = "UPDATE" if existing is not None else "CREATE"
    change_name = f"r2-preview-{parameters['ProductSha'][:10]}-{uuid4().hex[:8]}"
    created = _object(
        invoker.json(
            "cloudformation",
            "create-change-set",
            "--stack-name",
            STACK_NAME,
            "--change-set-name",
            change_name,
            "--change-set-type",
            change_type,
            *template_argument,
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--parameters",
            *(f"ParameterKey={key},ParameterValue={value}" for key, value in parameters.items()),
            "--tags",
            *(f"Key={key},Value={value}" for key, value in PROJECT_TAGS.items()),
        ),
        "created change set",
    )
    arn = created.get("Id")
    if not isinstance(arn, str):
        raise PreviewAwsError("CHANGE_SET_ARN_MISSING")
    deadline = time.monotonic() + 300
    described: object = {}
    while time.monotonic() < deadline:
        described = invoker.json("cloudformation", "describe-change-set", "--change-set-name", arn)
        state = _object(described, "change set").get("Status")
        if state == "CREATE_COMPLETE":
            break
        if state == "FAILED":
            raise PreviewAwsError("CHANGE_SET_PLAN_FAILED")
        time.sleep(2)
    changes = _validate_described_change_set(
        described,
        expected_account_id=expected_account_id,
        expected_arn=arn,
        expected_parameters=parameters,
        expected_execution_status="AVAILABLE",
    )
    document: dict[str, object] = {
        "schema_version": PLAN_SCHEMA,
        "account_id": expected_account_id,
        "region": REGION,
        "stack_name": STACK_NAME,
        "product_sha": parameters["ProductSha"],
        "image_digest": parameters["ImageDigest"],
        "desired_count": desired_count,
        "parameters": parameters,
        "template_sha256": template_sha256,
        "template_source": template_source,
        "change_set_arn": arn,
        "change_set_type": change_type,
        "changes": changes,
        "migration_receipt_sha256": receipt_hash,
    }
    document["plan_sha256"] = _canonical_hash(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def deploy(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    template: Path,
    plan_path: Path,
    plan_sha256: str,
    confirmation: str,
) -> dict[str, str]:
    if confirmation != "DEPLOY_R2_PREVIEW":
        raise PreviewAwsError("DEPLOY_CONFIRMATION_REQUIRED")
    verify_identity(invoker.json("sts", "get-caller-identity"), expected_account_id)
    document = _object(json.loads(plan_path.read_text()), "deployment plan")
    unsigned = {key: value for key, value in document.items() if key != "plan_sha256"}
    if (
        document.get("plan_sha256") != plan_sha256
        or plan_sha256 != _canonical_hash(unsigned)
        or document.get("account_id") != expected_account_id
        or hashlib.sha256(template.read_bytes()).hexdigest() != document.get("template_sha256")
    ):
        raise PreviewAwsError("DEPLOYMENT_PLAN_INVALID")
    template_source = document.get("template_source")
    if not isinstance(template_source, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in template_source.items()
    ):
        raise PreviewAwsError("TEMPLATE_SOURCE_INVALID")
    if template_source.get("type") == "s3_version":
        if template_source.get("product_sha") != document.get("product_sha") or template_source.get(
            "sha256"
        ) != document.get("template_sha256"):
            raise PreviewAwsError("TEMPLATE_SOURCE_INVALID")
        _verify_large_template_reference(
            invoker,
            source={str(key): str(value) for key, value in template_source.items()},
            expected_size=template.stat().st_size,
        )
    elif template_source != {
        "type": "inline",
        "sha256": document.get("template_sha256"),
    }:
        raise PreviewAwsError("TEMPLATE_SOURCE_INVALID")
    arn = document.get("change_set_arn")
    parameters = document.get("parameters")
    if (
        not isinstance(arn, str)
        or not isinstance(parameters, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parameters.items()
        )
    ):
        raise PreviewAwsError("DEPLOYMENT_PLAN_BINDING_INVALID")
    expected_parameters = {str(key): str(value) for key, value in parameters.items()}
    image_digest = expected_parameters.get("ImageDigest", "")
    if image_digest:
        verify_image_binding(
            invoker,
            expected_account_id=expected_account_id,
            product_sha=expected_parameters.get("ProductSha", ""),
            image_digest=image_digest,
        )
    described = invoker.json("cloudformation", "describe-change-set", "--change-set-name", arn)
    changes = _validate_described_change_set(
        described,
        expected_account_id=expected_account_id,
        expected_arn=arn,
        expected_parameters=expected_parameters,
        expected_execution_status="AVAILABLE",
    )
    if changes != document.get("changes"):
        raise PreviewAwsError("CHANGE_SET_DRIFTED_AFTER_REVIEW")
    invoker.text("cloudformation", "execute-change-set", "--change-set-name", arn)
    waiter = (
        "stack-create-complete"
        if document.get("change_set_type") == "CREATE"
        else "stack-update-complete"
    )
    invoker.text("cloudformation", "wait", waiter, "--stack-name", STACK_NAME)
    stack = _stack_or_none(invoker, STACK_NAME)
    if stack is None:
        raise PreviewAwsError("DEPLOYED_STACK_MISSING")
    outputs = _outputs(stack)
    if (
        outputs.get("ProductSha") != document.get("product_sha")
        or outputs.get("ImageDigest") != document.get("image_digest")
        or outputs.get("RuntimeDesiredCount") != str(document.get("desired_count"))
    ):
        raise PreviewAwsError("DEPLOYED_OUTPUTS_MISMATCH")
    return outputs


def run_migration(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    image_digest: str,
    receipt_path: Path,
) -> dict[str, object]:
    verify_identity(invoker.json("sts", "get-caller-identity"), expected_account_id)
    verify_image_binding(
        invoker,
        expected_account_id=expected_account_id,
        product_sha=product_sha,
        image_digest=image_digest,
    )
    stack = _stack_or_none(invoker, STACK_NAME)
    if stack is None:
        raise PreviewAwsError("RUNTIME_STACK_MISSING")
    outputs = _outputs(stack)
    required = {
        "ProductSha": product_sha,
        "ImageDigest": image_digest,
        "RuntimeDesiredCount": "0",
    }
    if any(outputs.get(key) != value for key, value in required.items()):
        raise PreviewAwsError("MIGRATION_STACK_BINDING_INVALID")
    network = json.dumps(
        {
            "awsvpcConfiguration": {
                "subnets": outputs["PublicSubnetIds"].split(","),
                "securityGroups": [outputs["SchedulerTaskSecurityGroupId"]],
                "assignPublicIp": "ENABLED",
            }
        },
        separators=(",", ":"),
    )
    started = _object(
        invoker.json(
            "ecs",
            "run-task",
            "--cluster",
            outputs["ClusterName"],
            "--task-definition",
            outputs["MigrationTaskDefinitionArn"],
            "--launch-type",
            "FARGATE",
            "--platform-version",
            "1.4.0",
            "--network-configuration",
            network,
            "--count",
            "1",
        ),
        "migration task start",
    )
    if _array(started.get("failures"), "migration failures"):
        raise PreviewAwsError("MIGRATION_TASK_START_FAILED")
    tasks = _array(started.get("tasks"), "migration tasks")
    task = _object(tasks[0], "migration task") if len(tasks) == 1 else {}
    task_arn = task.get("taskArn")
    if not isinstance(task_arn, str):
        raise PreviewAwsError("MIGRATION_TASK_ARN_MISSING")
    invoker.text(
        "ecs", "wait", "tasks-stopped", "--cluster", outputs["ClusterName"], "--tasks", task_arn
    )
    described = _object(
        invoker.json(
            "ecs", "describe-tasks", "--cluster", outputs["ClusterName"], "--tasks", task_arn
        ),
        "migration task readback",
    )
    completed = _array(described.get("tasks"), "completed migration tasks")
    record = _object(completed[0], "completed migration task") if len(completed) == 1 else {}
    containers = _array(record.get("containers"), "migration containers")
    container = _object(containers[0], "migration container") if len(containers) == 1 else {}
    exit_code = container.get("exitCode")
    if exit_code != 0:
        raise PreviewAwsError(f"MIGRATION_FAILED:{exit_code}")
    receipt: dict[str, object] = {
        "schema_version": "1.0",
        "account_id": expected_account_id,
        "region": REGION,
        "stack_name": STACK_NAME,
        "product_sha": product_sha,
        "image_digest": image_digest,
        "task_arn": task_arn,
        "exit_code": 0,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    receipt["receipt_sha256"] = _canonical_hash(receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def render_runtime_config(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    output: Path,
    real_research: bool = False,
) -> dict[str, object]:
    verify_identity(invoker.json("sts", "get-caller-identity"), expected_account_id)
    runtime = _stack_or_none(invoker, STACK_NAME)
    public = _stack_or_none(invoker, PUBLIC_STACK_NAME)
    if runtime is None or public is None:
        raise PreviewAwsError("PREVIEW_STACKS_INCOMPLETE")
    runtime_outputs = _outputs(runtime)
    public_outputs = _outputs(public)
    if (
        runtime_outputs.get("ProductSha") != product_sha
        or public_outputs.get("ProductSha") != product_sha
        or runtime_outputs.get("RuntimeDesiredCount") != "1"
    ):
        raise PreviewAwsError("RUNTIME_CONFIG_SHA_OR_STATE_MISMATCH")
    issuer = runtime_outputs.get("CognitoIssuer")
    domain = runtime_outputs.get("CognitoDomainName")
    client_id = runtime_outputs.get("UserPoolClientId")
    if not issuer or not domain or not client_id:
        raise PreviewAwsError("RUNTIME_CONFIG_IDENTITY_OUTPUT_MISSING")
    config: dict[str, object] = {
        "schema_version": "1.0",
        "issuer": issuer,
        "authorization_endpoint": f"https://{domain}/oauth2/authorize",
        "token_endpoint": f"https://{domain}/oauth2/token",
        "end_session_endpoint": f"https://{domain}/logout",
        "client_id": client_id,
        "scope": "openid",
        "api_base_path": "/api/v2",
        "product_sha": product_sha,
        "environment_label": "私有合成 R2 预览",
        "synthetic_data": True,
    }
    if real_research:
        config.update(
            schema_version="1.1",
            environment_label="真实资料私人研究 · DEV_ONLY / UNVERIFIED",
            synthetic_data=False,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return config


def provision_owner(*, expected_account_id: str, user_pool_id: str, confirmation: str) -> None:
    if confirmation != "CREATE_OWNER":
        raise PreviewAwsError("OWNER_CONFIRMATION_REQUIRED")
    if not user_pool_id.startswith("ap-northeast-1_"):
        raise PreviewAwsError("USER_POOL_REGION_MISMATCH")
    import boto3

    client = boto3.client("cognito-idp", region_name=REGION)
    identity = boto3.client("sts", region_name=REGION).get_caller_identity()
    verify_identity(identity, expected_account_id)
    password = getpass.getpass("Temporary owner password: ")
    if len(password) < 14:
        raise PreviewAwsError("TEMPORARY_PASSWORD_TOO_SHORT")
    client.admin_create_user(
        UserPoolId=user_pool_id,
        Username="owner",
        TemporaryPassword=password,
        MessageAction="SUPPRESS",
    )


def disable_owner_mfa(*, expected_account_id: str, user_pool_id: str, confirmation: str) -> None:
    if confirmation != "DISABLE_OWNER_MFA":
        raise PreviewAwsError("OWNER_MFA_CONFIRMATION_REQUIRED")
    if not user_pool_id.startswith("ap-northeast-1_"):
        raise PreviewAwsError("USER_POOL_REGION_MISMATCH")
    import boto3

    identity = boto3.client("sts", region_name=REGION).get_caller_identity()
    verify_identity(identity, expected_account_id)
    boto3.client("cognito-idp", region_name=REGION).admin_set_user_mfa_preference(
        UserPoolId=user_pool_id,
        Username="owner",
        SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-account-id", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--product-sha", required=True)
    plan_parser.add_argument("--image-digest", default="")
    plan_parser.add_argument("--desired-count", type=int, required=True)
    plan_parser.add_argument("--public-base-url", required=True)
    plan_parser.add_argument("--cognito-domain-prefix", required=True)
    plan_parser.add_argument("--cloudfront-prefix-list-id", required=True)
    plan_parser.add_argument("--s3-prefix-list-id", required=True)
    plan_parser.add_argument("--deepseek-api-secret-arn", default="")
    plan_parser.add_argument("--tavily-api-secret-arn", default="")
    plan_parser.add_argument("--template", type=Path, default=TEMPLATE)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--migration-receipt", type=Path)
    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--template", type=Path, default=TEMPLATE)
    deploy_parser.add_argument("--plan", type=Path, required=True)
    deploy_parser.add_argument("--plan-sha256", required=True)
    deploy_parser.add_argument("--confirm", required=True)
    migration_parser = subparsers.add_parser("migrate")
    migration_parser.add_argument("--product-sha", required=True)
    migration_parser.add_argument("--image-digest", required=True)
    migration_parser.add_argument("--receipt", type=Path, required=True)
    config_parser = subparsers.add_parser("render-runtime-config")
    config_parser.add_argument("--product-sha", required=True)
    config_parser.add_argument("--output", type=Path, required=True)
    config_parser.add_argument("--real-research", action="store_true")
    owner_parser = subparsers.add_parser("provision-owner")
    owner_parser.add_argument("--user-pool-id", required=True)
    owner_parser.add_argument("--confirm", required=True)
    mfa_parser = subparsers.add_parser("disable-owner-mfa")
    mfa_parser.add_argument("--user-pool-id", required=True)
    mfa_parser.add_argument("--confirm", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result: object
        executable = locate_aws_cli()
        verify_cli_v2(executable)
        cli = AwsCli(executable)
        if args.command == "preflight":
            result = preflight(cli, expected_account_id=args.expected_account_id)
        elif args.command == "plan":
            parameters = _parameters(
                product_sha=args.product_sha,
                image_digest=args.image_digest,
                desired_count=args.desired_count,
                public_base_url=args.public_base_url,
                cognito_domain_prefix=args.cognito_domain_prefix,
                prefix_list_id=args.cloudfront_prefix_list_id,
                s3_prefix_list_id=args.s3_prefix_list_id,
                deepseek_api_secret_arn=args.deepseek_api_secret_arn,
                tavily_api_secret_arn=args.tavily_api_secret_arn,
            )
            result = plan(
                cli,
                expected_account_id=args.expected_account_id,
                template=args.template.resolve(),
                output=args.output.resolve(),
                parameters=parameters,
                migration_receipt=(
                    args.migration_receipt.resolve() if args.migration_receipt else None
                ),
            )
        elif args.command == "deploy":
            result = deploy(
                cli,
                expected_account_id=args.expected_account_id,
                template=args.template.resolve(),
                plan_path=args.plan.resolve(),
                plan_sha256=args.plan_sha256,
                confirmation=args.confirm,
            )
        elif args.command == "migrate":
            result = run_migration(
                cli,
                expected_account_id=args.expected_account_id,
                product_sha=args.product_sha,
                image_digest=args.image_digest,
                receipt_path=args.receipt.resolve(),
            )
        elif args.command == "render-runtime-config":
            result = render_runtime_config(
                cli,
                expected_account_id=args.expected_account_id,
                product_sha=args.product_sha,
                output=args.output.resolve(),
                real_research=args.real_research,
            )
        elif args.command == "provision-owner":
            provision_owner(
                expected_account_id=args.expected_account_id,
                user_pool_id=args.user_pool_id,
                confirmation=args.confirm,
            )
            result = {"owner": "created", "user_pool_id": args.user_pool_id}
        else:
            disable_owner_mfa(
                expected_account_id=args.expected_account_id,
                user_pool_id=args.user_pool_id,
                confirmation=args.confirm,
            )
            result = {"owner": "owner", "mfa": "disabled", "user_pool_id": args.user_pool_id}
        print(json.dumps(result, indent=2, sort_keys=True))
    except (
        PreviewAwsError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"R2 private preview blocked: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

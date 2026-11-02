"""Fail-closed AWS CLI v2 publisher for the StockMarketAgent public demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from typing import Final, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from yaml import YAMLError, compose, scan
from yaml.tokens import AliasToken, AnchorToken, ScalarToken

from scripts.public_demo_artifacts import (
    PublicArtifactError,
    build_public_demo,
    scan_public_tree,
    verify_public_pair,
)

REGION: Final = "ap-northeast-1"
STACK_NAME: Final = "stockmarketagent-public-demo-tokyo"
PROJECT_TAG: Final = "stockmarketagent-public-demo"
MANAGED_BY_TAG: Final = "StockMarketAgent"
LEGACY_MANAGED_BY_TAG: Final = "cloudformation"
PLAN_SCHEMA_VERSION: Final = "1.3"
# Project only verification metadata at the CLI boundary; never return origin secret values.
_DISTRIBUTION_VERIFICATION_QUERY: Final = """
{Distribution: Distribution.{Id: Id, ARN: ARN, Status: Status, DomainName: DomainName,
DistributionConfig: DistributionConfig.{Enabled: Enabled, HttpVersion: HttpVersion,
PriceClass: PriceClass, DefaultRootObject: DefaultRootObject, IsIPV6Enabled: IsIPV6Enabled,
Aliases: Aliases, Logging: Logging, WebACLId: WebACLId, ViewerCertificate: ViewerCertificate,
Origins: {Quantity: Origins.Quantity, Items: Origins.Items[].{Id: Id, DomainName: DomainName,
OriginAccessControlId: OriginAccessControlId, S3OriginConfig: S3OriginConfig,
CustomOriginConfig: CustomOriginConfig, CustomHeaders: {Quantity: CustomHeaders.Quantity,
Items: CustomHeaders.Items[].{HeaderName: HeaderName,
HeaderValuePresent: type(HeaderValue) == 'string' && length(HeaderValue) > `0`}}}},
DefaultCacheBehavior: DefaultCacheBehavior, CacheBehaviors: CacheBehaviors,
CustomErrorResponses: CustomErrorResponses}}}
""".strip()
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
ACCOUNT_PATTERN: Final = re.compile(r"^[0-9]{12}$")
HEX_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
API_ORIGIN_PATTERN: Final = re.compile(
    rf"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.{re.escape(REGION)}"
    r"\.elb\.amazonaws\.com$"
)
COGNITO_DOMAIN_PATTERN: Final = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.auth\.ap-northeast-1\.amazoncognito\.com$"
)
ORIGIN_SECRET_ARN_PATTERN: Final = re.compile(
    r"^arn:aws:secretsmanager:ap-northeast-1:([0-9]{12}):"
    r"secret:[A-Za-z0-9/_+=.@-]{1,512}$"
)
CLOUDFRONT_POLICY_ID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
STACK_NOT_FOUND_ERROR: Final = re.compile(
    r"^(?:aws: \[ERROR\]: )?An error occurred \(ValidationError\) when calling the "
    r"DescribeStacks operation"
    r"(?: \(reached max retries: [1-9][0-9]*\))?: "
    rf"Stack with id {re.escape(STACK_NAME)} does not exist$"
)
ALLOWED_RESOURCE_TYPES: Final = {
    "AWS::CloudFront::CachePolicy",
    "AWS::CloudFront::Distribution",
    "AWS::CloudFront::Function",
    "AWS::CloudFront::OriginAccessControl",
    "AWS::CloudFront::OriginRequestPolicy",
    "AWS::CloudFront::ResponseHeadersPolicy",
    "AWS::S3::Bucket",
    "AWS::S3::BucketPolicy",
}
ALLOWED_LOGICAL_IDS: Final = {
    "PublicDemoBucket",
    "PublicDemoBucketPolicy",
    "PublicDemoDistribution",
    "PublicDemoImmutableCachePolicy",
    "PublicDemoNoCachePolicy",
    "PublicDemoOriginAccessControl",
    "PublicApiOriginRequestPolicy",
    "PublicDemoRewriteFunction",
    "PublicDemoSecurityHeadersPolicy",
    "PublicDemoShortCachePolicy",
    "PrivatePreviewApiNoCachePolicy",
    "PrivatePreviewApiOriginRequestPolicy",
}
STACK_TAGS: Final = {
    "Project": PROJECT_TAG,
    "ManagedBy": MANAGED_BY_TAG,
}
DEFAULT_AWS_CLI_TIMEOUT_SECONDS: Final = 120
CLOUDFORMATION_WAIT_TIMEOUT_SECONDS: Final = 3600
CLOUDFRONT_WAIT_TIMEOUT_SECONDS: Final = 1800
SHELL_UPGRADE_MARKER_KEY: Final = "ops/live-shell-upgrade/state"
SHELL_UPGRADE_MARKER_PREFIX: Final = "ops/live-shell-upgrade/*"
SHELL_RELEASE_RECEIPT_PREFIX: Final = "ops/live-shell-upgrade/receipts"
SHELL_RELEASE_RECEIPT_SCHEMA: Final = "1.0"
SHELL_OBJECT_SERVER_SIDE_ENCRYPTION: Final = "AES256"
SHELL_IMMUTABLE_CACHE_CONTROL: Final = "public,max-age=31536000,immutable"
SHELL_MUTABLE_CACHE_CONTROL: Final = "public,max-age=300,must-revalidate"
SHELL_RELEASE_RECEIPT_CONTENT_TYPE: Final = "application/json"
SHELL_RELEASE_RECEIPT_CACHE_CONTROL: Final = "no-store,max-age=0"


class AwsSafetyError(ValueError):
    """The requested AWS action did not satisfy the deployment boundary."""


def locate_aws_cli() -> str:
    """Resolve an explicitly verified CLI before documented macOS locations."""

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
    raise AwsSafetyError("AWS CLI v2 was not found")


def assert_cloudformation_template_alias_free(document: bytes) -> None:
    """Reject YAML anchors, aliases, and merge keys before any AWS call."""

    try:
        text = document.decode("utf-8")
        tokens = scan(text)
        if any(
            isinstance(token, (AnchorToken, AliasToken))
            or (isinstance(token, ScalarToken) and token.value == "<<")
            for token in tokens
        ):
            raise ValueError("CLOUDFORMATION_YAML_ALIAS_FORBIDDEN")
        compose(text)
    except (UnicodeDecodeError, YAMLError) as error:
        raise ValueError("CLOUDFORMATION_TEMPLATE_YAML_INVALID") from error


class AwsInvoker(Protocol):
    def json(self, service: str, operation: str, *arguments: str) -> object: ...

    def text(self, service: str, operation: str, *arguments: str) -> str: ...


@dataclass(frozen=True)
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
        timeout = DEFAULT_AWS_CLI_TIMEOUT_SECONDS
        if (service, operation) == ("cloudformation", "wait"):
            timeout = CLOUDFORMATION_WAIT_TIMEOUT_SECONDS
        elif (service, operation) == ("cloudfront", "wait"):
            timeout = CLOUDFRONT_WAIT_TIMEOUT_SECONDS
        completed = subprocess.run(  # noqa: S603 - argv is built without a shell.
            self._command(service, operation, *arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout

    def json(self, service: str, operation: str, *arguments: str) -> object:
        output = self.text(service, operation, *arguments, "--output", "json")
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise AwsSafetyError(
                f"AWS CLI returned invalid JSON for {service} {operation}"
            ) from exc


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AwsSafetyError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AwsSafetyError(f"{label} must be an array")
    return value


def _sha(value: str) -> str:
    if SHA_PATTERN.fullmatch(value) is None:
        raise AwsSafetyError("product SHA must be a full lowercase commit")
    return value


def _origin_secret_arn(value: str, expected_account_id: str) -> str:
    match = ORIGIN_SECRET_ARN_PATTERN.fullmatch(value)
    if match is None or match.group(1) != expected_account_id:
        raise AwsSafetyError("API origin verification secret ARN is outside the fixed account")
    return value


def _private_preview_policy_ids(
    cache_policy_id: str,
    origin_request_policy_id: str,
    *,
    api_origin_domain: str,
) -> tuple[str, str]:
    if bool(cache_policy_id) != bool(origin_request_policy_id):
        raise AwsSafetyError("private preview policy IDs must be configured together")
    if cache_policy_id and not api_origin_domain:
        raise AwsSafetyError("private preview policy IDs require the R2 API origin")
    for value in (cache_policy_id, origin_request_policy_id):
        if value and CLOUDFRONT_POLICY_ID_PATTERN.fullmatch(value) is None:
            raise AwsSafetyError("private preview policy ID is invalid")
    return cache_policy_id, origin_request_policy_id


def verify_cli_v2(output: str) -> None:
    if not output.startswith("aws-cli/2."):
        raise AwsSafetyError("AWS CLI v2 is required")


def verify_identity(payload: object, expected_account_id: str) -> dict[str, object]:
    if ACCOUNT_PATTERN.fullmatch(expected_account_id) is None:
        raise AwsSafetyError("EXPECTED_AWS_ACCOUNT_ID must contain exactly 12 digits")
    identity = _object(payload, "STS identity")
    if identity.get("Account") != expected_account_id:
        raise AwsSafetyError("AWS account does not match EXPECTED_AWS_ACCOUNT_ID")
    arn = identity.get("Arn")
    if not isinstance(arn, str) or (
        not arn.startswith("arn:aws:iam::") and not arn.startswith("arn:aws:sts::")
    ):
        raise AwsSafetyError("STS identity ARN is not an AWS IAM/STS principal")
    return identity


def _tags(value: object) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in _array(value, "stack tags"):
        record = _object(item, "stack tag")
        key, tag_value = record.get("Key"), record.get("Value")
        if not isinstance(key, str) or not isinstance(tag_value, str) or key in tags:
            raise AwsSafetyError("stack tags are malformed")
        tags[key] = tag_value
    return tags


def _tag_mapping(value: object, label: str) -> dict[str, str]:
    record = _object(value, label)
    tags: dict[str, str] = {}
    for key, tag_value in record.items():
        if not isinstance(key, str) or not isinstance(tag_value, str):
            raise AwsSafetyError(f"{label} are malformed")
        tags[key] = tag_value
    return tags


def _stack_tag_profile(tags: dict[str, str]) -> str:
    if tags == STACK_TAGS:
        return "canonical"
    # The legacy stack-level ProductSha is retained only to avoid a broad tag migration.
    # Deployment authority remains the stack output, resource tags and artifact metadata.
    if (
        set(tags) == {"Project", "ManagedBy", "ProductSha"}
        and tags.get("Project") == PROJECT_TAG
        and tags.get("ManagedBy") == LEGACY_MANAGED_BY_TAG
        and SHA_PATTERN.fullmatch(tags.get("ProductSha", "")) is not None
    ):
        return "legacy"
    raise AwsSafetyError("stack tags do not exactly match a supported project profile")


def _selected_stack_tags(existing: dict[str, object] | None) -> dict[str, str]:
    if existing is None:
        return dict(STACK_TAGS)
    existing_tags = _tags(existing.get("Tags"))
    profile = _stack_tag_profile(existing_tags)
    if profile == "canonical":
        return dict(STACK_TAGS)
    return existing_tags


def verify_existing_stack(payload: object) -> dict[str, object]:
    stacks = _array(_object(payload, "describe-stacks").get("Stacks"), "Stacks")
    if len(stacks) != 1:
        raise AwsSafetyError("expected exactly one project stack")
    stack = _object(stacks[0], "stack")
    if stack.get("StackName") != STACK_NAME:
        raise AwsSafetyError("described stack name is outside the project boundary")
    _stack_tag_profile(_tags(stack.get("Tags")))
    status = stack.get("StackStatus")
    if status not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}:
        raise AwsSafetyError(f"stack is not safe to update: {status}")
    return stack


def normalize_changes(payload: object) -> list[dict[str, str]]:
    change_set = _object(payload, "change set")
    if change_set.get("Status") != "CREATE_COMPLETE":
        raise AwsSafetyError(f"change set is not ready: {change_set.get('Status')}")
    normalized: list[dict[str, str]] = []
    for change_value in _array(change_set.get("Changes"), "change set changes"):
        change = _object(change_value, "change")
        resource = _object(change.get("ResourceChange"), "resource change")
        action = resource.get("Action")
        logical_id = resource.get("LogicalResourceId")
        resource_type = resource.get("ResourceType")
        replacement = resource.get("Replacement", "False")
        if action not in {"Add", "Modify"}:
            raise AwsSafetyError(f"destructive or unknown change action: {action}")
        if replacement not in {"False", False, None}:
            raise AwsSafetyError(f"resource replacement is forbidden: {logical_id}")
        if logical_id not in ALLOWED_LOGICAL_IDS or resource_type not in ALLOWED_RESOURCE_TYPES:
            raise AwsSafetyError(f"change set contains an unknown project resource: {logical_id}")
        normalized.append(
            {
                "action": action,
                "logical_id": logical_id,
                "resource_type": resource_type,
                "replacement": "False",
            }
        )
    return sorted(normalized, key=lambda item: (item["logical_id"], item["action"]))


def verify_change_set(
    payload: object,
    *,
    expected_arn: str,
    expected_account_id: str,
    expected_product_sha: str,
    expected_change_set_type: str,
    expected_execution_status: str,
    expected_stack_tags: dict[str, str],
    expected_api_origin_domain: str = "",
    expected_api_origin_secret_arn: str = "",
    expected_cognito_domain: str = "",
    expected_api_no_cache_policy_id: str = "",
    expected_api_origin_request_policy_id: str = "",
) -> list[dict[str, str]]:
    """Bind one described change set to its reviewed account, stack and parameter."""

    change_set = _object(payload, "change set")
    expected_prefix = f"arn:aws:cloudformation:{REGION}:{expected_account_id}:changeSet/"
    if (
        not expected_arn.startswith(expected_prefix)
        or change_set.get("ChangeSetId") != expected_arn
    ):
        raise AwsSafetyError("described change set ARN differs from the reviewed ARN")
    if change_set.get("StackName") != STACK_NAME:
        raise AwsSafetyError("described change set stack name is outside the project boundary")
    if (
        "ChangeSetType" in change_set
        and change_set.get("ChangeSetType") != expected_change_set_type
    ):
        raise AwsSafetyError("described change set type differs from the reviewed plan")
    _stack_tag_profile(expected_stack_tags)
    if _tags(change_set.get("Tags")) != expected_stack_tags:
        raise AwsSafetyError("described change set tags differ from the reviewed plan")
    if change_set.get("Status") != "CREATE_COMPLETE":
        raise AwsSafetyError(f"change set is not ready: {change_set.get('Status')}")
    if change_set.get("ExecutionStatus") != expected_execution_status:
        raise AwsSafetyError("change set execution status differs from the required phase")
    parameters = _array(change_set.get("Parameters"), "change set parameters")
    actual_parameters: dict[str, str] = {}
    allowed_parameter_keys = {"ParameterKey", "ParameterValue", "ResolvedValue"}
    for raw_parameter in parameters:
        parameter = _object(raw_parameter, "change set parameter")
        key = parameter.get("ParameterKey")
        value = parameter.get("ParameterValue")
        if (
            set(parameter) - allowed_parameter_keys
            or not isinstance(key, str)
            or not isinstance(value, str)
            or key in actual_parameters
        ):
            raise AwsSafetyError("change set parameter binding is invalid")
        actual_parameters[key] = value
    expected_parameters = {"ProductSha": _sha(expected_product_sha)}
    if expected_api_origin_domain:
        if API_ORIGIN_PATTERN.fullmatch(expected_api_origin_domain) is None:
            raise AwsSafetyError("API origin domain is not an AWS load balancer")
        expected_parameters["ApiOriginVerifySecretArn"] = _origin_secret_arn(
            expected_api_origin_secret_arn,
            expected_account_id,
        )
        expected_parameters["ApiOriginDomainName"] = expected_api_origin_domain
    elif actual_parameters.get("ApiOriginDomainName") == "":
        actual_parameters.pop("ApiOriginDomainName")
        if actual_parameters.get("ApiOriginVerifySecretArn") == "":
            actual_parameters.pop("ApiOriginVerifySecretArn")
    if bool(expected_api_origin_domain) != bool(expected_api_origin_secret_arn):
        raise AwsSafetyError("API origin and verification secret must be configured together")
    _private_preview_policy_ids(
        expected_api_no_cache_policy_id,
        expected_api_origin_request_policy_id,
        api_origin_domain=expected_api_origin_domain,
    )
    if expected_api_no_cache_policy_id:
        expected_parameters["PrivatePreviewApiNoCachePolicyId"] = expected_api_no_cache_policy_id
        expected_parameters["PrivatePreviewApiOriginRequestPolicyId"] = (
            expected_api_origin_request_policy_id
        )
    else:
        for key in (
            "PrivatePreviewApiNoCachePolicyId",
            "PrivatePreviewApiOriginRequestPolicyId",
        ):
            if actual_parameters.get(key) == "":
                actual_parameters.pop(key)
    if expected_cognito_domain:
        if COGNITO_DOMAIN_PATTERN.fullmatch(expected_cognito_domain) is None:
            raise AwsSafetyError("Cognito domain is outside the fixed Tokyo service domain")
        expected_parameters["CognitoDomainName"] = expected_cognito_domain
    elif actual_parameters.get("CognitoDomainName") == "":
        actual_parameters.pop("CognitoDomainName")
    if actual_parameters != expected_parameters:
        raise AwsSafetyError("change set ProductSha/origin parameters differ from the release")
    return normalize_changes(change_set)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def create_plan_document(
    *,
    change_set_arn: str,
    product_sha: str,
    expected_account_id: str,
    template_sha256: str,
    changes: list[dict[str, str]],
    change_set_type: str = "UPDATE",
    stack_tags: dict[str, str] | None = None,
    api_origin_domain: str = "",
    api_origin_secret_arn: str = "",
    cognito_domain: str = "",
    api_no_cache_policy_id: str = "",
    api_origin_request_policy_id: str = "",
) -> dict[str, object]:
    if ACCOUNT_PATTERN.fullmatch(expected_account_id) is None:
        raise AwsSafetyError("plan account must contain exactly 12 digits")
    if HEX_SHA256_PATTERN.fullmatch(template_sha256) is None:
        raise AwsSafetyError("template SHA256 must be a full lowercase digest")
    if not change_set_arn.startswith(
        f"arn:aws:cloudformation:{REGION}:{expected_account_id}:changeSet/"
    ):
        raise AwsSafetyError("change set ARN is outside the fixed account/region")
    if change_set_type not in {"CREATE", "UPDATE"}:
        raise AwsSafetyError("change set type must be CREATE or UPDATE")
    if api_origin_domain and API_ORIGIN_PATTERN.fullmatch(api_origin_domain) is None:
        raise AwsSafetyError("API origin domain is outside the fixed Tokyo region")
    if bool(api_origin_domain) != bool(api_origin_secret_arn):
        raise AwsSafetyError("API origin and verification secret must be configured together")
    if api_origin_secret_arn:
        _origin_secret_arn(api_origin_secret_arn, expected_account_id)
    if cognito_domain and COGNITO_DOMAIN_PATTERN.fullmatch(cognito_domain) is None:
        raise AwsSafetyError("Cognito domain is outside the fixed Tokyo service domain")
    _private_preview_policy_ids(
        api_no_cache_policy_id,
        api_origin_request_policy_id,
        api_origin_domain=api_origin_domain,
    )
    selected_tags = dict(STACK_TAGS) if stack_tags is None else dict(stack_tags)
    tag_profile = _stack_tag_profile(selected_tags)
    document: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "region": REGION,
        "stack_name": STACK_NAME,
        "project": PROJECT_TAG,
        "expected_account_id": expected_account_id,
        "product_sha": _sha(product_sha),
        "template_sha256": template_sha256,
        "change_set_arn": change_set_arn,
        "change_set_type": change_set_type,
        "stack_tag_profile": tag_profile,
        "stack_tags": selected_tags,
        "changes": changes,
        "api_origin_domain": api_origin_domain,
        "api_origin_secret_arn": api_origin_secret_arn,
        "cognito_domain": cognito_domain,
        "api_no_cache_policy_id": api_no_cache_policy_id,
        "api_origin_request_policy_id": api_origin_request_policy_id,
    }
    document["plan_sha256"] = _canonical_hash(document)
    return document


def verify_plan_document(plan: object) -> dict[str, object]:
    document = _object(plan, "plan")
    plan_hash = document.get("plan_sha256")
    unsigned = {key: value for key, value in document.items() if key != "plan_sha256"}
    if not isinstance(plan_hash, str) or plan_hash != _canonical_hash(unsigned):
        raise AwsSafetyError("plan hash does not match its immutable payload")
    if document.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise AwsSafetyError("plan schema version is unsupported")
    if document.get("region") != REGION or document.get("stack_name") != STACK_NAME:
        raise AwsSafetyError("plan is outside the fixed stack boundary")
    if document.get("project") != PROJECT_TAG:
        raise AwsSafetyError("plan project tag is invalid")
    account = document.get("expected_account_id")
    arn = document.get("change_set_arn")
    if not isinstance(account, str) or ACCOUNT_PATTERN.fullmatch(account) is None:
        raise AwsSafetyError("plan account is invalid")
    if not isinstance(arn, str) or not arn.startswith(
        f"arn:aws:cloudformation:{REGION}:{account}:changeSet/"
    ):
        raise AwsSafetyError("plan change set ARN is outside its account/region")
    product_sha = document.get("product_sha")
    template_sha = document.get("template_sha256")
    if not isinstance(product_sha, str) or SHA_PATTERN.fullmatch(product_sha) is None:
        raise AwsSafetyError("plan product SHA is invalid")
    if not isinstance(template_sha, str) or HEX_SHA256_PATTERN.fullmatch(template_sha) is None:
        raise AwsSafetyError("plan template SHA256 is invalid")
    if document.get("change_set_type") not in {"CREATE", "UPDATE"}:
        raise AwsSafetyError("plan change set type is invalid")
    api_origin = document.get("api_origin_domain", "")
    if not isinstance(api_origin, str) or (
        api_origin and API_ORIGIN_PATTERN.fullmatch(api_origin) is None
    ):
        raise AwsSafetyError("plan API origin domain is invalid")
    api_origin_secret_arn = document.get("api_origin_secret_arn", "")
    if not isinstance(api_origin_secret_arn, str) or bool(api_origin) != bool(
        api_origin_secret_arn
    ):
        raise AwsSafetyError("plan API origin verification binding is invalid")
    if api_origin_secret_arn:
        _origin_secret_arn(api_origin_secret_arn, account)
    cognito_domain = document.get("cognito_domain", "")
    if not isinstance(cognito_domain, str) or (
        cognito_domain and COGNITO_DOMAIN_PATTERN.fullmatch(cognito_domain) is None
    ):
        raise AwsSafetyError("plan Cognito domain is invalid")
    api_no_cache_policy_id = document.get("api_no_cache_policy_id", "")
    api_origin_request_policy_id = document.get("api_origin_request_policy_id", "")
    if not isinstance(api_no_cache_policy_id, str) or not isinstance(
        api_origin_request_policy_id, str
    ):
        raise AwsSafetyError("plan private preview policy IDs are invalid")
    _private_preview_policy_ids(
        api_no_cache_policy_id,
        api_origin_request_policy_id,
        api_origin_domain=api_origin,
    )
    stack_tags = _tag_mapping(document.get("stack_tags"), "plan stack tags")
    if document.get("stack_tag_profile") != _stack_tag_profile(stack_tags):
        raise AwsSafetyError("plan stack tag profile does not match its exact tags")
    return document


def _identity(invoker: AwsInvoker, expected_account_id: str) -> dict[str, object]:
    return verify_identity(
        invoker.json("sts", "get-caller-identity"),
        expected_account_id,
    )


def _describe_stack(invoker: AwsInvoker) -> dict[str, object] | None:
    try:
        return verify_existing_stack(
            invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if STACK_NOT_FOUND_ERROR.fullmatch(stderr) is not None:
            return None
        raise


def _wait_change_set(
    invoker: AwsInvoker,
    arn: str,
    *,
    expected_account_id: str,
    expected_product_sha: str,
    expected_change_set_type: str,
    expected_stack_tags: dict[str, str],
    expected_api_origin_domain: str = "",
    expected_api_origin_secret_arn: str = "",
    expected_cognito_domain: str = "",
    expected_api_no_cache_policy_id: str = "",
    expected_api_origin_request_policy_id: str = "",
) -> dict[str, object]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        payload = _object(
            invoker.json("cloudformation", "describe-change-set", "--change-set-name", arn),
            "change set",
        )
        if payload.get("Status") == "FAILED":
            raise AwsSafetyError(f"change set planning failed: {payload.get('StatusReason')}")
        if payload.get("Status") == "CREATE_COMPLETE":
            verify_change_set(
                payload,
                expected_arn=arn,
                expected_account_id=expected_account_id,
                expected_product_sha=expected_product_sha,
                expected_change_set_type=expected_change_set_type,
                expected_execution_status="AVAILABLE",
                expected_stack_tags=expected_stack_tags,
                expected_api_origin_domain=expected_api_origin_domain,
                expected_api_origin_secret_arn=expected_api_origin_secret_arn,
                expected_cognito_domain=expected_cognito_domain,
                expected_api_no_cache_policy_id=expected_api_no_cache_policy_id,
                expected_api_origin_request_policy_id=(expected_api_origin_request_policy_id),
            )
            return payload
        time.sleep(2)
    raise AwsSafetyError("change set planning timed out")


def plan(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    template: Path,
    output: Path,
    api_origin_domain: str = "",
    api_origin_secret_arn: str = "",
    cognito_domain: str = "",
    api_no_cache_policy_id: str = "",
    api_origin_request_policy_id: str = "",
) -> dict[str, object]:
    if template.resolve() != template or not template.is_file():
        raise AwsSafetyError("template path must be a canonical file")
    template_bytes = template.read_bytes()
    try:
        assert_cloudformation_template_alias_free(template_bytes)
    except ValueError as error:
        raise AwsSafetyError(str(error)) from error
    template_sha = hashlib.sha256(template_bytes).hexdigest()
    _identity(invoker, expected_account_id)
    invoker.json(
        "cloudformation",
        "validate-template",
        "--template-body",
        f"file://{template}",
    )
    existing = _describe_stack(invoker)
    change_type = "UPDATE" if existing is not None else "CREATE"
    stack_tags = _selected_stack_tags(existing)
    if api_origin_domain and API_ORIGIN_PATTERN.fullmatch(api_origin_domain) is None:
        raise AwsSafetyError("API origin domain is not an AWS load balancer")
    if bool(api_origin_domain) != bool(api_origin_secret_arn):
        raise AwsSafetyError("API origin and verification secret must be configured together")
    if api_origin_secret_arn:
        _origin_secret_arn(api_origin_secret_arn, expected_account_id)
    if cognito_domain and COGNITO_DOMAIN_PATTERN.fullmatch(cognito_domain) is None:
        raise AwsSafetyError("Cognito domain is outside the fixed Tokyo service domain")
    _private_preview_policy_ids(
        api_no_cache_policy_id,
        api_origin_request_policy_id,
        api_origin_domain=api_origin_domain,
    )
    _verify_external_private_preview_policies(
        invoker,
        cache_policy_id=api_no_cache_policy_id,
        origin_request_policy_id=api_origin_request_policy_id,
    )
    change_name = f"public-demo-{_sha(product_sha)[:12]}"
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
            "--template-body",
            f"file://{template}",
            "--parameters",
            f"ParameterKey=ProductSha,ParameterValue={product_sha}",
            *(
                (f"ParameterKey=ApiOriginDomainName,ParameterValue={api_origin_domain}",)
                if api_origin_domain
                else ()
            ),
            *(
                (f"ParameterKey=ApiOriginVerifySecretArn,ParameterValue={api_origin_secret_arn}",)
                if api_origin_secret_arn
                else ()
            ),
            *(
                (f"ParameterKey=CognitoDomainName,ParameterValue={cognito_domain}",)
                if cognito_domain
                else ()
            ),
            *(
                (
                    "ParameterKey=PrivatePreviewApiNoCachePolicyId,"
                    f"ParameterValue={api_no_cache_policy_id}",
                )
                if api_no_cache_policy_id
                else ()
            ),
            *(
                (
                    "ParameterKey=PrivatePreviewApiOriginRequestPolicyId,"
                    f"ParameterValue={api_origin_request_policy_id}",
                )
                if api_origin_request_policy_id
                else ()
            ),
            "--tags",
            *(f"Key={key},Value={value}" for key, value in stack_tags.items()),
        ),
        "create change set",
    )
    arn = created.get("Id")
    if not isinstance(arn, str):
        raise AwsSafetyError("create-change-set omitted its ARN")
    described = _wait_change_set(
        invoker,
        arn,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
        expected_change_set_type=change_type,
        expected_stack_tags=stack_tags,
        expected_api_origin_domain=api_origin_domain,
        expected_api_origin_secret_arn=api_origin_secret_arn,
        expected_cognito_domain=cognito_domain,
        expected_api_no_cache_policy_id=api_no_cache_policy_id,
        expected_api_origin_request_policy_id=api_origin_request_policy_id,
    )
    changes = verify_change_set(
        described,
        expected_arn=arn,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
        expected_change_set_type=change_type,
        expected_execution_status="AVAILABLE",
        expected_stack_tags=stack_tags,
        expected_api_origin_domain=api_origin_domain,
        expected_api_origin_secret_arn=api_origin_secret_arn,
        expected_cognito_domain=cognito_domain,
        expected_api_no_cache_policy_id=api_no_cache_policy_id,
        expected_api_origin_request_policy_id=api_origin_request_policy_id,
    )
    document = create_plan_document(
        change_set_arn=arn,
        product_sha=product_sha,
        expected_account_id=expected_account_id,
        template_sha256=template_sha,
        changes=changes,
        change_set_type=change_type,
        stack_tags=stack_tags,
        api_origin_domain=api_origin_domain,
        api_origin_secret_arn=api_origin_secret_arn,
        cognito_domain=cognito_domain,
        api_no_cache_policy_id=api_no_cache_policy_id,
        api_origin_request_policy_id=api_origin_request_policy_id,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def deploy(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    plan_path: Path,
    change_set_arn: str,
    plan_sha256: str,
    confirmation: str,
    template: Path,
) -> None:
    if confirmation != "DEPLOY":
        raise AwsSafetyError("deploy requires the exact DEPLOY confirmation")
    _identity(invoker, expected_account_id)
    plan_document = verify_plan_document(json.loads(plan_path.read_text(encoding="utf-8")))
    if plan_document.get("expected_account_id") != expected_account_id:
        raise AwsSafetyError("reviewed plan account differs from current identity")
    if plan_document.get("change_set_arn") != change_set_arn:
        raise AwsSafetyError("change set ARN differs from the reviewed plan")
    if plan_document.get("plan_sha256") != plan_sha256:
        raise AwsSafetyError("provided plan hash differs from the reviewed plan")
    if hashlib.sha256(template.read_bytes()).hexdigest() != plan_document.get("template_sha256"):
        raise AwsSafetyError("template changed after planning")
    described = invoker.json(
        "cloudformation", "describe-change-set", "--change-set-name", change_set_arn
    )
    product_sha = str(plan_document["product_sha"])
    change_set_type = str(plan_document["change_set_type"])
    stack_tags = _tag_mapping(plan_document.get("stack_tags"), "plan stack tags")
    api_origin_domain = str(plan_document.get("api_origin_domain", ""))
    api_origin_secret_arn = str(plan_document.get("api_origin_secret_arn", ""))
    cognito_domain = str(plan_document.get("cognito_domain", ""))
    api_no_cache_policy_id = str(plan_document.get("api_no_cache_policy_id", ""))
    api_origin_request_policy_id = str(plan_document.get("api_origin_request_policy_id", ""))
    described_changes = verify_change_set(
        described,
        expected_arn=change_set_arn,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
        expected_change_set_type=change_set_type,
        expected_execution_status="AVAILABLE",
        expected_stack_tags=stack_tags,
        expected_api_origin_domain=api_origin_domain,
        expected_api_origin_secret_arn=api_origin_secret_arn,
        expected_cognito_domain=cognito_domain,
        expected_api_no_cache_policy_id=api_no_cache_policy_id,
        expected_api_origin_request_policy_id=api_origin_request_policy_id,
    )
    if described_changes != plan_document.get("changes"):
        raise AwsSafetyError("change set changed after planning")
    _verify_external_private_preview_policies(
        invoker,
        cache_policy_id=api_no_cache_policy_id,
        origin_request_policy_id=api_origin_request_policy_id,
    )
    if change_set_type == "UPDATE":
        current_stack = verify_existing_stack(
            invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
        )
        if _tags(current_stack.get("Tags")) != stack_tags:
            raise AwsSafetyError("current stack tags differ from the reviewed plan")
    invoker.text("cloudformation", "execute-change-set", "--change-set-name", change_set_arn)
    waiter = (
        "stack-create-complete"
        if plan_document.get("change_set_type") == "CREATE"
        else "stack-update-complete"
    )
    invoker.text("cloudformation", "wait", waiter, "--stack-name", STACK_NAME)
    after = invoker.json(
        "cloudformation", "describe-change-set", "--change-set-name", change_set_arn
    )
    after_changes = verify_change_set(
        after,
        expected_arn=change_set_arn,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
        expected_change_set_type=change_set_type,
        expected_execution_status="EXECUTE_COMPLETE",
        expected_stack_tags=stack_tags,
        expected_api_origin_domain=api_origin_domain,
        expected_api_origin_secret_arn=api_origin_secret_arn,
        expected_cognito_domain=cognito_domain,
        expected_api_no_cache_policy_id=api_no_cache_policy_id,
        expected_api_origin_request_policy_id=api_origin_request_policy_id,
    )
    if after_changes != described_changes:
        raise AwsSafetyError("executed change set no longer matches the reviewed diff")
    stack = verify_existing_stack(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
    )
    if _tags(stack.get("Tags")) != stack_tags:
        raise AwsSafetyError("deployed stack tags differ from the reviewed plan")
    _verify_deployed_resources(
        invoker,
        stack,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
        expected_api_no_cache_policy_id=api_no_cache_policy_id,
        expected_api_origin_request_policy_id=api_origin_request_policy_id,
    )


def _outputs(stack: dict[str, object]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for item_value in _array(stack.get("Outputs"), "stack outputs"):
        item = _object(item_value, "stack output")
        key, value = item.get("OutputKey"), item.get("OutputValue")
        if not isinstance(key, str) or not isinstance(value, str) or key in outputs:
            raise AwsSafetyError("stack outputs are malformed")
        outputs[key] = value
    required = {
        "BucketName",
        "DistributionDomainName",
        "DistributionId",
        "PublicDemoUrl",
        "ProductSha",
    }
    if set(outputs) != required:
        raise AwsSafetyError("stack outputs do not exactly match the public-demo interface")
    return outputs


def _deployed_api_origin_domain(
    stack: dict[str, object],
    *,
    expected_account_id: str,
    expected_product_sha: str,
) -> str:
    parameters = _array(stack.get("Parameters"), "stack parameters")
    actual: dict[str, str] = {}
    allowed_parameter_keys = {"ParameterKey", "ParameterValue", "ResolvedValue"}
    for raw_parameter in parameters:
        parameter = _object(raw_parameter, "stack parameter")
        key = parameter.get("ParameterKey")
        value = parameter.get("ParameterValue")
        if (
            set(parameter) - allowed_parameter_keys
            or not isinstance(key, str)
            or not isinstance(value, str)
            or key in actual
        ):
            raise AwsSafetyError("deployed stack parameter binding is invalid")
        actual[key] = value
    api_origin_domain = actual.get("ApiOriginDomainName", "")
    if api_origin_domain == "":
        actual.pop("ApiOriginDomainName", None)
    elif API_ORIGIN_PATTERN.fullmatch(api_origin_domain) is None:
        raise AwsSafetyError("deployed API origin domain is not an AWS load balancer")
    api_origin_secret_arn = actual.get("ApiOriginVerifySecretArn", "")
    if api_origin_secret_arn == "":
        actual.pop("ApiOriginVerifySecretArn", None)
    else:
        _origin_secret_arn(api_origin_secret_arn, expected_account_id)
    if bool(api_origin_domain) != bool(api_origin_secret_arn):
        raise AwsSafetyError("deployed API origin verification binding is incomplete")
    cognito_domain = actual.get("CognitoDomainName", "")
    if cognito_domain == "":
        actual.pop("CognitoDomainName", None)
    elif COGNITO_DOMAIN_PATTERN.fullmatch(cognito_domain) is None:
        raise AwsSafetyError("deployed Cognito domain is outside the fixed Tokyo service domain")
    api_no_cache_policy_id = actual.get("PrivatePreviewApiNoCachePolicyId", "")
    api_origin_request_policy_id = actual.get("PrivatePreviewApiOriginRequestPolicyId", "")
    if api_no_cache_policy_id == "":
        actual.pop("PrivatePreviewApiNoCachePolicyId", None)
    if api_origin_request_policy_id == "":
        actual.pop("PrivatePreviewApiOriginRequestPolicyId", None)
    _private_preview_policy_ids(
        api_no_cache_policy_id,
        api_origin_request_policy_id,
        api_origin_domain=api_origin_domain,
    )
    expected = {"ProductSha": _sha(expected_product_sha)}
    if api_origin_domain:
        expected["ApiOriginDomainName"] = api_origin_domain
        expected["ApiOriginVerifySecretArn"] = api_origin_secret_arn
    if cognito_domain:
        expected["CognitoDomainName"] = cognito_domain
    if api_no_cache_policy_id:
        expected["PrivatePreviewApiNoCachePolicyId"] = api_no_cache_policy_id
        expected["PrivatePreviewApiOriginRequestPolicyId"] = api_origin_request_policy_id
    if actual != expected:
        raise AwsSafetyError("deployed stack ProductSha/origin parameters differ")
    return api_origin_domain


def _verify_bucket(invoker: AwsInvoker, bucket: str) -> None:
    encryption = _object(
        invoker.json("s3api", "get-bucket-encryption", "--bucket", bucket), "encryption"
    )
    rules = _array(
        _object(encryption.get("ServerSideEncryptionConfiguration"), "encryption config").get(
            "Rules"
        ),
        "encryption rules",
    )
    first = _object(rules[0], "encryption rule") if len(rules) == 1 else {}
    default = _object(first.get("ApplyServerSideEncryptionByDefault"), "default encryption")
    if default.get("SSEAlgorithm") != "AES256":
        raise AwsSafetyError("bucket does not use AES256 default encryption")
    versioning = _object(
        invoker.json("s3api", "get-bucket-versioning", "--bucket", bucket), "versioning"
    )
    if versioning.get("Status") != "Enabled":
        raise AwsSafetyError("bucket versioning is not enabled")
    public = _object(
        _object(
            invoker.json("s3api", "get-public-access-block", "--bucket", bucket),
            "public access",
        ).get("PublicAccessBlockConfiguration"),
        "public access config",
    )
    if public != {
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    }:
        raise AwsSafetyError("bucket public access block is incomplete")
    ownership = _object(
        invoker.json("s3api", "get-bucket-ownership-controls", "--bucket", bucket),
        "ownership",
    )
    if ownership.get("OwnershipControls") != {
        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
    }:
        raise AwsSafetyError("bucket ownership is not BucketOwnerEnforced")


def _quantity_items(value: object, label: str) -> list[object]:
    collection = _object(value, label)
    items = _array(collection.get("Items", []), f"{label} items")
    if collection.get("Quantity") != len(items):
        raise AwsSafetyError(f"{label} quantity differs from its items")
    return items


def _verify_cache_behavior_transport(
    behavior: dict[str, object],
    *,
    label: str,
) -> None:
    allowed = _object(behavior.get("AllowedMethods"), f"{label} allowed methods")
    allowed_items = _quantity_items(allowed, f"{label} allowed methods")
    cached_items = _quantity_items(
        allowed.get("CachedMethods"),
        f"{label} cached methods",
    )
    allowed_names = [item for item in allowed_items if isinstance(item, str)]
    cached_names = [item for item in cached_items if isinstance(item, str)]
    lambda_functions = _quantity_items(
        behavior.get("LambdaFunctionAssociations"),
        f"{label} Lambda associations",
    )
    if (
        len(allowed_names) != len(allowed_items)
        or sorted(allowed_names) != ["GET", "HEAD"]
        or len(cached_names) != len(cached_items)
        or sorted(cached_names) != ["GET", "HEAD"]
        or behavior.get("Compress") is not True
        or lambda_functions
    ):
        raise AwsSafetyError(
            f"{label} methods, compression, or Lambda associations differ from the locked template"
        )


def _verify_api_cache_behavior_transport(behavior: dict[str, object]) -> None:
    allowed = _object(behavior.get("AllowedMethods"), "API cache behavior allowed methods")
    allowed_items = _quantity_items(allowed, "API cache behavior allowed methods")
    cached_items = _quantity_items(
        allowed.get("CachedMethods"),
        "API cache behavior cached methods",
    )
    allowed_names = [item for item in allowed_items if isinstance(item, str)]
    cached_names = [item for item in cached_items if isinstance(item, str)]
    lambda_functions = _quantity_items(
        behavior.get("LambdaFunctionAssociations"),
        "API cache behavior Lambda associations",
    )
    if (
        len(allowed_names) != len(allowed_items)
        or sorted(allowed_names) != ["GET", "HEAD", "OPTIONS"]
        or len(cached_names) != len(cached_items)
        or sorted(cached_names) != ["GET", "HEAD"]
        or behavior.get("Compress") is not False
        or lambda_functions
    ):
        raise AwsSafetyError(
            "API cache behavior methods, compression, or Lambda associations differ "
            "from the locked template"
        )


def _verify_r2_api_cache_behavior_transport(behavior: dict[str, object]) -> None:
    allowed = _object(behavior.get("AllowedMethods"), "R2 API allowed methods")
    allowed_items = _quantity_items(allowed, "R2 API allowed methods")
    cached_items = _quantity_items(allowed.get("CachedMethods"), "R2 API cached methods")
    if (
        sorted(item for item in allowed_items if isinstance(item, str))
        != ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
        or sorted(item for item in cached_items if isinstance(item, str))
        != ["GET", "HEAD", "OPTIONS"]
        or behavior.get("Compress") is not False
        or _quantity_items(behavior.get("LambdaFunctionAssociations"), "R2 API Lambda associations")
    ):
        raise AwsSafetyError("R2 API transport differs from the locked authenticated behavior")


def _verify_origin_request_policy(invoker: AwsInvoker, *, policy_id: str) -> None:
    response = _object(
        invoker.json("cloudfront", "get-origin-request-policy", "--id", policy_id),
        "origin request policy response",
    )
    policy = _object(response.get("OriginRequestPolicy"), "origin request policy")
    config = _object(policy.get("OriginRequestPolicyConfig"), "origin request policy config")
    headers = _object(config.get("HeadersConfig"), "origin request headers config")
    header_items = _quantity_items(headers.get("Headers"), "origin request headers")
    header_names = [item for item in header_items if isinstance(item, str)]
    if (
        _object(config.get("CookiesConfig"), "origin request cookies config").get("CookieBehavior")
        != "none"
        or _object(config.get("QueryStringsConfig"), "origin request query config").get(
            "QueryStringBehavior"
        )
        != "all"
        or headers.get("HeaderBehavior") != "whitelist"
        or len(header_names) != len(header_items)
        or sorted(header_names) != ["Accept", "Last-Event-ID", "X-Correlation-ID", "X-Request-ID"]
    ):
        raise AwsSafetyError(
            "CloudFront API origin request policy differs from the locked template"
        )


def _verify_r2_origin_request_policy(invoker: AwsInvoker, *, policy_id: str) -> None:
    response = _object(
        invoker.json("cloudfront", "get-origin-request-policy", "--id", policy_id),
        "R2 origin request policy response",
    )
    policy = _object(response.get("OriginRequestPolicy"), "R2 origin request policy")
    config = _object(policy.get("OriginRequestPolicyConfig"), "R2 origin request policy config")
    headers = _object(config.get("HeadersConfig"), "R2 origin request headers config")
    items = _quantity_items(headers.get("Headers"), "R2 origin request headers")
    if (
        _object(config.get("CookiesConfig"), "R2 origin cookies").get("CookieBehavior") != "none"
        or _object(config.get("QueryStringsConfig"), "R2 origin query").get("QueryStringBehavior")
        != "all"
        or headers.get("HeaderBehavior") != "allExcept"
        or items != ["Host"]
    ):
        raise AwsSafetyError("R2 origin request policy differs from the locked template")


def _verify_bucket_policy(
    invoker: AwsInvoker,
    *,
    bucket: str,
    expected_account_id: str,
    distribution_id: str,
) -> None:
    value = _object(
        invoker.json("s3api", "get-bucket-policy", "--bucket", bucket),
        "bucket policy response",
    ).get("Policy")
    if not isinstance(value, str):
        raise AwsSafetyError("bucket policy response omitted Policy")
    try:
        policy = _object(json.loads(value), "bucket policy")
    except json.JSONDecodeError as exc:
        raise AwsSafetyError("bucket policy is not valid JSON") from exc
    expected = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
            {
                "Sid": "AllowOnlyThisCloudFrontDistribution",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/*",
                "Condition": {
                    "StringEquals": {
                        "AWS:SourceArn": (
                            f"arn:aws:cloudfront::{expected_account_id}:"
                            f"distribution/{distribution_id}"
                        )
                    }
                },
            },
        ],
    }
    if policy != expected:
        raise AwsSafetyError("bucket policy differs from the locked private-origin policy")


def _verify_cache_policy(
    invoker: AwsInvoker,
    *,
    policy_id: str,
    expected_ttls: tuple[int, int, int],
    expected_accept_encoding: bool = True,
) -> None:
    response = _object(
        invoker.json("cloudfront", "get-cache-policy", "--id", policy_id),
        "cache policy response",
    )
    policy = _object(response.get("CachePolicy"), "cache policy")
    config = _object(policy.get("CachePolicyConfig"), "cache policy config")
    actual = (config.get("MinTTL"), config.get("DefaultTTL"), config.get("MaxTTL"))
    if actual != expected_ttls:
        raise AwsSafetyError("CloudFront cache policy TTLs differ from the locked template")
    parameters = _object(
        config.get("ParametersInCacheKeyAndForwardedToOrigin"),
        "cache-key parameters",
    )
    if (
        _object(parameters.get("CookiesConfig"), "cookie config").get("CookieBehavior") != "none"
        or _object(parameters.get("HeadersConfig"), "header config").get("HeaderBehavior") != "none"
        or _object(parameters.get("QueryStringsConfig"), "query config").get("QueryStringBehavior")
        != "none"
        or parameters.get("EnableAcceptEncodingBrotli") is not expected_accept_encoding
        or parameters.get("EnableAcceptEncodingGzip") is not expected_accept_encoding
    ):
        raise AwsSafetyError("CloudFront cache-key policy differs from the locked template")


def _verify_r2_api_cache_policy(invoker: AwsInvoker, *, policy_id: str) -> None:
    response = _object(
        invoker.json("cloudfront", "get-cache-policy", "--id", policy_id),
        "R2 API cache policy response",
    )
    policy = _object(response.get("CachePolicy"), "R2 API cache policy")
    config = _object(policy.get("CachePolicyConfig"), "R2 API cache policy config")
    parameters = _object(
        config.get("ParametersInCacheKeyAndForwardedToOrigin"), "R2 cache parameters"
    )
    headers = _object(parameters.get("HeadersConfig"), "R2 cache headers")
    header_items = headers.get("Headers")
    if (
        (config.get("MinTTL"), config.get("DefaultTTL"), config.get("MaxTTL")) != (0, 0, 0)
        or _object(parameters.get("CookiesConfig"), "R2 cache cookies").get("CookieBehavior")
        != "none"
        or headers.get("HeaderBehavior") != "none"
        or header_items not in (None, {"Quantity": 0}, {"Quantity": 0, "Items": []})
        or _object(parameters.get("QueryStringsConfig"), "R2 cache query").get(
            "QueryStringBehavior"
        )
        != "none"
        or parameters.get("EnableAcceptEncodingBrotli") is not False
        or parameters.get("EnableAcceptEncodingGzip") is not False
    ):
        raise AwsSafetyError("R2 API cache policy differs from the locked template")


def _verify_external_private_preview_policies(
    invoker: AwsInvoker,
    *,
    cache_policy_id: str,
    origin_request_policy_id: str,
) -> None:
    if not cache_policy_id:
        return
    _verify_r2_api_cache_policy(invoker, policy_id=cache_policy_id)
    _verify_r2_origin_request_policy(invoker, policy_id=origin_request_policy_id)


def _verify_response_headers_policy(invoker: AwsInvoker, *, policy_id: str) -> None:
    response = _object(
        invoker.json("cloudfront", "get-response-headers-policy", "--id", policy_id),
        "response headers policy response",
    )
    policy = _object(response.get("ResponseHeadersPolicy"), "response headers policy")
    config = _object(policy.get("ResponseHeadersPolicyConfig"), "response headers config")
    security = _object(config.get("SecurityHeadersConfig"), "security headers config")
    hsts = _object(security.get("StrictTransportSecurity"), "HSTS config")
    csp = _object(security.get("ContentSecurityPolicy"), "CSP config")
    csp_value = csp.get("ContentSecurityPolicy")
    if (
        security.get("ContentTypeOptions") != {"Override": True}
        or security.get("FrameOptions") != {"FrameOption": "DENY", "Override": True}
        or hsts
        != {
            "AccessControlMaxAgeSec": 31536000,
            "IncludeSubdomains": True,
            "Override": True,
            "Preload": True,
        }
        or not isinstance(csp_value, str)
        or "object-src 'none'" not in csp_value
        or "frame-ancestors 'none'" not in csp_value
        or csp.get("Override") is not True
    ):
        raise AwsSafetyError("CloudFront security headers differ from the locked template")


def _verify_oac(invoker: AwsInvoker, *, oac_id: str) -> None:
    response = _object(
        invoker.json("cloudfront", "get-origin-access-control", "--id", oac_id),
        "origin access control response",
    )
    oac = _object(response.get("OriginAccessControl"), "origin access control")
    config = _object(oac.get("OriginAccessControlConfig"), "origin access control config")
    if (
        oac.get("Id") != oac_id
        or config.get("OriginAccessControlOriginType") != "s3"
        or config.get("SigningBehavior") != "always"
        or config.get("SigningProtocol") != "sigv4"
    ):
        raise AwsSafetyError("CloudFront OAC differs from the locked signed S3 origin")


def _verify_resource_tags(
    invoker: AwsInvoker,
    *,
    bucket: str,
    distribution_arn: str,
    product_sha: str,
    stack_id: str,
) -> None:
    bucket_tags = _tags(
        _object(
            invoker.json("s3api", "get-bucket-tagging", "--bucket", bucket),
            "bucket tags",
        ).get("TagSet")
    )
    distribution_tags = _tags(
        _object(
            _object(
                invoker.json(
                    "cloudfront",
                    "list-tags-for-resource",
                    "--resource",
                    distribution_arn,
                ),
                "distribution tags",
            ).get("Tags"),
            "distribution tag collection",
        ).get("Items")
    )
    expected_business_tags = {
        "Project": PROJECT_TAG,
        "ManagedBy": "cloudformation",
        "ProductSha": product_sha,
    }
    expected_cloudformation_bucket_tags = {
        **expected_business_tags,
        "aws:cloudformation:stack-name": STACK_NAME,
        "aws:cloudformation:logical-id": "PublicDemoBucket",
        "aws:cloudformation:stack-id": stack_id,
    }
    if (
        bucket_tags not in (expected_business_tags, expected_cloudformation_bucket_tags)
        or distribution_tags != expected_business_tags
    ):
        raise AwsSafetyError("deployed resource tags do not bind the reviewed ProductSha")


def _verify_deployed_resources(
    invoker: AwsInvoker,
    stack: dict[str, object],
    *,
    expected_account_id: str,
    expected_product_sha: str,
    expected_api_no_cache_policy_id: str | None = None,
    expected_api_origin_request_policy_id: str | None = None,
) -> None:
    outputs = _outputs(stack)
    if outputs["ProductSha"] != _sha(expected_product_sha):
        raise AwsSafetyError("deployed stack ProductSha differs from the reviewed commit")
    api_origin_domain = _deployed_api_origin_domain(
        stack,
        expected_account_id=expected_account_id,
        expected_product_sha=expected_product_sha,
    )
    stack_parameters = {
        str(parameter.get("ParameterKey")): str(parameter.get("ParameterValue"))
        for raw_parameter in _array(stack.get("Parameters"), "stack parameters")
        for parameter in (_object(raw_parameter, "stack parameter"),)
    }
    bound_api_no_cache_policy_id = stack_parameters.get("PrivatePreviewApiNoCachePolicyId", "")
    bound_api_origin_request_policy_id = stack_parameters.get(
        "PrivatePreviewApiOriginRequestPolicyId", ""
    )
    if expected_api_no_cache_policy_id is not None:
        if expected_api_origin_request_policy_id is None:
            raise AwsSafetyError("expected private preview policy IDs are incomplete")
        _private_preview_policy_ids(
            expected_api_no_cache_policy_id,
            expected_api_origin_request_policy_id,
            api_origin_domain=api_origin_domain,
        )
        if (
            bound_api_no_cache_policy_id,
            bound_api_origin_request_policy_id,
        ) != (
            expected_api_no_cache_policy_id,
            expected_api_origin_request_policy_id,
        ):
            raise AwsSafetyError("deployed private preview policy parameters differ from plan")
    stack_id = stack.get("StackId")
    expected_stack_id_prefix = (
        f"arn:aws:cloudformation:{REGION}:{expected_account_id}:stack/{STACK_NAME}/"
    )
    if (
        not isinstance(stack_id, str)
        or not stack_id.startswith(expected_stack_id_prefix)
        or stack_id == expected_stack_id_prefix
    ):
        raise AwsSafetyError("deployed stack ID is outside the project boundary")
    bucket = outputs["BucketName"]
    distribution_id = outputs["DistributionId"]
    _verify_bucket(invoker, bucket)
    _verify_bucket_policy(
        invoker,
        bucket=bucket,
        expected_account_id=expected_account_id,
        distribution_id=distribution_id,
    )
    response = _object(
        invoker.json(
            "cloudfront",
            "get-distribution",
            "--id",
            distribution_id,
            "--query",
            _DISTRIBUTION_VERIFICATION_QUERY,
        ),
        "distribution response",
    )
    distribution = _object(response.get("Distribution"), "distribution")
    expected_arn = f"arn:aws:cloudfront::{expected_account_id}:distribution/{distribution_id}"
    if (
        distribution.get("Id") != distribution_id
        or distribution.get("ARN") != expected_arn
        or distribution.get("Status") != "Deployed"
        or distribution.get("DomainName") != outputs["DistributionDomainName"]
    ):
        raise AwsSafetyError("CloudFront distribution identity is not the deployed project")
    config = _object(distribution.get("DistributionConfig"), "distribution config")
    certificate = _object(config.get("ViewerCertificate"), "viewer certificate")
    if (
        config.get("Enabled") is not True
        or config.get("HttpVersion") != "http2and3"
        or config.get("PriceClass") != "PriceClass_200"
        or config.get("DefaultRootObject") != "index.html"
        or config.get("IsIPV6Enabled") is not True
        or config.get("Aliases") not in ({"Quantity": 0}, {"Quantity": 0, "Items": []})
        or config.get("Logging")
        != {
            "Enabled": False,
            "IncludeCookies": False,
            "Bucket": "",
            "Prefix": "",
        }
        or config.get("WebACLId", "") != ""
        or certificate.get("CloudFrontDefaultCertificate") is not True
    ):
        raise AwsSafetyError("CloudFront distribution settings differ from the locked template")
    origins = _quantity_items(config.get("Origins"), "origins")
    origins_by_id: dict[str, dict[str, object]] = {}
    for raw_origin in origins:
        candidate = _object(raw_origin, "origin")
        origin_id = candidate.get("Id")
        if not isinstance(origin_id, str) or origin_id in origins_by_id:
            raise AwsSafetyError("CloudFront origins are malformed")
        origins_by_id[origin_id] = candidate
    expected_origin_ids = {"private-s3-origin"}
    if api_origin_domain:
        expected_origin_ids.add("runtime-api-origin")
    if set(origins_by_id) != expected_origin_ids:
        raise AwsSafetyError("CloudFront origins differ from the locked template")
    origin = origins_by_id["private-s3-origin"]
    oac_id = origin.get("OriginAccessControlId")
    s3_origin_config = _object(origin.get("S3OriginConfig"), "S3 origin config")
    origin_read_timeout = s3_origin_config.get("OriginReadTimeout")
    locked_s3_origin_config = s3_origin_config == {"OriginAccessIdentity": ""} or (
        set(s3_origin_config) == {"OriginAccessIdentity", "OriginReadTimeout"}
        and s3_origin_config.get("OriginAccessIdentity") == ""
        and type(origin_read_timeout) is int
        and origin_read_timeout == 30
    )
    if (
        origin.get("Id") != "private-s3-origin"
        or origin.get("DomainName") != f"{bucket}.s3.{REGION}.amazonaws.com"
        or not isinstance(oac_id, str)
        or not oac_id
        or not locked_s3_origin_config
    ):
        raise AwsSafetyError("CloudFront origin differs from the locked private S3 origin")
    if api_origin_domain:
        api_origin = origins_by_id["runtime-api-origin"]
        custom = _object(api_origin.get("CustomOriginConfig"), "API custom origin config")
        origin_headers = _quantity_items(
            api_origin.get("CustomHeaders"),
            "API origin verification headers",
        )
        origin_header = (
            _object(origin_headers[0], "API origin verification header")
            if len(origin_headers) == 1
            else {}
        )
        if (
            api_origin.get("DomainName") != api_origin_domain
            or api_origin.get("OriginAccessControlId", "") != ""
            or origin_header.get("HeaderName") != "X-StockMarketAgent-Origin-Verify"
            or origin_header.get("HeaderValuePresent") is not True
            or custom
            != {
                "HTTPPort": 80,
                "HTTPSPort": 443,
                "OriginProtocolPolicy": "http-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                "OriginReadTimeout": 60,
                "OriginKeepaliveTimeout": 5,
            }
        ):
            raise AwsSafetyError("CloudFront API origin differs from the locked ALB origin")
    default = _object(config.get("DefaultCacheBehavior"), "default cache behavior")
    behaviors = _quantity_items(config.get("CacheBehaviors"), "cache behaviors")
    expected_behavior_count = 5 if api_origin_domain else 3
    if len(behaviors) != expected_behavior_count:
        raise AwsSafetyError(
            "CloudFront path cache behavior count differs from the locked template"
        )
    behaviors_by_path: dict[str, dict[str, object]] = {}
    for raw_behavior in behaviors:
        behavior = _object(raw_behavior, "path cache behavior")
        path = behavior.get("PathPattern")
        if not isinstance(path, str) or path in behaviors_by_path:
            raise AwsSafetyError("CloudFront path cache behaviors are malformed")
        behaviors_by_path[path] = behavior
    expected_behavior_paths = {
        "_next/static/*",
        "data/current.json",
        "data/snapshots/*",
    }
    if api_origin_domain:
        expected_behavior_paths.update(("api/*", "api/v2/*"))
    if set(behaviors_by_path) != expected_behavior_paths:
        raise AwsSafetyError("CloudFront path cache behaviors differ from the locked template")
    current = behaviors_by_path["data/current.json"]
    snapshots = behaviors_by_path["data/snapshots/*"]
    next_static = behaviors_by_path["_next/static/*"]
    _verify_cache_behavior_transport(default, label="default cache behavior")
    for path in ("_next/static/*", "data/current.json", "data/snapshots/*"):
        behavior = behaviors_by_path[path]
        _verify_cache_behavior_transport(behavior, label=f"{path} cache behavior")
    short_policy_id = default.get("CachePolicyId")
    no_cache_policy_id = current.get("CachePolicyId")
    immutable_policy_id = snapshots.get("CachePolicyId")
    headers_policy_id = default.get("ResponseHeadersPolicyId")
    if (
        default.get("ViewerProtocolPolicy") != "redirect-to-https"
        or default.get("TargetOriginId") != "private-s3-origin"
        or any(
            behavior.get("ViewerProtocolPolicy") != "redirect-to-https"
            or behavior.get("TargetOriginId") != "private-s3-origin"
            or behavior.get("ResponseHeadersPolicyId") != headers_policy_id
            for path, behavior in behaviors_by_path.items()
            if path not in {"api/*", "api/v2/*"}
        )
        or next_static.get("CachePolicyId") != immutable_policy_id
        or not all(
            isinstance(value, str) and value
            for value in (
                short_policy_id,
                no_cache_policy_id,
                immutable_policy_id,
                headers_policy_id,
            )
        )
    ):
        raise AwsSafetyError("CloudFront cache behaviors differ from the locked template")
    api_origin_request_policy_id: str | None = None
    r2_origin_request_policy_id: str | None = None
    r2_cache_policy_id: str | None = None
    if api_origin_domain:
        api_behavior = behaviors_by_path["api/*"]
        _verify_api_cache_behavior_transport(api_behavior)
        raw_origin_request_policy_id = api_behavior.get("OriginRequestPolicyId")
        if (
            api_behavior.get("ViewerProtocolPolicy") != "redirect-to-https"
            or api_behavior.get("TargetOriginId") != "runtime-api-origin"
            or api_behavior.get("ResponseHeadersPolicyId") != headers_policy_id
            or api_behavior.get("CachePolicyId") != no_cache_policy_id
            or not isinstance(raw_origin_request_policy_id, str)
            or not raw_origin_request_policy_id
        ):
            raise AwsSafetyError("CloudFront API cache behavior differs from the locked template")
        api_origin_request_policy_id = raw_origin_request_policy_id
        r2_behavior = behaviors_by_path["api/v2/*"]
        _verify_r2_api_cache_behavior_transport(r2_behavior)
        raw_r2_origin_policy_id = r2_behavior.get("OriginRequestPolicyId")
        raw_r2_cache_policy_id = r2_behavior.get("CachePolicyId")
        if (
            r2_behavior.get("ViewerProtocolPolicy") != "redirect-to-https"
            or r2_behavior.get("TargetOriginId") != "runtime-api-origin"
            or r2_behavior.get("ResponseHeadersPolicyId") != headers_policy_id
            or not isinstance(raw_r2_origin_policy_id, str)
            or not raw_r2_origin_policy_id
            or not isinstance(raw_r2_cache_policy_id, str)
            or not raw_r2_cache_policy_id
            or raw_r2_cache_policy_id == no_cache_policy_id
            or (
                bool(bound_api_no_cache_policy_id)
                and raw_r2_cache_policy_id != bound_api_no_cache_policy_id
            )
            or (
                bool(bound_api_origin_request_policy_id)
                and raw_r2_origin_policy_id != bound_api_origin_request_policy_id
            )
        ):
            raise AwsSafetyError("CloudFront R2 API behavior differs from the locked template")
        r2_origin_request_policy_id = raw_r2_origin_policy_id
        r2_cache_policy_id = raw_r2_cache_policy_id
    functions = _quantity_items(default.get("FunctionAssociations"), "function associations")
    function = _object(functions[0], "function association") if len(functions) == 1 else {}
    if (
        function.get("EventType") != "viewer-request"
        or function.get("FunctionARN")
        != f"arn:aws:cloudfront::{expected_account_id}:function/{STACK_NAME}-route-rewrite"
    ):
        raise AwsSafetyError("CloudFront viewer-request rewrite is not attached")
    for path, behavior in behaviors_by_path.items():
        path_functions = _quantity_items(
            behavior.get("FunctionAssociations"),
            f"{path} function associations",
        )
        if path_functions:
            raise AwsSafetyError("path cache behaviors must not attach CloudFront functions")
    errors = _quantity_items(config.get("CustomErrorResponses"), "custom errors")
    if errors:
        raise AwsSafetyError("CloudFront must not intercept API origin error responses")
    _verify_oac(invoker, oac_id=oac_id)
    _verify_cache_policy(
        invoker,
        policy_id=str(short_policy_id),
        expected_ttls=(0, 60, 300),
    )
    _verify_cache_policy(
        invoker,
        policy_id=str(no_cache_policy_id),
        expected_ttls=(0, 0, 0),
        expected_accept_encoding=False,
    )
    _verify_cache_policy(
        invoker,
        policy_id=str(immutable_policy_id),
        expected_ttls=(31536000, 31536000, 31536000),
    )
    _verify_response_headers_policy(invoker, policy_id=str(headers_policy_id))
    if api_origin_request_policy_id is not None:
        _verify_origin_request_policy(invoker, policy_id=api_origin_request_policy_id)
    if r2_origin_request_policy_id is not None and r2_cache_policy_id is not None:
        _verify_r2_origin_request_policy(invoker, policy_id=r2_origin_request_policy_id)
        _verify_r2_api_cache_policy(invoker, policy_id=r2_cache_policy_id)
    _verify_resource_tags(
        invoker,
        bucket=bucket,
        distribution_arn=expected_arn,
        product_sha=expected_product_sha,
        stack_id=stack_id,
    )


def upload(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    artifact_dir: Path,
    confirmation: str,
) -> dict[str, str]:
    if confirmation != "SYNC_DELETE":
        raise AwsSafetyError("upload requires the exact SYNC_DELETE confirmation")
    _identity(invoker, expected_account_id)
    stack = verify_existing_stack(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
    )
    _verify_deployed_resources(
        invoker,
        stack,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
    )
    outputs = _outputs(stack)
    if outputs["ProductSha"] != _sha(product_sha):
        raise AwsSafetyError("stack ProductSha does not match the artifact")
    if artifact_dir.resolve() != artifact_dir or not artifact_dir.is_dir():
        raise AwsSafetyError("artifact directory must be canonical")
    scan_public_tree(artifact_dir, kind="artifact")
    metadata = _object(
        json.loads((artifact_dir / "build-metadata.json").read_text(encoding="utf-8")),
        "artifact metadata",
    )
    if (
        metadata.get("project") != "StockMarketAgent"
        or metadata.get("product_commit") != product_sha
    ):
        raise AwsSafetyError("artifact metadata is not the reviewed project build")
    bucket = outputs["BucketName"]
    listed = _object(
        invoker.json("s3api", "list-objects-v2", "--bucket", bucket, "--max-keys", "1"),
        "bucket listing",
    )
    key_count = listed.get("KeyCount", 0)
    if not isinstance(key_count, int) or isinstance(key_count, bool) or key_count < 0:
        raise AwsSafetyError("bucket listing KeyCount is malformed")
    if key_count:
        raise AwsSafetyError("FULL_SITE_UPLOAD_INITIAL_ONLY")
    common = (
        str(artifact_dir),
        f"s3://{bucket}",
        "--delete",
        "--sse",
        "AES256",
        "--cache-control",
        "public,max-age=300,must-revalidate",
    )
    invoker.text("s3", "sync", *common, "--dryrun")
    invoker.text("s3", "sync", *common)
    immutable = artifact_dir / "_next/static"
    if not immutable.is_dir():
        raise AwsSafetyError("immutable Next.js artifact directory is missing")
    invoker.text(
        "s3",
        "cp",
        str(immutable),
        f"s3://{bucket}/_next/static",
        "--recursive",
        "--sse",
        "AES256",
        "--cache-control",
        "public,max-age=31536000,immutable",
    )
    invalidation = _object(
        invoker.json(
            "cloudfront",
            "create-invalidation",
            "--distribution-id",
            outputs["DistributionId"],
            "--paths",
            "/*",
        ),
        "create invalidation response",
    )
    invalidation_record = _object(invalidation.get("Invalidation"), "invalidation")
    invalidation_id = invalidation_record.get("Id")
    if not isinstance(invalidation_id, str) or not invalidation_id:
        raise AwsSafetyError("CloudFront invalidation response omitted its Id")
    invoker.text(
        "cloudfront",
        "wait",
        "invalidation-completed",
        "--distribution-id",
        outputs["DistributionId"],
        "--id",
        invalidation_id,
    )
    return outputs


def _optional_head_object(
    invoker: AwsInvoker,
    *,
    bucket: str,
    key: str,
    version_id: str | None = None,
) -> dict[str, object] | None:
    arguments = ["--bucket", bucket, "--key", key]
    if version_id is not None:
        arguments.extend(("--version-id", version_id))
    try:
        return _object(
            invoker.json(
                "s3api",
                "head-object",
                *arguments,
            ),
            f"head object {key}",
        )
    except subprocess.CalledProcessError as error:
        detail = f"{error.stderr or ''} {error.stdout or ''}"
        if any(token in detail for token in ("404", "Not Found", "NoSuchKey")):
            return None
        raise


def _versioned_object_body(
    invoker: AwsInvoker,
    *,
    bucket: str,
    key: str,
    version_id: str,
) -> bytes:
    with TemporaryDirectory(prefix="stockmarketagent-s3-readback-") as temporary:
        output = Path(temporary).resolve() / "object"
        invoker.json(
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--version-id",
            version_id,
            str(output),
        )
        try:
            return output.read_bytes()
        except OSError as error:
            raise AwsSafetyError(f"versioned object readback is unavailable: {key}") from error


def _versioned_head(value: dict[str, object], *, key: str) -> tuple[str, str]:
    etag = value.get("ETag")
    version_id = value.get("VersionId")
    if not isinstance(etag, str) or not etag or not isinstance(version_id, str) or not version_id:
        raise AwsSafetyError(f"versioned object metadata is incomplete: {key}")
    return etag, version_id


def _metadata(value: dict[str, object], *, label: str) -> dict[str, str]:
    return _tag_mapping(value.get("Metadata"), label)


def _put_marker(
    invoker: AwsInvoker,
    *,
    bucket: str,
    metadata: dict[str, str],
    if_match: str | None,
) -> dict[str, object]:
    arguments = [
        "--bucket",
        bucket,
        "--key",
        SHELL_UPGRADE_MARKER_KEY,
        "--server-side-encryption",
        "AES256",
        "--cache-control",
        "no-store,max-age=0",
        "--metadata",
        ",".join(f"{key}={value}" for key, value in sorted(metadata.items())),
    ]
    if if_match is None:
        arguments.extend(("--if-none-match", "*"))
    else:
        arguments.extend(("--if-match", if_match))
    response = _object(
        invoker.json("s3api", "put-object", *arguments),
        "put upgrade marker",
    )
    _versioned_head(response, key=SHELL_UPGRADE_MARKER_KEY)
    head = _optional_head_object(
        invoker,
        bucket=bucket,
        key=SHELL_UPGRADE_MARKER_KEY,
    )
    if head is None or _metadata(head, label="upgrade marker metadata") != metadata:
        raise AwsSafetyError("SHELL_UPGRADE_MARKER_READBACK_MISMATCH")
    _versioned_head(head, key=SHELL_UPGRADE_MARKER_KEY)
    return head


def _remote_build_metadata(invoker: AwsInvoker, *, bucket: str) -> dict[str, object]:
    try:
        return _object(
            json.loads(
                invoker.text(
                    "s3",
                    "cp",
                    f"s3://{bucket}/build-metadata.json",
                    "-",
                )
            ),
            "deployed build metadata",
        )
    except json.JSONDecodeError as error:
        raise AwsSafetyError("deployed build metadata is invalid JSON") from error


def _validate_build_metadata(value: dict[str, object], *, product_sha: str) -> None:
    if value.get("project") != "StockMarketAgent" or value.get("product_commit") != product_sha:
        raise AwsSafetyError("deployed build metadata is outside the requested shell state")


def _route_invalidation_paths(route_paths: list[str]) -> tuple[str, ...]:
    paths = {"/build-metadata.json"}
    for relative in route_paths:
        path = Path(relative)
        if path.suffix.casefold() != ".html":
            continue
        physical = f"/{relative}"
        paths.add(physical)
        if relative == "index.html":
            paths.add("/")
        elif relative.endswith("/index.html"):
            route = f"/{relative.removesuffix('/index.html')}"
            paths.update((route, f"{route}/"))
        elif path.name != "404.html":
            paths.add(physical.removesuffix(".html"))
    if "/*" in paths or len(paths) > 100:
        raise AwsSafetyError("shell route invalidation inventory is unsafe")
    return tuple(sorted(paths))


def _verify_versioned_file(
    invoker: AwsInvoker,
    *,
    bucket: str,
    key: str,
    expected_body: bytes,
    expected_content_type: str,
    expected_cache_control: str,
    expected_server_side_encryption: str,
    receipt: dict[str, object],
) -> dict[str, object]:
    expected_metadata = {"sha256": hashlib.sha256(expected_body).hexdigest()}
    expected_etag, version_id = _versioned_head(receipt, key=key)
    exact = _optional_head_object(
        invoker,
        bucket=bucket,
        key=key,
        version_id=version_id,
    )
    if exact is None:
        raise AwsSafetyError(f"SHELL_OBJECT_VERSION_MISSING:{key}")
    exact_etag, exact_version_id = _versioned_head(exact, key=key)
    if (
        (exact_etag, exact_version_id) != (expected_etag, version_id)
        or exact.get("ContentLength") != len(expected_body)
        or _metadata(exact, label=f"{key} version metadata") != expected_metadata
        or exact.get("ContentType") != expected_content_type
        or exact.get("CacheControl") != expected_cache_control
        or exact.get("ServerSideEncryption") != expected_server_side_encryption
        or _versioned_object_body(
            invoker,
            bucket=bucket,
            key=key,
            version_id=version_id,
        )
        != expected_body
    ):
        raise AwsSafetyError(f"SHELL_OBJECT_VERSION_READBACK_MISMATCH:{key}")
    current = _optional_head_object(invoker, bucket=bucket, key=key)
    if current is None:
        raise AwsSafetyError(f"SHELL_OBJECT_CURRENT_VERSION_MISSING:{key}")
    current_etag, current_version_id = _versioned_head(current, key=key)
    if (
        (current_etag, current_version_id) != (expected_etag, version_id)
        or current.get("ContentLength") != len(expected_body)
        or _metadata(current, label=f"{key} current metadata") != expected_metadata
        or current.get("ContentType") != expected_content_type
        or current.get("CacheControl") != expected_cache_control
        or current.get("ServerSideEncryption") != expected_server_side_encryption
    ):
        raise AwsSafetyError(f"SHELL_OBJECT_CURRENT_VERSION_MISMATCH:{key}")
    return current


def _legacy_immutable_version_is_reusable(
    invoker: AwsInvoker,
    *,
    bucket: str,
    key: str,
    expected_body: bytes,
    expected_content_type: str,
    existing: dict[str, object],
) -> bool:
    """Reuse a byte-identical pre-receipt immutable object without overwriting it."""

    expected_mime = expected_content_type.partition(";")[0].strip().casefold()

    def has_legacy_safe_policy(value: dict[str, object]) -> bool:
        content_type = value.get("ContentType")
        metadata = value.get("Metadata")
        return (
            value.get("ContentLength") == len(expected_body)
            and isinstance(metadata, dict)
            and not metadata
            and value.get("CacheControl") is None
            and value.get("ServerSideEncryption") == SHELL_OBJECT_SERVER_SIDE_ENCRYPTION
            and isinstance(content_type, str)
            and content_type.partition(";")[0].strip().casefold() == expected_mime
        )

    if not has_legacy_safe_policy(existing):
        return False
    expected_etag, version_id = _versioned_head(existing, key=key)
    exact = _optional_head_object(invoker, bucket=bucket, key=key, version_id=version_id)
    if exact is None or not has_legacy_safe_policy(exact):
        return False
    if _versioned_head(exact, key=key) != (expected_etag, version_id):
        return False
    if (
        _versioned_object_body(invoker, bucket=bucket, key=key, version_id=version_id)
        != expected_body
    ):
        return False
    current = _optional_head_object(invoker, bucket=bucket, key=key)
    return (
        current is not None
        and has_legacy_safe_policy(current)
        and _versioned_head(current, key=key) == (expected_etag, version_id)
    )


def _ensure_versioned_file(
    invoker: AwsInvoker,
    *,
    bucket: str,
    relative: str,
    body: bytes,
    immutable: bool,
) -> dict[str, object]:
    if (
        not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or relative.startswith("data/")
        or relative.startswith("ops/")
        or relative == SHELL_UPGRADE_MARKER_KEY
    ):
        raise AwsSafetyError(f"shell upgrade object is outside its inventory: {relative}")
    digest = hashlib.sha256(body).hexdigest()
    expected_metadata = {"sha256": digest}
    content_type, cache_control = _shell_object_policy(relative, immutable=immutable)
    existing = _optional_head_object(invoker, bucket=bucket, key=relative)
    if existing is not None:
        matches = (
            existing.get("ContentLength") == len(body)
            and _metadata(existing, label=f"{relative} metadata") == expected_metadata
            and existing.get("ContentType") == content_type
            and existing.get("CacheControl") == cache_control
            and existing.get("ServerSideEncryption") == SHELL_OBJECT_SERVER_SIDE_ENCRYPTION
        )
        if matches:
            _versioned_head(existing, key=relative)
            return _verify_versioned_file(
                invoker,
                bucket=bucket,
                key=relative,
                expected_body=body,
                expected_content_type=content_type,
                expected_cache_control=cache_control,
                expected_server_side_encryption=SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
                receipt=existing,
            )
        if immutable:
            if _legacy_immutable_version_is_reusable(
                invoker,
                bucket=bucket,
                key=relative,
                expected_body=body,
                expected_content_type=content_type,
                existing=existing,
            ):
                return existing
            raise AwsSafetyError(f"IMMUTABLE_SHELL_COLLISION:{relative}")
    with TemporaryDirectory(prefix="stockmarketagent-shell-object-") as temporary:
        upload = Path(temporary).resolve() / "body"
        upload.write_bytes(body)
        arguments = [
            "--bucket",
            bucket,
            "--key",
            relative,
            "--body",
            str(upload),
            "--content-type",
            content_type,
            "--server-side-encryption",
            SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
            "--cache-control",
            cache_control,
            "--metadata",
            f"sha256={digest}",
        ]
        if immutable:
            arguments.extend(("--if-none-match", "*"))
        response = _object(
            invoker.json("s3api", "put-object", *arguments),
            f"put {relative}",
        )
    _versioned_head(response, key=relative)
    return _verify_versioned_file(
        invoker,
        bucket=bucket,
        key=relative,
        expected_body=body,
        expected_content_type=content_type,
        expected_cache_control=cache_control,
        expected_server_side_encryption=SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
        receipt=response,
    )


def verify_shell_release_source(*, repository_root: Path, product_sha: str) -> str:
    """Prove the build executes from one clean checkout and exact reviewed blobs."""

    sha = _sha(product_sha)
    if repository_root.resolve() != repository_root or not repository_root.is_dir():
        raise AwsSafetyError("SHELL_RELEASE_REPOSITORY_INVALID")
    git = which("git")
    if git is None:
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_UNVERIFIED")
    try:
        top_level = subprocess.run(  # noqa: S603 - fixed Git argv only.
            [git, "-C", str(repository_root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        head = subprocess.run(  # noqa: S603 - fixed Git argv only.
            [git, "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        tree = subprocess.run(  # noqa: S603 - fixed Git argv only.
            [git, "-C", str(repository_root), "rev-parse", f"{sha}^{{tree}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603 - fixed Git argv only.
            [
                git,
                "-C",
                str(repository_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        listed = subprocess.run(  # noqa: S603 - fixed Git argv only.
            [
                git,
                "-C",
                str(repository_root),
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                sha,
                "--",
                "apps/web",
                "scripts/public_demo_artifacts.py",
                "scripts/public_demo_aws.py",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_UNVERIFIED") from error
    if Path(top_level).resolve() != repository_root:
        raise AwsSafetyError("SHELL_RELEASE_REPOSITORY_INVALID")
    if head != sha or SHA_PATTERN.fullmatch(tree) is None or status:
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_NOT_CLEAN")
    try:
        tracked = tuple(value.decode("utf-8") for value in listed.split(b"\0") if value)
    except UnicodeDecodeError as error:
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_PATH_INVALID") from error
    required = {"scripts/public_demo_artifacts.py", "scripts/public_demo_aws.py"}
    if (
        not tracked
        or not required.issubset(tracked)
        or not any(value.startswith("apps/web/") for value in tracked)
    ):
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_INCOMPLETE")
    for relative in tracked:
        local = repository_root / relative
        if local.is_symlink() or not local.is_file():
            raise AwsSafetyError(f"SHELL_RELEASE_SOURCE_FILE_INVALID:{relative}")
        try:
            committed = subprocess.run(  # noqa: S603 - fixed Git argv only.
                [git, "-C", str(repository_root), "show", f"{sha}:{relative}"],
                check=True,
                capture_output=True,
                timeout=30,
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise AwsSafetyError("SHELL_RELEASE_SOURCE_UNVERIFIED") from error
        if committed != local.read_bytes():
            raise AwsSafetyError(f"SHELL_RELEASE_SOURCE_BLOB_MISMATCH:{relative}")
    return tree


def _freeze_public_tree(source: Path, destination: Path, *, kind: str) -> Path:
    """Copy one scanned public tree into a private read-only staging directory."""

    if source.resolve() != source or not source.is_dir() or source.is_symlink():
        raise AwsSafetyError(f"{kind} directory must be canonical")
    destination.mkdir(parents=True)
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise AwsSafetyError(f"{kind} symlink is forbidden: {path.relative_to(source)}")
        if path.is_dir():
            continue
        if not path.is_file() or path.resolve().parent != path.parent.resolve():
            raise AwsSafetyError(f"{kind} item is not a regular file: {path}")
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        target.chmod(0o400)
    scan_public_tree(destination, kind=kind)
    return destination


def _freeze_shell_artifact(source: Path, destination: Path) -> Path:
    return _freeze_public_tree(source, destination, kind="artifact")


@dataclass(frozen=True, slots=True)
class _ShellReleaseReceipt:
    body: bytes
    sha256: str
    intent_sha256: str
    key: str


@dataclass(frozen=True, slots=True)
class _ShellReleaseMaterial:
    body: bytes
    sha256: str
    snapshot_files: tuple[tuple[str, bytes], ...]
    artifact_files: tuple[tuple[str, bytes], ...]


def _shell_content_type(path: Path) -> str:
    return {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".txt": "text/plain; charset=utf-8",
        ".xml": "application/xml",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _shell_object_policy(relative: str, *, immutable: bool) -> tuple[str, str]:
    cache_control = SHELL_IMMUTABLE_CACHE_CONTROL if immutable else SHELL_MUTABLE_CACHE_CONTROL
    return _shell_content_type(Path(relative)), cache_control


def _capture_release_tree(root: Path, *, kind: str) -> tuple[tuple[str, bytes], ...]:
    if root.resolve() != root or not root.is_dir() or root.is_symlink():
        raise AwsSafetyError(f"{kind} directory must be canonical")
    captured: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AwsSafetyError(f"{kind} symlink is forbidden: {path.relative_to(root)}")
        if path.is_dir():
            continue
        if not path.is_file() or path.resolve().parent != path.parent.resolve():
            raise AwsSafetyError(f"{kind} item is not a regular file: {path}")
        captured.append((path.relative_to(root).as_posix(), path.read_bytes()))
    if not captured:
        raise AwsSafetyError(f"{kind} tree is empty")
    return tuple(sorted(captured, key=lambda item: item[0]))


def _write_captured_tree(
    files: tuple[tuple[str, bytes], ...],
    destination: Path,
) -> Path:
    destination.mkdir(parents=True)
    for relative, body in files:
        path = Path(relative)
        if not relative or path.is_absolute() or ".." in path.parts:
            raise AwsSafetyError("SHELL_RELEASE_MATERIAL_PATH_INVALID")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(0o400)
    return destination


def _release_inventory(
    files: tuple[tuple[str, bytes], ...],
    *,
    artifact: bool,
) -> list[dict[str, object]]:
    paths = [relative for relative, _body in files]
    if paths != sorted(set(paths)) or any(
        not relative or Path(relative).is_absolute() or ".." in Path(relative).parts
        for relative in paths
    ):
        raise AwsSafetyError("SHELL_RELEASE_MATERIAL_PATH_INVALID")
    inventory: list[dict[str, object]] = []
    for relative, body in files:
        path = Path(relative)
        item: dict[str, object] = {
            "path": relative,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        if artifact:
            if relative.startswith("data/"):
                cache_class = "SNAPSHOT_DATA_UNTOUCHED"
            elif relative.startswith("_next/static/"):
                cache_class = "IMMUTABLE"
            elif path.suffix.casefold() == ".html" or relative == "build-metadata.json":
                cache_class = "ROUTE"
            else:
                cache_class = "MUTABLE"
            item.update(
                {
                    "cache_class": cache_class,
                    "content_type": _shell_content_type(path),
                }
            )
        inventory.append(item)
    return inventory


def _build_shell_release_material(
    *,
    old_product_sha: str,
    new_product_sha: str,
    source_tree_sha: str,
    snapshot_dir: Path,
    artifact_dir: Path,
) -> _ShellReleaseMaterial:
    old_sha = _sha(old_product_sha)
    new_sha = _sha(new_product_sha)
    if SHA_PATTERN.fullmatch(source_tree_sha) is None:
        raise AwsSafetyError("SHELL_RELEASE_SOURCE_TREE_INVALID")
    snapshot_files = _capture_release_tree(snapshot_dir, kind="snapshot")
    artifact_files = _capture_release_tree(artifact_dir, kind="artifact")
    with TemporaryDirectory(prefix="stockmarketagent-shell-material-") as temporary:
        verification = Path(temporary).resolve()
        captured_snapshot = _write_captured_tree(snapshot_files, verification / "snapshot")
        captured_artifact = _write_captured_tree(artifact_files, verification / "artifact")
        pair = verify_public_pair(captured_snapshot, captured_artifact, new_sha)
    snapshot_inventory = _release_inventory(snapshot_files, artifact=False)
    artifact_inventory = _release_inventory(artifact_files, artifact=True)
    document = {
        "schema_version": SHELL_RELEASE_RECEIPT_SCHEMA,
        "old_product_sha": old_sha,
        "new_product_sha": new_sha,
        "source_tree_sha": source_tree_sha,
        "snapshot_scan": pair["snapshot"],
        "artifact_scan": pair["artifact"],
        "snapshot_inventory": snapshot_inventory,
        "artifact_inventory": artifact_inventory,
        "snapshot_inventory_sha256": _canonical_hash(snapshot_inventory),
        "artifact_inventory_sha256": _canonical_hash(artifact_inventory),
    }
    body = (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")
    return _ShellReleaseMaterial(
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        snapshot_files=snapshot_files,
        artifact_files=artifact_files,
    )


def _validated_shell_release_material(
    material: _ShellReleaseMaterial,
) -> dict[str, object]:
    if hashlib.sha256(material.body).hexdigest() != material.sha256:
        raise AwsSafetyError("SHELL_RELEASE_MATERIAL_INVALID")
    try:
        local = _object(json.loads(material.body), "shell release material")
    except json.JSONDecodeError as error:
        raise AwsSafetyError("SHELL_RELEASE_MATERIAL_INVALID") from error
    old_product_sha = local.get("old_product_sha")
    new_product_sha = local.get("new_product_sha")
    source_tree_sha = local.get("source_tree_sha")
    snapshot_inventory = local.get("snapshot_inventory")
    artifact_inventory = local.get("artifact_inventory")
    if (
        set(local)
        != {
            "schema_version",
            "old_product_sha",
            "new_product_sha",
            "source_tree_sha",
            "snapshot_scan",
            "artifact_scan",
            "snapshot_inventory",
            "artifact_inventory",
            "snapshot_inventory_sha256",
            "artifact_inventory_sha256",
        }
        or local.get("schema_version") != SHELL_RELEASE_RECEIPT_SCHEMA
        or not isinstance(old_product_sha, str)
        or SHA_PATTERN.fullmatch(old_product_sha) is None
        or not isinstance(new_product_sha, str)
        or SHA_PATTERN.fullmatch(new_product_sha) is None
        or not isinstance(source_tree_sha, str)
        or SHA_PATTERN.fullmatch(source_tree_sha) is None
        or not isinstance(snapshot_inventory, list)
        or not isinstance(artifact_inventory, list)
        or snapshot_inventory != _release_inventory(material.snapshot_files, artifact=False)
        or artifact_inventory != _release_inventory(material.artifact_files, artifact=True)
        or local.get("snapshot_inventory_sha256") != _canonical_hash(snapshot_inventory)
        or local.get("artifact_inventory_sha256") != _canonical_hash(artifact_inventory)
    ):
        raise AwsSafetyError("SHELL_RELEASE_MATERIAL_INVALID")
    return local


def _build_shell_release_receipt(
    *,
    material: _ShellReleaseMaterial,
    expected_old_product_sha: str,
    expected_new_product_sha: str,
    expected_account_id: str,
    stack_id: str,
    bucket: str,
    distribution_id: str,
) -> _ShellReleaseReceipt:
    local = _validated_shell_release_material(material)
    old_product_sha = local["old_product_sha"]
    new_product_sha = local["new_product_sha"]
    source_tree_sha = local["source_tree_sha"]
    snapshot_inventory = local["snapshot_inventory"]
    artifact_inventory = local["artifact_inventory"]
    if (
        old_product_sha != expected_old_product_sha
        or new_product_sha != expected_new_product_sha
        or ACCOUNT_PATTERN.fullmatch(expected_account_id) is None
        or not stack_id.startswith(
            f"arn:aws:cloudformation:{REGION}:{expected_account_id}:stack/{STACK_NAME}/"
        )
        or not bucket
        or not distribution_id
    ):
        raise AwsSafetyError("SHELL_RELEASE_TARGET_INVALID")
    intent = {
        "schema_version": SHELL_RELEASE_RECEIPT_SCHEMA,
        "old_product_sha": old_product_sha,
        "new_product_sha": new_product_sha,
        "source_tree_sha": source_tree_sha,
        "deployment_target": {
            "expected_account_id": expected_account_id,
            "stack_id": stack_id,
            "bucket": bucket,
            "distribution_id": distribution_id,
        },
        "snapshot_inventory_sha256": local["snapshot_inventory_sha256"],
        "artifact_inventory_sha256": local["artifact_inventory_sha256"],
    }
    intent_sha256 = _canonical_hash(intent)
    document = {
        "schema_version": SHELL_RELEASE_RECEIPT_SCHEMA,
        "project": PROJECT_TAG,
        "release_id": intent_sha256,
        "intent": intent,
        "material_sha256": material.sha256,
        "snapshot_scan": local.get("snapshot_scan"),
        "artifact_scan": local.get("artifact_scan"),
        "snapshot_inventory": snapshot_inventory,
        "artifact_inventory": artifact_inventory,
    }
    body = (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    return _ShellReleaseReceipt(
        body=body,
        sha256=digest,
        intent_sha256=intent_sha256,
        key=f"{SHELL_RELEASE_RECEIPT_PREFIX}/{intent_sha256}.json",
    )


def _ensure_shell_release_receipt(
    invoker: AwsInvoker,
    *,
    bucket: str,
    receipt: _ShellReleaseReceipt,
) -> dict[str, object]:
    """Create or reuse the exact immutable receipt and prove its current version."""

    if (
        receipt.key != f"{SHELL_RELEASE_RECEIPT_PREFIX}/{receipt.intent_sha256}.json"
        or HEX_SHA256_PATTERN.fullmatch(receipt.intent_sha256) is None
        or hashlib.sha256(receipt.body).hexdigest() != receipt.sha256
    ):
        raise AwsSafetyError("SHELL_RELEASE_RECEIPT_INVALID")
    existing = _optional_head_object(invoker, bucket=bucket, key=receipt.key)
    if existing is not None:
        return _verify_versioned_file(
            invoker,
            bucket=bucket,
            key=receipt.key,
            expected_body=receipt.body,
            expected_content_type=SHELL_RELEASE_RECEIPT_CONTENT_TYPE,
            expected_cache_control=SHELL_RELEASE_RECEIPT_CACHE_CONTROL,
            expected_server_side_encryption=SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
            receipt=existing,
        )
    with TemporaryDirectory(prefix="stockmarketagent-shell-receipt-") as temporary:
        body_path = Path(temporary).resolve() / "receipt.json"
        body_path.write_bytes(receipt.body)
        try:
            created = _object(
                invoker.json(
                    "s3api",
                    "put-object",
                    "--bucket",
                    bucket,
                    "--key",
                    receipt.key,
                    "--body",
                    str(body_path),
                    "--content-type",
                    SHELL_RELEASE_RECEIPT_CONTENT_TYPE,
                    "--server-side-encryption",
                    SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
                    "--cache-control",
                    SHELL_RELEASE_RECEIPT_CACHE_CONTROL,
                    "--metadata",
                    f"sha256={receipt.sha256}",
                    "--if-none-match",
                    "*",
                ),
                "put shell release receipt",
            )
        except subprocess.CalledProcessError:
            raced = _optional_head_object(invoker, bucket=bucket, key=receipt.key)
            if raced is None:
                raise
            created = raced
    return _verify_versioned_file(
        invoker,
        bucket=bucket,
        key=receipt.key,
        expected_body=receipt.body,
        expected_content_type=SHELL_RELEASE_RECEIPT_CONTENT_TYPE,
        expected_cache_control=SHELL_RELEASE_RECEIPT_CACHE_CONTROL,
        expected_server_side_encryption=SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
        receipt=created,
    )


def _verify_shell_receipts(
    invoker: AwsInvoker,
    *,
    bucket: str,
    receipts: list[tuple[str, bytes, dict[str, object], str, str]],
) -> None:
    for key, body, receipt, content_type, cache_control in receipts:
        _verify_versioned_file(
            invoker,
            bucket=bucket,
            key=key,
            expected_body=body,
            expected_content_type=content_type,
            expected_cache_control=cache_control,
            expected_server_side_encryption=SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
            receipt=receipt,
        )


def upgrade_existing_shell(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    old_product_sha: str,
    new_product_sha: str,
    repository_root: Path,
    snapshot_dir: Path,
    confirmation: str,
    builder: Callable[[Path, Path, str], Path] = build_public_demo,
) -> dict[str, str]:
    """Build and publish one receipt-bound shell from one clean reviewed checkout."""

    old_sha = _sha(old_product_sha)
    new_sha = _sha(new_product_sha)
    if old_sha == new_sha or confirmation != f"UPGRADE_EXISTING_SHELL:{old_sha}:{new_sha}":
        raise AwsSafetyError("shell upgrade requires its exact old-to-new confirmation")
    source_tree_sha = verify_shell_release_source(
        repository_root=repository_root,
        product_sha=new_sha,
    )
    with TemporaryDirectory(prefix="stockmarketagent-shell-upgrade-") as temporary:
        staging = Path(temporary).resolve()
        frozen_snapshot = _freeze_public_tree(
            snapshot_dir,
            staging / "snapshot",
            kind="snapshot",
        )
        built_artifact = builder(repository_root, frozen_snapshot, new_sha)
        frozen_artifact = _freeze_shell_artifact(
            built_artifact,
            staging / "artifact",
        )
        release_material = _build_shell_release_material(
            old_product_sha=old_sha,
            new_product_sha=new_sha,
            source_tree_sha=source_tree_sha,
            snapshot_dir=frozen_snapshot,
            artifact_dir=frozen_artifact,
        )
        confirmed_tree_sha = verify_shell_release_source(
            repository_root=repository_root,
            product_sha=new_sha,
        )
        if confirmed_tree_sha != source_tree_sha:
            raise AwsSafetyError("SHELL_RELEASE_SOURCE_TREE_CHANGED")
        return _upgrade_existing_shell_frozen(
            invoker,
            expected_account_id=expected_account_id,
            old_product_sha=old_sha,
            new_product_sha=new_sha,
            release_material=release_material,
            confirmation=confirmation,
        )


def _upgrade_existing_shell_frozen(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    old_product_sha: str,
    new_product_sha: str,
    release_material: _ShellReleaseMaterial,
    confirmation: str,
) -> dict[str, str]:
    """Perform the transition using only immutable captured artifact bytes."""

    old_sha = _sha(old_product_sha)
    new_sha = _sha(new_product_sha)
    expected_confirmation = f"UPGRADE_EXISTING_SHELL:{old_sha}:{new_sha}"
    if old_sha == new_sha or confirmation != expected_confirmation:
        raise AwsSafetyError("shell upgrade requires its exact old-to-new confirmation")
    local_material = _validated_shell_release_material(release_material)
    if (
        local_material.get("old_product_sha") != old_sha
        or local_material.get("new_product_sha") != new_sha
    ):
        raise AwsSafetyError("SHELL_RELEASE_TARGET_INVALID")
    artifact_files = release_material.artifact_files
    artifact_by_path = dict(artifact_files)
    metadata_body = artifact_by_path.get("build-metadata.json")
    if metadata_body is None:
        raise AwsSafetyError("live shell artifact inventory is incomplete")
    try:
        local_metadata = _object(json.loads(metadata_body), "artifact metadata")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AwsSafetyError("artifact metadata is invalid JSON") from error
    _validate_build_metadata(local_metadata, product_sha=new_sha)
    immutable_files: list[tuple[str, bytes]] = []
    mutable_files: list[tuple[str, bytes]] = []
    route_files: list[tuple[str, bytes]] = []
    for relative, body in artifact_files:
        if relative.startswith("data/"):
            continue
        if relative.startswith("ops/"):
            raise AwsSafetyError("artifact collides with the operator marker namespace")
        if relative.startswith("_next/static/"):
            immutable_files.append((relative, body))
        elif Path(relative).suffix.casefold() == ".html" or relative == "build-metadata.json":
            route_files.append((relative, body))
        else:
            mutable_files.append((relative, body))
    route_files.sort(
        key=lambda item: (
            Path(item[0]).name == "build-metadata.json",
            item[0],
        )
    )
    if (
        not immutable_files
        or not any(Path(relative).suffix.casefold() == ".html" for relative, _ in route_files)
        or not route_files
        or route_files[-1][0] != "build-metadata.json"
    ):
        raise AwsSafetyError("live shell artifact inventory is incomplete")
    shell_manifest = [
        {
            "path": relative,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "cache_class": cache_class,
        }
        for files, cache_class in (
            (immutable_files, "IMMUTABLE"),
            (mutable_files, "MUTABLE"),
            (route_files, "ROUTE"),
        )
        for relative, body in files
    ]
    shell_artifact_sha256 = hashlib.sha256(
        json.dumps(
            shell_manifest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    invalidation_paths = _route_invalidation_paths([relative for relative, _body in route_files])
    invalidation_batch_sha256 = hashlib.sha256(
        json.dumps(invalidation_paths, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    _identity(invoker, expected_account_id)
    stack = verify_existing_stack(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
    )
    _verify_deployed_resources(
        invoker,
        stack,
        expected_account_id=expected_account_id,
        expected_product_sha=new_sha,
    )
    outputs = _outputs(stack)
    bucket = outputs["BucketName"]
    stack_id = stack.get("StackId")
    if not isinstance(stack_id, str):
        raise AwsSafetyError("SHELL_RELEASE_TARGET_INVALID")
    release_receipt = _build_shell_release_receipt(
        material=release_material,
        expected_old_product_sha=old_sha,
        expected_new_product_sha=new_sha,
        expected_account_id=expected_account_id,
        stack_id=stack_id,
        bucket=bucket,
        distribution_id=outputs["DistributionId"],
    )
    receipt_identity = {
        "release-receipt-key": release_receipt.key,
        "release-receipt-sha256": release_receipt.sha256,
        "intent-sha256": release_receipt.intent_sha256,
    }
    marker = _optional_head_object(
        invoker,
        bucket=bucket,
        key=SHELL_UPGRADE_MARKER_KEY,
    )
    deployed_metadata = _remote_build_metadata(invoker, bucket=bucket)
    if marker is None:
        _validate_build_metadata(deployed_metadata, product_sha=old_sha)
        receipt_head = _ensure_shell_release_receipt(
            invoker,
            bucket=bucket,
            receipt=release_receipt,
        )
        _, receipt_version_id = _versioned_head(receipt_head, key=release_receipt.key)
        receipt_binding = {
            **receipt_identity,
            "release-receipt-version-id": receipt_version_id,
        }
        marker_metadata = {
            "state": "IN_PROGRESS",
            "old-product-sha": old_sha,
            "new-product-sha": new_sha,
            "invalidation-batch-sha256": invalidation_batch_sha256,
            "shell-artifact-sha256": shell_artifact_sha256,
            **receipt_binding,
        }
        marker = _put_marker(
            invoker,
            bucket=bucket,
            metadata=marker_metadata,
            if_match=None,
        )
    else:
        marker_metadata = _metadata(marker, label="upgrade marker metadata")
        if marker_metadata.get("state") == "COMPLETE":
            raise AwsSafetyError("SHELL_UPGRADE_ALREADY_COMPLETE")
        receipt_version_id = marker_metadata.get("release-receipt-version-id", "")
        if (
            marker_metadata.get("state") != "IN_PROGRESS"
            or not receipt_version_id
            or any(marker_metadata.get(key) != value for key, value in receipt_identity.items())
        ):
            raise AwsSafetyError("SHELL_UPGRADE_TARGET_MISMATCH")
        exact_receipt = _optional_head_object(
            invoker,
            bucket=bucket,
            key=release_receipt.key,
            version_id=receipt_version_id,
        )
        if exact_receipt is None:
            raise AwsSafetyError("SHELL_RELEASE_RECEIPT_VERSION_MISSING")
        receipt_head = _verify_versioned_file(
            invoker,
            bucket=bucket,
            key=release_receipt.key,
            expected_body=release_receipt.body,
            expected_content_type=SHELL_RELEASE_RECEIPT_CONTENT_TYPE,
            expected_cache_control=SHELL_RELEASE_RECEIPT_CACHE_CONTROL,
            expected_server_side_encryption=SHELL_OBJECT_SERVER_SIDE_ENCRYPTION,
            receipt=exact_receipt,
        )
        receipt_binding = {
            **receipt_identity,
            "release-receipt-version-id": receipt_version_id,
        }
        expected_pair = {
            "old-product-sha": old_sha,
            "new-product-sha": new_sha,
            "invalidation-batch-sha256": invalidation_batch_sha256,
            "shell-artifact-sha256": shell_artifact_sha256,
            **receipt_binding,
        }
        if any(marker_metadata.get(key) != value for key, value in expected_pair.items()) or set(
            marker_metadata
        ) - {*expected_pair, "state", "invalidation-id"}:
            raise AwsSafetyError("SHELL_UPGRADE_TARGET_MISMATCH")
        if deployed_metadata.get("product_commit") not in {old_sha, new_sha}:
            raise AwsSafetyError("deployed build metadata is outside the resumable upgrade")
        if deployed_metadata.get("project") != "StockMarketAgent":
            raise AwsSafetyError("deployed build metadata is outside the resumable upgrade")

    receipts: list[tuple[str, bytes, dict[str, object], str, str]] = [
        (
            release_receipt.key,
            release_receipt.body,
            receipt_head,
            SHELL_RELEASE_RECEIPT_CONTENT_TYPE,
            SHELL_RELEASE_RECEIPT_CACHE_CONTROL,
        )
    ]
    _verify_shell_receipts(invoker, bucket=bucket, receipts=receipts)
    for relative, body in immutable_files:
        receipt = _ensure_versioned_file(
            invoker,
            bucket=bucket,
            relative=relative,
            body=body,
            immutable=True,
        )
        content_type, cache_control = _shell_object_policy(relative, immutable=True)
        receipts.append((relative, body, receipt, content_type, cache_control))
    _verify_shell_receipts(invoker, bucket=bucket, receipts=receipts)
    for relative, body in (*mutable_files, *route_files):
        receipt = _ensure_versioned_file(
            invoker,
            bucket=bucket,
            relative=relative,
            body=body,
            immutable=False,
        )
        content_type, cache_control = _shell_object_policy(relative, immutable=False)
        receipts.append((relative, body, receipt, content_type, cache_control))
    _validate_build_metadata(_remote_build_metadata(invoker, bucket=bucket), product_sha=new_sha)
    _verify_shell_receipts(invoker, bucket=bucket, receipts=receipts)

    marker_metadata = _metadata(marker, label="upgrade marker metadata")
    invalidation_id = marker_metadata.get("invalidation-id")
    if invalidation_id is None:
        caller_reference = (
            f"stockmarketagent-shell-upgrade-{new_sha[:12]}-{shell_artifact_sha256[:32]}"
        )
        invalidation = _object(
            invoker.json(
                "cloudfront",
                "create-invalidation",
                "--distribution-id",
                outputs["DistributionId"],
                "--invalidation-batch",
                json.dumps(
                    {
                        "CallerReference": caller_reference,
                        "Paths": {
                            "Quantity": len(invalidation_paths),
                            "Items": list(invalidation_paths),
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
            "create invalidation response",
        )
        created_invalidation_id = _object(
            invalidation.get("Invalidation"),
            "invalidation",
        ).get("Id")
        if not isinstance(created_invalidation_id, str) or not created_invalidation_id:
            raise AwsSafetyError("CloudFront invalidation response omitted its Id")
        invalidation_id = created_invalidation_id
        marker_etag, _ = _versioned_head(marker, key=SHELL_UPGRADE_MARKER_KEY)
        marker_metadata = {**marker_metadata, "invalidation-id": invalidation_id}
        marker = _put_marker(
            invoker,
            bucket=bucket,
            metadata=marker_metadata,
            if_match=marker_etag,
        )
    invoker.text(
        "cloudfront",
        "wait",
        "invalidation-completed",
        "--distribution-id",
        outputs["DistributionId"],
        "--id",
        invalidation_id,
    )
    _verify_shell_receipts(invoker, bucket=bucket, receipts=receipts)
    marker_etag, _ = _versioned_head(marker, key=SHELL_UPGRADE_MARKER_KEY)
    complete_metadata = {**marker_metadata, "state": "COMPLETE"}
    completed = _put_marker(
        invoker,
        bucket=bucket,
        metadata=complete_metadata,
        if_match=marker_etag,
    )
    _, completed_version = _versioned_head(completed, key=SHELL_UPGRADE_MARKER_KEY)
    _verify_shell_receipts(invoker, bucket=bucket, receipts=receipts)
    return {
        **outputs,
        "InvalidationId": invalidation_id,
        "ShellReleaseReceiptVersionId": receipt_version_id,
        "ShellUpgradeMarkerVersionId": completed_version,
    }


def _validate_https_target(url: str, *, expected_host: str) -> None:
    try:
        target = urlsplit(url)
        port = target.port
    except ValueError as exc:
        raise AwsSafetyError("public smoke target is malformed") from exc
    if (
        target.scheme.casefold() != "https"
        or target.hostname is None
        or target.hostname.casefold() != expected_host.casefold()
        or target.username is not None
        or target.password is not None
        or port is not None
    ):
        raise AwsSafetyError("public smoke target leaves its exact HTTPS host")


class _RejectRedirects(HTTPRedirectHandler):
    """Never follow redirects and reject cross-host locations before another request."""

    def __init__(self, expected_host: str) -> None:
        super().__init__()
        self.expected_host = expected_host

    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del file_pointer, code, message, headers
        _validate_https_target(
            urljoin(request.full_url, new_url),
            expected_host=self.expected_host,
        )
        return None


def _https_get(url: str, *, expected_host: str) -> tuple[int, bytes, dict[str, str]]:
    _validate_https_target(url, expected_host=expected_host)
    request = Request(  # noqa: S310 - the HTTPS scheme was checked immediately above.
        url,
        headers={"User-Agent": "StockMarketAgent-public-demo-smoke/1"},
    )
    opener = build_opener(_RejectRedirects(expected_host))
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, response.read(2 * 1024 * 1024), dict(response.headers.items())
    except HTTPError as exc:
        return exc.code, exc.read(2 * 1024 * 1024), dict(exc.headers.items())
    except URLError as exc:
        raise AwsSafetyError(f"public demo smoke failed: {exc.reason}") from exc


def _canonical_evidence_id(value: str) -> str:
    try:
        canonical = str(UUID(value))
    except (AttributeError, ValueError) as exc:
        raise AwsSafetyError("evidence ID must be a canonical UUID") from exc
    if value != canonical:
        raise AwsSafetyError("evidence ID must be one lowercase UUID path segment")
    return canonical


def smoke(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    evidence_id: str,
) -> None:
    canonical_evidence_id = _canonical_evidence_id(evidence_id)
    _identity(invoker, expected_account_id)
    stack = verify_existing_stack(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
    )
    _verify_deployed_resources(
        invoker,
        stack,
        expected_account_id=expected_account_id,
        expected_product_sha=product_sha,
    )
    outputs = _outputs(stack)
    if outputs["ProductSha"] != _sha(product_sha):
        raise AwsSafetyError("deployed stack ProductSha differs from the smoke target")
    distribution_domain = outputs["DistributionDomainName"]
    base = outputs["PublicDemoUrl"]
    if base != f"https://{distribution_domain}":
        raise AwsSafetyError("PublicDemoUrl does not exactly match the deployed distribution")
    _validate_https_target(base, expected_host=distribution_domain)
    evidence_path = f"/evidence/{canonical_evidence_id}/"
    details_marker = "为什么得到这个结果".encode()
    evidence_marker = "正在检查这条证据".encode()
    for path, expected in (
        ("/", 200),
        ("/details/", 200),
        (evidence_path, 200),
        ("/missing/", 404),
    ):
        status, body, headers = _https_get(
            f"{base}{path}",
            expected_host=distribution_domain,
        )
        if status != expected or not body:
            raise AwsSafetyError(f"unexpected public response for {path}: {status}")
        if path == "/details/" and details_marker not in body:
            raise AwsSafetyError("details page marker is missing")
        if path == evidence_path and evidence_marker not in body:
            raise AwsSafetyError("evidence page loader marker is missing")
        folded_headers = {key.casefold(): value for key, value in headers.items()}
        csp = folded_headers.get("content-security-policy", "")
        hsts = folded_headers.get("strict-transport-security", "").casefold()
        if (
            folded_headers.get("x-content-type-options", "").casefold() != "nosniff"
            or "object-src 'none'" not in csp
            or "frame-ancestors 'none'" not in csp
            or "max-age=31536000" not in hsts
            or "includesubdomains" not in hsts
            or "preload" not in hsts
        ):
            raise AwsSafetyError("CloudFront security headers are missing")
    status, metadata_body, _ = _https_get(
        f"{base}/build-metadata.json",
        expected_host=distribution_domain,
    )
    metadata = _object(json.loads(metadata_body), "deployed build metadata")
    if status != 200 or metadata.get("product_commit") != product_sha:
        raise AwsSafetyError("deployed build metadata SHA is wrong")
    bucket_url = f"https://{outputs['BucketName']}.s3.{REGION}.amazonaws.com/index.html"
    direct_status, _, _ = _https_get(
        bucket_url,
        expected_host=f"{outputs['BucketName']}.s3.{REGION}.amazonaws.com",
    )
    if direct_status != 403:
        raise AwsSafetyError("direct S3 access is not private")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-account-id", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--product-sha", required=True)
    plan_parser.add_argument("--template", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--api-origin-domain", default="")
    plan_parser.add_argument("--api-origin-secret-arn", default="")
    plan_parser.add_argument("--cognito-domain", default="")
    plan_parser.add_argument("--api-no-cache-policy-id", default="")
    plan_parser.add_argument("--api-origin-request-policy-id", default="")
    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--plan", type=Path, required=True)
    deploy_parser.add_argument("--change-set-arn", required=True)
    deploy_parser.add_argument("--plan-sha256", required=True)
    deploy_parser.add_argument("--confirm", required=True)
    deploy_parser.add_argument("--template", type=Path, required=True)
    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("--product-sha", required=True)
    upload_parser.add_argument("--artifact-dir", type=Path, required=True)
    upload_parser.add_argument("--confirm", required=True)
    upgrade_parser = subparsers.add_parser("upgrade-existing-shell")
    upgrade_parser.add_argument("--old-product-sha", required=True)
    upgrade_parser.add_argument("--new-product-sha", required=True)
    upgrade_parser.add_argument("--repository-root", type=Path, required=True)
    upgrade_parser.add_argument("--snapshot-dir", type=Path, required=True)
    upgrade_parser.add_argument("--confirm", required=True)
    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--product-sha", required=True)
    smoke_parser.add_argument("--evidence-id", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        cli = AwsCli(locate_aws_cli())
        verify_cli_v2(
            subprocess.run(  # noqa: S603 - exact AWS CLI executable and flag only.
                [cli.executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        )
        if args.command == "plan":
            plan_result = plan(
                cli,
                expected_account_id=args.expected_account_id,
                product_sha=args.product_sha,
                template=args.template,
                output=args.output,
                api_origin_domain=args.api_origin_domain,
                api_origin_secret_arn=args.api_origin_secret_arn,
                cognito_domain=args.cognito_domain,
                api_no_cache_policy_id=args.api_no_cache_policy_id,
                api_origin_request_policy_id=args.api_origin_request_policy_id,
            )
            print(json.dumps(plan_result, indent=2, sort_keys=True))
        elif args.command == "deploy":
            deploy(
                cli,
                expected_account_id=args.expected_account_id,
                plan_path=args.plan,
                change_set_arn=args.change_set_arn,
                plan_sha256=args.plan_sha256,
                confirmation=args.confirm,
                template=args.template,
            )
        elif args.command == "upload":
            upload_result = upload(
                cli,
                expected_account_id=args.expected_account_id,
                product_sha=args.product_sha,
                artifact_dir=args.artifact_dir,
                confirmation=args.confirm,
            )
            print(json.dumps(upload_result, indent=2, sort_keys=True))
        elif args.command == "upgrade-existing-shell":
            upgrade_result = upgrade_existing_shell(
                cli,
                expected_account_id=args.expected_account_id,
                old_product_sha=args.old_product_sha,
                new_product_sha=args.new_product_sha,
                repository_root=args.repository_root,
                snapshot_dir=args.snapshot_dir,
                confirmation=args.confirm,
            )
            print(json.dumps(upgrade_result, indent=2, sort_keys=True))
        else:
            smoke(
                cli,
                expected_account_id=args.expected_account_id,
                product_sha=args.product_sha,
                evidence_id=args.evidence_id,
            )
    except (
        AwsSafetyError,
        PublicArtifactError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        print(f"AWS public demo blocked: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

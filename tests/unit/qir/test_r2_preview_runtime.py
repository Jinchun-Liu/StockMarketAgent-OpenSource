"""Focused tests for managed runtime URLs and non-destructive Web releases."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from scripts.r2_preview_aws import (
    PreviewAwsError,
    _large_template_reference,
    _normalize_changes,
    _parameters,
    _verify_large_template_reference,
    locate_aws_cli,
    verify_identity,
    verify_image_binding,
)
from scripts.r2_preview_release import (
    _build_metadata,
    _invalidation_paths,
    _ordered,
    _write_build_metadata,
    inventory,
    publish,
)
from sqlalchemy import make_url

from stock_assistant.platform.runtime import (
    database_url_from_environment,
    valkey_urls_from_environment,
)


def test_database_url_uses_separately_injected_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_HOST", "database.internal")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "stock_assistant")
    monkeypatch.setenv("DB_USERNAME", "runtime")
    monkeypatch.setenv("DB_PASSWORD", "synthetic pass")
    result = database_url_from_environment("postgresql+asyncpg://unused")
    parsed = make_url(result)
    assert parsed.username == "runtime"
    assert parsed.password == "synthetic pass"  # noqa: S105 - deterministic synthetic fixture.
    assert parsed.host == "database.internal"
    assert parsed.database == "stock_assistant"
    assert parsed.query == {"ssl": "require"}


def test_valkey_url_requires_complete_tls_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALKEY_HOST", "queue.internal")
    monkeypatch.setenv("VALKEY_PORT", "6379")
    monkeypatch.setenv("VALKEY_AUTH_TOKEN", "synthetic token")
    result = valkey_urls_from_environment(
        default_redis="redis://local/0",
        default_broker="redis://local/1",
        default_result_backend="redis://local/2",
    )
    assert result.broker == (
        "rediss://:synthetic%20token@queue.internal:6379/1?ssl_cert_reqs=required"
    )
    assert result.result_backend.endswith("/2?ssl_cert_reqs=required")


def test_managed_url_helpers_fail_closed_and_preserve_local_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert database_url_from_environment("postgresql+asyncpg://local/db") == (
        "postgresql+asyncpg://local/db"
    )
    defaults = valkey_urls_from_environment(
        default_redis="redis://local/0",
        default_broker="redis://local/1",
        default_result_backend="redis://local/2",
    )
    assert defaults.broker == "redis://local/1"
    monkeypatch.setenv("VALKEY_HOST", "queue.internal")
    with pytest.raises(RuntimeError, match="VALKEY_CONFIGURATION_INCOMPLETE"):
        valkey_urls_from_environment(
            default_redis="redis://local/0",
            default_broker="redis://local/1",
            default_result_backend="redis://local/2",
        )
    monkeypatch.setenv("VALKEY_PORT", "not-a-port")
    monkeypatch.setenv("VALKEY_AUTH_TOKEN", "synthetic-token")
    with pytest.raises(RuntimeError, match="VALKEY_PORT_INVALID"):
        valkey_urls_from_environment(
            default_redis="redis://local/0",
            default_broker="redis://local/1",
            default_result_backend="redis://local/2",
        )


def test_database_url_rejects_partial_and_invalid_managed_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_HOST", "database.internal")
    with pytest.raises(RuntimeError, match="DATABASE_CONFIGURATION_INCOMPLETE"):
        database_url_from_environment("postgresql+asyncpg://local/db")

    monkeypatch.setenv("DB_PORT", "not-a-port")
    monkeypatch.setenv("DB_NAME", "stock_assistant")
    monkeypatch.setenv("DB_USERNAME", "runtime")
    monkeypatch.setenv("DB_PASSWORD", "synthetic pass")
    with pytest.raises(RuntimeError, match="DATABASE_PORT_INVALID"):
        database_url_from_environment("postgresql+asyncpg://local/db")


@pytest.mark.parametrize("port", ["0", "65536"])
def test_valkey_url_rejects_out_of_range_port(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("VALKEY_HOST", "queue.internal")
    monkeypatch.setenv("VALKEY_PORT", port)
    monkeypatch.setenv("VALKEY_AUTH_TOKEN", "synthetic-token")
    with pytest.raises(RuntimeError, match="VALKEY_PORT_INVALID"):
        valkey_urls_from_environment(
            default_redis="redis://local/0",
            default_broker="redis://local/1",
            default_result_backend="redis://local/2",
        )


def test_preview_parameters_fail_closed() -> None:
    with pytest.raises(PreviewAwsError, match="ACTIVATION"):
        _parameters(
            product_sha="a" * 40,
            image_digest="",
            desired_count=1,
            public_base_url="https://preview.example/",
            cognito_domain_prefix="stockmarketagent-r2",
            prefix_list_id="pl-12345678",
            s3_prefix_list_id="pl-87654321",
        )
    with pytest.raises(PreviewAwsError, match="MUST_END_WITH_SLASH"):
        _parameters(
            product_sha="a" * 40,
            image_digest="sha256:" + "b" * 64,
            desired_count=0,
            public_base_url="https://preview.example",
            cognito_domain_prefix="stockmarketagent-r2",
            prefix_list_id="pl-12345678",
            s3_prefix_list_id="pl-87654321",
        )


def test_change_set_rejects_remove_and_unreviewed_replacement() -> None:
    with pytest.raises(PreviewAwsError, match="DELETE_FORBIDDEN"):
        _normalize_changes(
            {
                "Changes": [
                    {
                        "ResourceChange": {
                            "Action": "Remove",
                            "LogicalResourceId": "ResearchDatabase",
                            "ResourceType": "AWS::RDS::DBInstance",
                        }
                    }
                ]
            }
        )

    for action, resource_type in (
        ("Modify", "AWS::RDS::DBInstance"),
        ("Add", "AWS::EC2::SecurityGroupIngress"),
    ):
        with pytest.raises(PreviewAwsError, match="UNREVIEWED_REPLACEMENT"):
            _normalize_changes(
                {
                    "Changes": [
                        {
                            "ResourceChange": {
                                "Action": action,
                                "LogicalResourceId": "ApiIngressFromLoadBalancer",
                                "ResourceType": resource_type,
                                "Replacement": "True",
                            }
                        }
                    ]
                }
            )

    with pytest.raises(PreviewAwsError, match="UNREVIEWED_REPLACEMENT"):
        _normalize_changes(
            {
                "Changes": [
                    {
                        "ResourceChange": {
                            "Action": "Modify",
                            "LogicalResourceId": "LoadBalancerEgressToApi",
                            "ResourceType": "AWS::EC2::SecurityGroup",
                            "Replacement": "Conditional",
                        }
                    }
                ]
            }
        )
    with pytest.raises(PreviewAwsError, match="UNREVIEWED_REPLACEMENT"):
        _normalize_changes(
            {
                "Changes": [
                    {
                        "ResourceChange": {
                            "Action": "Modify",
                            "LogicalResourceId": "ApiLoadBalancer",
                            "ResourceType": "AWS::ElasticLoadBalancingV2::LoadBalancer",
                            "Replacement": "True",
                        }
                    }
                ]
            }
        )


def test_change_set_allows_only_the_reviewed_legacy_security_rule_removals() -> None:
    changes = _normalize_changes(
        {
            "Changes": [
                {
                    "ResourceChange": {
                        "Action": "Remove",
                        "LogicalResourceId": "TaskHttpsEgress",
                        "ResourceType": "AWS::EC2::SecurityGroupEgress",
                    }
                },
                {
                    "ResourceChange": {
                        "Action": "Modify",
                        "LogicalResourceId": "ApiIngressFromLoadBalancer",
                        "ResourceType": "AWS::EC2::SecurityGroupIngress",
                        "Replacement": "True",
                    }
                },
            ]
        }
    )
    assert changes == [
        {
            "action": "Modify",
            "logical_id": "ApiIngressFromLoadBalancer",
            "replacement": "True",
            "resource_type": "AWS::EC2::SecurityGroupIngress",
        },
        {
            "action": "Remove",
            "logical_id": "TaskHttpsEgress",
            "replacement": "False",
            "resource_type": "AWS::EC2::SecurityGroupEgress",
        },
    ]

    with pytest.raises(PreviewAwsError, match="DELETE_FORBIDDEN"):
        _normalize_changes(
            {
                "Changes": [
                    {
                        "ResourceChange": {
                            "Action": "Remove",
                            "LogicalResourceId": "TaskHttpsEgress",
                            "ResourceType": "AWS::EC2::SecurityGroup",
                        }
                    }
                ]
            }
        )


def test_identity_is_exact_account_bound() -> None:
    verify_identity(
        {
            "Account": "123456789012",
            "Arn": "arn:aws:sts::123456789012:assumed-role/operator/session",
        },
        "123456789012",
    )
    with pytest.raises(PreviewAwsError, match="MISMATCH"):
        verify_identity(
            {
                "Account": "210987654321",
                "Arn": "arn:aws:sts::210987654321:assumed-role/operator/session",
            },
            "123456789012",
        )


class _ImageReadbackFixture:
    def __init__(self, detail: dict[str, object]) -> None:
        self.detail = detail

    def json(self, service: str, operation: str, *arguments: str) -> object:
        assert (service, operation) == ("ecr", "describe-images")
        assert "--repository-name" in arguments
        return {"imageDetails": [self.detail]}

    def text(self, service: str, operation: str, *arguments: str) -> str:
        del service, operation, arguments
        raise AssertionError("image verification is read-only JSON")


def test_image_digest_must_be_bound_to_the_exact_candidate_tag() -> None:
    account = "1" * 12
    product_sha = "a" * 40
    image_digest = "sha256:" + "b" * 64
    valid = {
        "registryId": account,
        "repositoryName": "stockmarketagent-r2-preview",
        "imageDigest": image_digest,
        "imageTags": [product_sha],
    }
    assert (
        verify_image_binding(
            _ImageReadbackFixture(valid),
            expected_account_id=account,
            product_sha=product_sha,
            image_digest=image_digest,
        )
        == valid
    )

    with pytest.raises(PreviewAwsError, match="ECR_IMAGE_SHA_DIGEST_MISMATCH"):
        verify_image_binding(
            _ImageReadbackFixture({**valid, "imageTags": ["c" * 40]}),
            expected_account_id=account,
            product_sha=product_sha,
            image_digest=image_digest,
        )


class _TemplateUploadFixture:
    def __init__(self, *, mismatch: bool = False) -> None:
        self.mismatch = mismatch
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def json(self, service: str, operation: str, *arguments: str) -> object:
        self.calls.append((service, operation, arguments))
        if (service, operation) == ("s3api", "put-object"):
            return {"VersionId": "synthetic-version"}
        if (service, operation) == ("s3api", "head-object"):
            return {
                "VersionId": "synthetic-version",
                "ContentLength": 60_000 if not self.mismatch else 1,
                "ServerSideEncryption": "AES256",
                "ChecksumSHA256": base64.b64encode(hashlib.sha256(b"x" * 60_000).digest()).decode(
                    "ascii"
                ),
                "Metadata": {
                    "productsha": "a" * 40,
                    "templatesha256": hashlib.sha256(b"x" * 60_000).hexdigest(),
                },
            }
        raise AssertionError(f"unexpected call: {service} {operation}")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        del service, operation, arguments
        raise AssertionError("template publication uses structured AWS responses")


def test_large_template_is_uploaded_and_read_back_by_exact_s3_version(tmp_path: Path) -> None:
    template = tmp_path.resolve() / "runtime.yaml"
    template_bytes = b"x" * 60_000
    template.write_bytes(template_bytes)
    template_sha256 = hashlib.sha256(template_bytes).hexdigest()
    invoker = _TemplateUploadFixture()

    argument, source = _large_template_reference(
        invoker,
        existing_stack={
            "Outputs": [
                {
                    "OutputKey": "ArtifactBucketName",
                    "OutputValue": "synthetic-r2-artifacts",
                }
            ]
        },
        product_sha="a" * 40,
        template=template,
        template_bytes=template_bytes,
        template_sha256=template_sha256,
    )

    assert argument[0] == "--template-url"
    assert argument[1].endswith("?versionId=synthetic-version")
    assert source == {
        "type": "s3_version",
        "bucket": "synthetic-r2-artifacts",
        "key": f"releases/{'a' * 40}/deployment-templates/{template_sha256}.yaml",
        "version_id": "synthetic-version",
        "sha256": template_sha256,
        "product_sha": "a" * 40,
    }
    put_arguments = invoker.calls[0][2]
    assert put_arguments[put_arguments.index("--body") + 1] == str(template)
    assert invoker.calls[1][:2] == ("s3api", "head-object")


def test_large_template_requires_existing_bucket_and_exact_readback(tmp_path: Path) -> None:
    template = tmp_path.resolve() / "runtime.yaml"
    template_bytes = b"x" * 60_000
    template.write_bytes(template_bytes)
    template_sha256 = hashlib.sha256(template_bytes).hexdigest()
    invoker = _TemplateUploadFixture(mismatch=True)

    with pytest.raises(PreviewAwsError, match="EXISTING_ARTIFACT_BUCKET"):
        _large_template_reference(
            invoker,
            existing_stack=None,
            product_sha="a" * 40,
            template=template,
            template_bytes=template_bytes,
            template_sha256=template_sha256,
        )
    assert invoker.calls == []

    with pytest.raises(PreviewAwsError, match="READBACK_MISMATCH"):
        _verify_large_template_reference(
            invoker,
            source={
                "type": "s3_version",
                "bucket": "synthetic-r2-artifacts",
                "key": f"releases/{'a' * 40}/deployment-templates/{template_sha256}.yaml",
                "version_id": "synthetic-version",
                "sha256": template_sha256,
                "product_sha": "a" * 40,
            },
            expected_size=len(template_bytes),
        )


def test_aws_cli_can_be_bound_to_a_verified_explicit_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "aws"
    executable.write_text("verified test fixture", encoding="utf-8")
    monkeypatch.setenv("STOCK_ASSISTANT_AWS_CLI", str(executable))
    assert locate_aws_cli() == str(executable.resolve())


class _ReleaseInvokerFixture:
    def __init__(self) -> None:
        self.calls = 0

    def json(self, service: str, operation: str, *arguments: str) -> object:
        del service, operation, arguments
        self.calls += 1
        if self.calls == 1:
            return {}
        raise AssertionError("release changed remote state before identity verification")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        del service, operation, arguments
        raise AssertionError("release waited on remote state before identity verification")


def test_release_fails_before_mutation_for_an_unexpected_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def reject(_payload: object, _expected: str) -> dict[str, object]:
        raise PreviewAwsError("SYNTHETIC_IDENTITY_REJECTED")

    monkeypatch.setattr("scripts.r2_preview_release.verify_identity", reject)
    invoker = _ReleaseInvokerFixture()
    with pytest.raises(PreviewAwsError, match="SYNTHETIC_IDENTITY_REJECTED"):
        publish(
            invoker,
            expected_account_id="synthetic",
            product_sha="a" * 40,
            artifact_dir=tmp_path.resolve(),
            confirmation="PUBLISH_R2_WEB",
        )
    assert invoker.calls == 1


def test_release_inventory_versions_assets_and_switches_html_last(tmp_path: Path) -> None:
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "a" * 40}), encoding="utf-8"
    )
    artifact.joinpath("build-metadata.json").write_text(
        json.dumps(_build_metadata(product_sha="a" * 40)), encoding="utf-8"
    )
    static.joinpath("app.js").write_text("app", encoding="utf-8")
    objects = inventory(artifact, product_sha="a" * 40)
    names = [item.key for item in _ordered(objects)]
    assert names[0] == "assets/releases/" + "a" * 40 + "/_next/static/app.js"
    assert names[-3:] == ["runtime-config.json", "index.html", "build-metadata.json"]
    config = next(item for item in objects if item.key == "runtime-config.json")
    metadata = next(item for item in objects if item.key == "build-metadata.json")
    assert config.cache_control == "no-store,max-age=0"
    assert metadata.cache_control == "no-store,max-age=0"
    assert "/build-metadata.json" in _invalidation_paths()


def test_release_build_preparation_writes_exact_metadata(tmp_path: Path) -> None:
    artifact = tmp_path.resolve()

    result = _write_build_metadata(artifact, product_sha="a" * 40)

    assert result == _build_metadata(product_sha="a" * 40)
    assert (
        json.loads(artifact.joinpath("build-metadata.json").read_text(encoding="utf-8")) == result
    )


def test_release_inventory_rejects_missing_build_metadata(tmp_path: Path) -> None:
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "a" * 40}), encoding="utf-8"
    )
    static.joinpath("app.js").write_text("app", encoding="utf-8")

    with pytest.raises(PreviewAwsError, match="R2_WEB_ARTIFACT_INCOMPLETE"):
        inventory(artifact, product_sha="a" * 40)


@pytest.mark.parametrize(
    "metadata",
    [
        {**_build_metadata(product_sha="a" * 40), "product_commit": "b" * 40},
        {**_build_metadata(product_sha="a" * 40), "project": "AnotherProject"},
        {**_build_metadata(product_sha="a" * 40), "runtime_mode": "PUBLIC_STATIC"},
    ],
)
def test_release_inventory_rejects_stale_build_metadata(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "a" * 40}), encoding="utf-8"
    )
    artifact.joinpath("build-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    static.joinpath("app.js").write_text("app", encoding="utf-8")

    with pytest.raises(PreviewAwsError, match="R2_WEB_BUILD_METADATA_INVALID"):
        inventory(artifact, product_sha="a" * 40)


def test_release_inventory_rejects_malformed_build_metadata(tmp_path: Path) -> None:
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "a" * 40}), encoding="utf-8"
    )
    artifact.joinpath("build-metadata.json").write_text("{", encoding="utf-8")
    static.joinpath("app.js").write_text("app", encoding="utf-8")

    with pytest.raises(PreviewAwsError, match="R2_WEB_BUILD_METADATA_INVALID"):
        inventory(artifact, product_sha="a" * 40)


def test_release_inventory_rejects_runtime_config_for_another_sha(tmp_path: Path) -> None:
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "b" * 40}), encoding="utf-8"
    )
    artifact.joinpath("build-metadata.json").write_text(
        json.dumps(_build_metadata(product_sha="a" * 40)), encoding="utf-8"
    )
    static.joinpath("app.js").write_text("app", encoding="utf-8")

    with pytest.raises(PreviewAwsError, match="R2_WEB_RUNTIME_CONFIG_INVALID"):
        inventory(artifact, product_sha="a" * 40)


class _ArtifactValidationFixture:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []

    def json(self, service: str, operation: str, *arguments: str) -> object:
        del arguments
        self.operations.append((service, operation))
        if (service, operation) == ("sts", "get-caller-identity"):
            return {}
        raise AssertionError(f"unexpected release operation: {service} {operation}")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        del service, operation, arguments
        raise AssertionError("release waited before artifact validation")


@pytest.mark.parametrize(
    ("metadata", "error_code"),
    [
        (None, "R2_WEB_ARTIFACT_INCOMPLETE"),
        ("{", "R2_WEB_BUILD_METADATA_INVALID"),
        (
            json.dumps({**_build_metadata(product_sha="a" * 40), "product_commit": "b" * 40}),
            "R2_WEB_BUILD_METADATA_INVALID",
        ),
    ],
)
def test_publish_rejects_invalid_build_metadata_before_aws_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata: str | None,
    error_code: str,
) -> None:
    def accept_identity(_payload: object, _expected: str) -> dict[str, object]:
        return {}

    def outputs(_invoker: object, *, product_sha: str) -> dict[str, str]:
        assert product_sha == "a" * 40
        return {
            "BucketName": "preview",
            "DistributionId": "distribution",
            "PublicDemoUrl": "https://preview.invalid",
        }

    monkeypatch.setattr("scripts.r2_preview_release.verify_identity", accept_identity)
    monkeypatch.setattr("scripts.r2_preview_release._stack_outputs", outputs)
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "a" * 40}), encoding="utf-8"
    )
    static.joinpath("app.js").write_text("app", encoding="utf-8")
    if metadata is not None:
        artifact.joinpath("build-metadata.json").write_text(metadata, encoding="utf-8")
    invoker = _ArtifactValidationFixture()

    with pytest.raises(PreviewAwsError, match=error_code):
        publish(
            invoker,
            expected_account_id="synthetic",
            product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation="PUBLISH_R2_WEB",
        )

    assert all(service not in {"s3api", "cloudfront"} for service, _ in invoker.operations)


# Versioned uploads must be confirmed before invalidation.
class _UploadReceiptFixture:
    def __init__(self, receipt: object) -> None:
        self.receipt = receipt
        self.operations: list[tuple[str, str]] = []

    def json(self, service: str, operation: str, *arguments: str) -> object:
        del arguments
        self.operations.append((service, operation))
        if (service, operation) == ("sts", "get-caller-identity"):
            return {}
        if (service, operation) == ("s3api", "put-object"):
            return {"VersionId": self.receipt}
        raise AssertionError(f"unexpected release operation: {service} {operation}")

    def text(self, service: str, operation: str, *arguments: str) -> str:
        del service, operation, arguments
        raise AssertionError("release waited after an unconfirmed versioned upload")


@pytest.mark.parametrize("receipt", [None, "", "   "])
def test_release_rejects_unconfirmed_version_before_invalidation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, receipt: object
) -> None:
    def accept_identity(_payload: object, _expected: str) -> dict[str, object]:
        return {}

    def outputs(_invoker: object, *, product_sha: str) -> dict[str, str]:
        assert product_sha == "a" * 40
        return {"BucketName": "preview"}

    monkeypatch.setattr("scripts.r2_preview_release.verify_identity", accept_identity)
    monkeypatch.setattr("scripts.r2_preview_release._stack_outputs", outputs)
    artifact = tmp_path.resolve()
    static = artifact.joinpath("_next", "static")
    static.mkdir(parents=True)
    artifact.joinpath("index.html").write_text("index", encoding="utf-8")
    artifact.joinpath("runtime-config.json").write_text(
        json.dumps({"product_sha": "a" * 40}), encoding="utf-8"
    )
    _write_build_metadata(artifact, product_sha="a" * 40)
    static.joinpath("app.js").write_text("app", encoding="utf-8")
    invoker = _UploadReceiptFixture(receipt)

    with pytest.raises(PreviewAwsError, match="VERSIONED_UPLOAD_NOT_CONFIRMED"):
        publish(
            invoker,
            expected_account_id="synthetic",
            product_sha="a" * 40,
            artifact_dir=artifact,
            confirmation="PUBLISH_R2_WEB",
        )

    assert ("cloudfront", "create-invalidation") not in invoker.operations

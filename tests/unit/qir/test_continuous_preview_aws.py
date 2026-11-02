from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import scripts.continuous_preview_aws as aws_module
import yaml
from scripts.continuous_preview_aws import (
    ContinuousPreviewAwsError,
    assert_schedules_disabled,
    deploy,
    rollback_pointer,
    run_task,
    set_schedules,
    validate_change_set,
    validate_change_set_binding,
    validate_cloudfront_origin_prefix_list,
    validate_ecr_product_image,
    validate_promax_data_secret,
    validate_research_data_secret,
    verify_cli_v2,
)
from scripts.public_demo_aws import assert_cloudformation_template_alias_free

TEMPLATE = Path("deploy/aws/continuous-preview.yaml")
DOCUMENT = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
RESOURCES = DOCUMENT["Resources"]
PSEUDO = {
    "AWS::AccountId",
    "AWS::NoValue",
    "AWS::Partition",
    "AWS::Region",
    "AWS::StackName",
}
CONDITIONED = {
    logical_id
    for logical_id, resource in RESOURCES.items()
    if resource.get("Condition") == "HasImageDigest"
}
ACCOUNT = "123456789012"
PRODUCT_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
CLOUDFRONT_PREFIX_LIST_ID = "pl-1234abcd"
RESEARCH_DATA_SECRET_ARN = (
    f"arn:aws:secretsmanager:ap-northeast-1:{ACCOUNT}:"
    "secret:stockmarketagent/research-data-r1-AbCd12"
)
PROMAX_SECRET_ARN = (
    f"arn:aws:secretsmanager:ap-northeast-1:{ACCOUNT}:"
    "secret:stockmarketagent/research-data-promax-r1-EfGh34"
)


def _walk(value: object):  # type: ignore[no-untyped-def]
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            yield from _walk(child)


def _references(value: object) -> set[str]:
    refs: set[str] = set()
    for item in _walk(value):
        if not isinstance(item, Mapping):
            continue
        ref = item.get("Ref")
        if isinstance(ref, str):
            refs.add(ref)
        get_att = item.get("Fn::GetAtt")
        if isinstance(get_att, list) and get_att and isinstance(get_att[0], str):
            refs.add(get_att[0])
        elif isinstance(get_att, str):
            refs.add(get_att.split(".", 1)[0])
    return refs


def test_template_raw_yaml_forbids_aliases_and_binds_fixed_network_and_public_url() -> None:
    assert_cloudformation_template_alias_free(TEMPLATE.read_bytes())
    assert_cloudformation_template_alias_free(Path("deploy/aws/public-demo.yaml").read_bytes())
    assert DOCUMENT["Resources"]["RefreshVpc"]["Properties"]["CidrBlock"] == "10.73.0.0/16"
    assert "VpcCidr" not in DOCUMENT["Parameters"]
    assert DOCUMENT["Outputs"]["PublicBaseUrl"] == {"Value": {"Ref": "PublicBaseUrl"}}
    for logical_id in ("BootstrapTaskDefinition", "RefreshTaskDefinition"):
        environment = RESOURCES[logical_id]["Properties"]["ContainerDefinitions"][0]["Environment"]
        by_name = {item["Name"]: item["Value"] for item in environment}
        assert by_name["PUBLIC_BASE_URL"] == {"Ref": "PublicBaseUrl"}
        assert by_name["PUBLIC_SMOKE_ATTEMPTS"] == "3"


def test_template_provider_secret_namespaces_are_disjoint() -> None:
    parameters = DOCUMENT["Parameters"]
    research_pattern = parameters["ResearchDataApiSecretArn"]["AllowedPattern"]
    promax_pattern = parameters["PromaxApiSecretArn"]["AllowedPattern"]
    assert re.fullmatch(research_pattern, RESEARCH_DATA_SECRET_ARN) is not None
    assert re.fullmatch(promax_pattern, PROMAX_SECRET_ARN) is not None
    assert re.fullmatch(research_pattern, PROMAX_SECRET_ARN) is None
    assert DOCUMENT["Rules"]["ProviderSecretsMustBeDistinct"]["Assertions"][0]["Assert"] == {
        "Fn::Or": [
            {"Fn::Equals": [{"Ref": "ImageDigest"}, ""]},
            {
                "Fn::Not": [
                    {
                        "Fn::Equals": [
                            {"Ref": "ResearchDataApiSecretArn"},
                            {"Ref": "PromaxApiSecretArn"},
                        ]
                    }
                ]
            },
        ]
    }


@pytest.mark.parametrize(
    "document",
    [
        b"value: &shared one\n",
        b"value: *shared\n",
        b"value: {<<: {nested: true}}\n",
        b"value: [unterminated\n",
        b"value: \xff\n",
    ],
)
def test_template_raw_yaml_guard_rejects_alias_merge_or_invalid_bytes(document: bytes) -> None:
    with pytest.raises(ValueError):
        assert_cloudformation_template_alias_free(document)


def test_template_refs_exist_condition_references_are_safe_and_graph_is_acyclic() -> None:
    known = set(RESOURCES) | set(DOCUMENT["Parameters"]) | PSEUDO
    graph: dict[str, set[str]] = {}
    for logical_id, resource in RESOURCES.items():
        refs = _references(resource)
        depends = resource.get("DependsOn", [])
        if isinstance(depends, str):
            refs.add(depends)
        else:
            refs.update(depends)
        assert refs <= known
        if logical_id not in CONDITIONED:
            assert not (refs & CONDITIONED), (logical_id, refs & CONDITIONED)
        graph[logical_id] = refs & set(RESOURCES)
    for output in DOCUMENT["Outputs"].values():
        refs = _references(output)
        if refs & CONDITIONED:
            assert output.get("Condition") == "HasImageDigest"

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise AssertionError(f"CloudFormation dependency cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for logical_id in graph:
        visit(logical_id)


def test_template_adds_one_api_service_and_alb_without_nat_or_cache() -> None:
    forbidden = {
        "AWS::EC2::NatGateway",
        "AWS::ApiGateway::RestApi",
        "AWS::ApiGatewayV2::Api",
        "AWS::ElastiCache::CacheCluster",
        "AWS::ElastiCache::ReplicationGroup",
    }
    resource_types = {resource["Type"] for resource in RESOURCES.values()}
    assert not resource_types & forbidden
    assert sum(item["Type"] == "AWS::ECS::Service" for item in RESOURCES.values()) == 1
    assert (
        sum(
            item["Type"] == "AWS::ElasticLoadBalancingV2::LoadBalancer"
            for item in RESOURCES.values()
        )
        == 1
    )
    assert RESOURCES["ApiService"]["Properties"]["DesiredCount"] == 1
    for logical_id, resource in RESOURCES.items():
        if resource["Type"] == "AWS::EC2::SecurityGroupIngress":
            assert "CidrIp" not in resource["Properties"], logical_id
        if (
            resource["Type"] == "AWS::EC2::SecurityGroup"
            and logical_id != "ApiLoadBalancerSecurityGroup"
        ):
            assert resource["Properties"].get("SecurityGroupIngress", []) == []
    assert RESOURCES["ApiLoadBalancerSecurityGroup"]["Properties"]["SecurityGroupIngress"] == [
        {
            "FromPort": 80,
            "IpProtocol": "tcp",
            "SourcePrefixListId": {"Ref": "CloudFrontOriginFacingPrefixListId"},
            "ToPort": 80,
        }
    ]
    assert (
        "CidrIp"
        not in RESOURCES["ApiLoadBalancerSecurityGroup"]["Properties"]["SecurityGroupIngress"][0]
    )
    assert DOCUMENT["Rules"]["CloudFrontOriginPrefixListRequiredForRuntime"]["Assertions"][0][
        "Assert"
    ] == {
        "Fn::Or": [
            {"Fn::Equals": [{"Ref": "ImageDigest"}, ""]},
            {
                "Fn::Not": [
                    {
                        "Fn::Equals": [
                            {"Ref": "CloudFrontOriginFacingPrefixListId"},
                            "",
                        ]
                    }
                ]
            },
        ]
    }
    https = RESOURCES["TaskHttpsEgress"]["Properties"]
    assert (https["CidrIp"], https["FromPort"], https["ToPort"]) == (
        "0.0.0.0/0",
        443,
        443,
    )
    database = RESOURCES["TaskDatabaseEgress"]["Properties"]
    assert database["FromPort"] == database["ToPort"] == 5432


def test_api_task_uses_the_bounded_preview_profile_without_model_secrets() -> None:
    container = RESOURCES["ApiTaskDefinition"]["Properties"]["ContainerDefinitions"][0]
    environment = {item["Name"]: item["Value"] for item in container["Environment"]}
    assert environment["APP_ENV"] == "preview"
    assert environment["ENABLE_R1_DEV_LIVE"] == "true"
    assert environment["ENABLE_R1_FIXTURE"] == "false"
    assert environment["R1_RUNTIME_MODE"] == "dev_live"
    assert environment["AWS_REGION"] == {"Ref": "AWS::Region"}
    assert environment["PUBLIC_SNAPSHOT_BUCKET"] == {"Ref": "PublicSnapshotBucketName"}
    assert environment["PUBLIC_BASE_URL"] == {"Ref": "PublicBaseUrl"}
    assert environment["PRODUCT_COMMIT"] == {"Ref": "ProductSha"}
    assert environment["WEB_ORIGIN"] == {"Ref": "PublicBaseUrl"}


def test_rds_is_retained_private_encrypted_tls_and_uses_managed_master_secret() -> None:
    resource = RESOURCES["RefreshDatabase"]
    properties = resource["Properties"]
    assert resource["DeletionPolicy"] == resource["UpdateReplacePolicy"] == "Retain"
    assert properties["PubliclyAccessible"] is False
    assert properties["StorageEncrypted"] is True
    assert properties["DeletionProtection"] is True
    assert properties["BackupRetentionPeriod"] == 7
    assert properties["DeleteAutomatedBackups"] is False
    assert properties["ManageMasterUserPassword"] is True
    assert "MasterUserPassword" not in properties
    assert len(RESOURCES["DatabaseSubnetGroup"]["Properties"]["SubnetIds"]) == 2
    assert RESOURCES["DatabaseParameterGroup"]["Properties"]["Parameters"] == {"rds.force_ssl": "1"}


def test_image_phase_is_digest_only_readonly_nonroot_and_runtime_never_gets_master() -> None:
    expected_conditioned = {
        logical_id
        for logical_id, resource in RESOURCES.items()
        if resource["Type"].startswith(
            (
                "AWS::ECS::",
                "AWS::IAM::",
                "AWS::Scheduler::",
                "AWS::Events::",
                "AWS::Logs::",
                "AWS::CloudWatch::",
                "AWS::SQS::",
            )
        )
    }
    assert expected_conditioned <= CONDITIONED
    for logical_id in ("BootstrapTaskDefinition", "RefreshTaskDefinition"):
        properties = RESOURCES[logical_id]["Properties"]
        assert properties["RequiresCompatibilities"] == ["FARGATE"]
        assert properties["RuntimePlatform"]["OperatingSystemFamily"] == "LINUX"
        assert properties["RuntimePlatform"]["CpuArchitecture"] == "ARM64"
        container = properties["ContainerDefinitions"][0]
        assert container["Image"] == {
            "Fn::Sub": "${RefreshRepository.RepositoryUri}@${ImageDigest}"
        }
        assert container["ReadonlyRootFilesystem"] is True
        assert container["User"] == "app"
        assert container["LinuxParameters"]["Capabilities"]["Drop"] == ["ALL"]
        assert container["MountPoints"] == [
            {
                "ContainerPath": "/tmp",  # noqa: S108 - reviewed Fargate mount.
                "ReadOnly": False,
                "SourceVolume": "tmp",
            }
        ]
        assert "PortMappings" not in container
        assert container["LogConfiguration"]["LogDriver"] == "awslogs"
    bootstrap_secrets = {
        item["Name"]
        for item in RESOURCES["BootstrapTaskDefinition"]["Properties"]["ContainerDefinitions"][0][
            "Secrets"
        ]
    }
    refresh_secret_entries = RESOURCES["RefreshTaskDefinition"]["Properties"][
        "ContainerDefinitions"
    ][0]["Secrets"]
    runtime_secrets = {item["Name"] for item in refresh_secret_entries if "Name" in item}
    assert bootstrap_secrets == {
        "MASTER_DB_USERNAME",
        "MASTER_DB_PASSWORD",
        "DB_USERNAME",
        "DB_PASSWORD",
    }
    assert runtime_secrets == {"DB_USERNAME", "DB_PASSWORD"}
    assert refresh_secret_entries[-3] == {
        "Fn::If": [
            "HasDeepSeekSecret",
            {"Name": "DEEPSEEK_API_KEY", "ValueFrom": {"Ref": "DeepSeekApiSecretArn"}},
            {"Ref": "AWS::NoValue"},
        ]
    }
    assert refresh_secret_entries[-2] == {
        "Fn::If": [
            "HasResearchDataSecret",
            {
                "Name": "R1_RESEARCH_DATA_API_TOKEN",
                "ValueFrom": {"Ref": "ResearchDataApiSecretArn"},
            },
            {"Ref": "AWS::NoValue"},
        ]
    }
    assert refresh_secret_entries[-1] == {
        "Fn::If": [
            "HasPromaxSecret",
            {
                "Name": "R1_PROMAX_API_KEY",
                "ValueFrom": {"Ref": "PromaxApiSecretArn"},
            },
            {"Ref": "AWS::NoValue"},
        ]
    }
    api_secrets = RESOURCES["ApiTaskDefinition"]["Properties"]["ContainerDefinitions"][0]["Secrets"]
    assert {item["Name"] for item in api_secrets} == {"DB_USERNAME", "DB_PASSWORD"}
    assert "DEEPSEEK" not in json.dumps(RESOURCES["ApiTaskDefinition"], separators=(",", ":"))
    assert "RESEARCH_DATA" not in json.dumps(RESOURCES["ApiTaskDefinition"], separators=(",", ":"))
    assert "PROMAX" not in json.dumps(RESOURCES["ApiTaskDefinition"], separators=(",", ":"))


def test_iam_star_is_only_ecr_authorization_and_task_role_is_snapshot_prefix_only() -> None:
    star_statements: list[dict[str, object]] = []
    for resource in RESOURCES.values():
        if resource["Type"] != "AWS::IAM::Role":
            continue
        for policy in resource["Properties"].get("Policies", []):
            for statement in policy["PolicyDocument"]["Statement"]:
                if statement.get("Resource") == "*":
                    star_statements.append(statement)
    assert len(star_statements) == 2
    assert {item["Action"] for item in star_statements} == {"ecr:GetAuthorizationToken"}
    task_statements = RESOURCES["TaskRole"]["Properties"]["Policies"][0]["PolicyDocument"][
        "Statement"
    ]
    object_statement = next(
        statement for statement in task_statements if "s3:GetObject" in statement["Action"]
    )
    serialized = yaml.safe_dump(object_statement["Resource"])
    assert "/data/current.json" in serialized
    assert "/data/snapshots/*" in serialized
    assert object_statement["Action"] == [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
    ]
    list_statement = next(
        statement for statement in task_statements if statement["Action"] == "s3:ListBucket"
    )
    assert list_statement["Resource"] == {
        "Fn::Sub": "arn:${AWS::Partition}:s3:::${PublicSnapshotBucketName}"
    }
    assert list_statement["Condition"] == {"StringEquals": {"s3:prefix": "data/current.json"}}


def test_all_schedules_are_exact_disabled_and_target_refresh_task_fargate_1_4() -> None:
    expected = {
        "IngestSchedule": ("cron(55 8 ? * MON-FRI *)", ["ingest"]),
        "PublishSchedule": ("cron(5 9 ? * MON-FRI *)", ["publish"]),
        "CatchUpSchedule": ("cron(20 9 ? * MON-FRI *)", ["catch-up"]),
        "BehaviorIntradaySchedule": (
            "cron(0/10 9-15 ? * MON-FRI *)",
            ["behavior-intraday"],
        ),
        "BehaviorPostCloseSchedule": (
            "cron(10 15 ? * MON-FRI *)",
            ["behavior-post-close"],
        ),
    }
    for logical_id, (expression, command) in expected.items():
        properties = RESOURCES[logical_id]["Properties"]
        assert properties["State"] == "DISABLED"
        assert properties["FlexibleTimeWindow"] == {"Mode": "OFF"}
        assert properties["ScheduleExpressionTimezone"] == "Asia/Shanghai"
        assert properties["ScheduleExpression"] == expression
        target = properties["Target"]
        assert target["EcsParameters"]["PlatformVersion"] == "1.4.0"
        assert target["EcsParameters"]["TaskDefinitionArn"] == {"Ref": "RefreshTaskDefinition"}
        assert (
            target["EcsParameters"]["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"]
            == "ENABLED"
        )
        assert (
            __import__("json").loads(target["Input"])["containerOverrides"][0]["command"] == command
        )
        assert target["DeadLetterConfig"]["Arn"] == {
            "Fn::GetAtt": ["SchedulerDeliveryQueue", "Arn"]
        }


def test_stopped_nonzero_event_has_separate_failure_queue_and_alarm() -> None:
    pattern = RESOURCES["TaskFailureRule"]["Properties"]["EventPattern"]
    assert pattern["detail"]["lastStatus"] == ["STOPPED"]
    serialized = yaml.safe_dump(pattern)
    assert "anything-but: 0" in serialized
    assert "Task failed to start" in serialized
    target = RESOURCES["TaskFailureRule"]["Properties"]["Targets"][0]["Arn"]
    assert target == {"Fn::GetAtt": ["TaskFailureQueue", "Arn"]}
    for queue_id in ("SchedulerDeliveryQueue", "TaskFailureQueue"):
        properties = RESOURCES[queue_id]["Properties"]
        assert properties["SqsManagedSseEnabled"] is True
        assert "KmsMasterKeyId" not in properties
    alarm_by_queue = {
        alarm["Properties"]["Dimensions"][0]["Value"]["Fn::GetAtt"][0]: alarm
        for alarm in RESOURCES.values()
        if alarm["Type"] == "AWS::CloudWatch::Alarm"
        and alarm["Properties"].get("MetricName") == "ApproximateNumberOfMessagesVisible"
    }
    assert set(alarm_by_queue) == {"SchedulerDeliveryQueue", "TaskFailureQueue"}
    assert all(
        alarm["Properties"]["Threshold"] == 0
        and alarm["Properties"]["TreatMissingData"] == "notBreaching"
        for alarm in alarm_by_queue.values()
    )
    scheduler_queue = RESOURCES["SchedulerDeliveryQueue"]["Properties"]["QueueName"]
    failure_queue = RESOURCES["TaskFailureQueue"]["Properties"]["QueueName"]
    assert scheduler_queue != failure_queue


def _change(logical_id: str, *, action: str = "Modify", replacement: str = "False"):
    return {
        "ResourceChange": {
            "Action": action,
            "LogicalResourceId": logical_id,
            "Replacement": replacement,
        }
    }


def test_change_set_allows_only_task_definition_replacement() -> None:
    accepted = validate_change_set(
        {
            "Status": "CREATE_COMPLETE",
            "Changes": [
                _change("RefreshTaskDefinition", replacement="True"),
                _change("PublishSchedule"),
            ],
        }
    )
    assert accepted[1]["replacement"] == "True"
    with pytest.raises(ContinuousPreviewAwsError, match="PROTECTED_REPLACEMENT"):
        validate_change_set(
            {
                "Status": "CREATE_COMPLETE",
                "Changes": [_change("RefreshDatabase", replacement="True")],
            }
        )
    with pytest.raises(ContinuousPreviewAwsError, match="DESTRUCTIVE"):
        validate_change_set(
            {"Status": "CREATE_COMPLETE", "Changes": [_change("TaskRole", action="Remove")]}
        )
    with pytest.raises(ContinuousPreviewAwsError, match="UNKNOWN_LOGICAL_ID"):
        validate_change_set(
            {"Status": "CREATE_COMPLETE", "Changes": [_change("UnreviewedResource")]}
        )


def test_cli_v2_and_run_task_require_one_stopped_zero_exit_essential_container() -> None:
    verify_cli_v2("aws-cli/2.31.0 Python/3.13")
    with pytest.raises(ContinuousPreviewAwsError, match="V2_REQUIRED"):
        verify_cli_v2("aws-cli/1.42.0 Python/3.12")

    class Invoker:
        def __init__(self) -> None:
            self.exit_code = 0
            self.calls: list[tuple[str, str]] = []
            self.run_task_arguments: tuple[str, ...] = ()

        def json(self, service: str, operation: str, *arguments: str) -> object:
            self.calls.append((service, operation))
            if (service, operation) == ("sts", "get-caller-identity"):
                return {
                    "Account": ACCOUNT,
                    "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/release/test",
                }
            if (service, operation) == ("cloudformation", "describe-stacks"):
                return {
                    "Stacks": [
                        {
                            "StackName": "stockmarketagent-refresh",
                            "StackId": (
                                "arn:aws:cloudformation:ap-northeast-1:"
                                f"{ACCOUNT}:stack/stockmarketagent-refresh/stack-id"
                            ),
                            "StackStatus": "UPDATE_COMPLETE",
                            "Outputs": [
                                {"OutputKey": "ProductSha", "OutputValue": PRODUCT_SHA},
                                {"OutputKey": "ImageDigest", "OutputValue": IMAGE_DIGEST},
                                {"OutputKey": "RefreshClusterArn", "OutputValue": "cluster"},
                                {
                                    "OutputKey": "BootstrapTaskDefinitionArn",
                                    "OutputValue": "bootstrap",
                                },
                                {
                                    "OutputKey": "RefreshTaskDefinitionArn",
                                    "OutputValue": "refresh",
                                },
                                {"OutputKey": "PublicSubnetIds", "OutputValue": "one,two"},
                                {"OutputKey": "TaskSecurityGroupId", "OutputValue": "sg"},
                            ],
                        }
                    ]
                }
            if (service, operation) == ("ecs", "run-task"):
                self.run_task_arguments = arguments
                return {"failures": [], "tasks": [{"taskArn": "task/one"}]}
            if (service, operation) == ("ecs", "describe-tasks"):
                return {
                    "tasks": [
                        {
                            "lastStatus": "STOPPED",
                            "containers": [
                                {"name": "refresh", "exitCode": self.exit_code},
                            ],
                        }
                    ]
                }
            raise AssertionError((service, operation, arguments))

        def text(self, service: str, operation: str, *arguments: str) -> str:
            assert (service, operation) == ("ecs", "wait")
            assert "tasks-stopped" in arguments
            return ""

    invoker = Invoker()
    assert (
        run_task(
            invoker,
            operation="seed-last-session",
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
        )
        == "task/one"
    )
    overrides_index = invoker.run_task_arguments.index("--overrides") + 1
    overrides = json.loads(invoker.run_task_arguments[overrides_index])
    assert overrides["containerOverrides"] == [
        {"name": "refresh", "command": ["seed-last-session"]}
    ]
    assert (
        run_task(
            invoker,
            operation="behavior-last-session",
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
        )
        == "task/one"
    )
    overrides_index = invoker.run_task_arguments.index("--overrides") + 1
    overrides = json.loads(invoker.run_task_arguments[overrides_index])
    assert overrides["containerOverrides"] == [
        {"name": "refresh", "command": ["behavior-last-session"]}
    ]
    invoker.exit_code = 1
    with pytest.raises(ContinuousPreviewAwsError, match="NONZERO_EXIT"):
        run_task(
            invoker,
            operation="seed",
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
        )


@pytest.mark.parametrize(
    ("identity_account", "stack_product_sha", "stack_image_digest", "error_code"),
    [
        ("999999999999", PRODUCT_SHA, IMAGE_DIGEST, "AWS_ACCOUNT_MISMATCH"),
        (ACCOUNT, "c" * 40, IMAGE_DIGEST, "RUNTIME_CANDIDATE_MISMATCH"),
        (ACCOUNT, PRODUCT_SHA, "sha256:" + "d" * 64, "RUNTIME_CANDIDATE_MISMATCH"),
    ],
)
def test_run_task_rejects_identity_or_candidate_mismatch_before_ecs_write(
    identity_account: str,
    stack_product_sha: str,
    stack_image_digest: str,
    error_code: str,
) -> None:
    class Invoker:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def json(self, service: str, operation: str, *arguments: str) -> object:
            self.calls.append((service, operation))
            if (service, operation) == ("sts", "get-caller-identity"):
                return {
                    "Account": identity_account,
                    "Arn": f"arn:aws:sts::{identity_account}:assumed-role/release/test",
                }
            if (service, operation) == ("cloudformation", "describe-stacks"):
                return {
                    "Stacks": [
                        {
                            "StackName": "stockmarketagent-refresh",
                            "StackId": (
                                "arn:aws:cloudformation:ap-northeast-1:"
                                f"{ACCOUNT}:stack/stockmarketagent-refresh/stack-id"
                            ),
                            "StackStatus": "UPDATE_COMPLETE",
                            "Outputs": [
                                {
                                    "OutputKey": "ProductSha",
                                    "OutputValue": stack_product_sha,
                                },
                                {
                                    "OutputKey": "ImageDigest",
                                    "OutputValue": stack_image_digest,
                                },
                            ],
                        }
                    ]
                }
            raise AssertionError((service, operation, arguments))

        def text(self, service: str, operation: str, *arguments: str) -> str:
            raise AssertionError((service, operation, arguments))

    invoker = Invoker()
    with pytest.raises(ContinuousPreviewAwsError, match=error_code):
        run_task(
            invoker,
            operation="seed",
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
        )
    assert ("ecs", "run-task") not in invoker.calls


def test_run_task_cli_requires_account_sha_and_digest() -> None:
    with pytest.raises(SystemExit):
        aws_module._cli().parse_args(["run-task", "seed"])
    parsed = aws_module._cli().parse_args(
        [
            "run-task",
            "seed-last-session",
            "--expected-account",
            ACCOUNT,
            "--product-sha",
            PRODUCT_SHA,
            "--image-digest",
            IMAGE_DIGEST,
        ]
    )
    assert parsed.expected_account == ACCOUNT
    assert parsed.operation == "seed-last-session"
    assert parsed.product_sha == PRODUCT_SHA
    assert parsed.image_digest == IMAGE_DIGEST
    behavior = aws_module._cli().parse_args(
        [
            "run-task",
            "behavior-last-session",
            "--expected-account",
            ACCOUNT,
            "--product-sha",
            PRODUCT_SHA,
            "--image-digest",
            IMAGE_DIGEST,
        ]
    )
    assert behavior.operation == "behavior-last-session"
    source_smoke = aws_module._cli().parse_args(
        [
            "run-task",
            "source-smoke",
            "--expected-account",
            ACCOUNT,
            "--product-sha",
            PRODUCT_SHA,
            "--image-digest",
            IMAGE_DIGEST,
        ]
    )
    assert source_smoke.operation == "source-smoke"


class _ScheduleInvoker:
    def __init__(
        self,
        *,
        state: str = "DISABLED",
        product_sha: str = PRODUCT_SHA,
        image_digest: str = IMAGE_DIGEST,
        schedule_physical_ids_are_names: bool = False,
        schedule_physical_id_override: object | None = None,
        schedule_resource_type: str = "AWS::Scheduler::Schedule",
        schedule_resource_status: str = "CREATE_COMPLETE",
    ) -> None:
        self.states = {
            f"stockmarketagent-refresh-{suffix}": state for suffix in aws_module.SCHEDULE_SUFFIXES
        }
        self.fail_before_update_for: str | None = None
        self.fail_readback_for: str | None = None
        self.fail_compensation_for: str | None = None
        self._pending_readback_failure: str | None = None
        self.persistent_get_failure_for: set[str] = set()
        self.drift_target_for: str | None = None
        self.drift_schedule_semantics_for: str | None = None
        self.calls: list[tuple[str, str]] = []
        self.calls_with_arguments: list[tuple[str, str, tuple[str, ...]]] = []
        self.product_sha = product_sha
        self.image_digest = image_digest
        self.schedule_physical_ids_are_names = schedule_physical_ids_are_names
        self.schedule_physical_id_override = schedule_physical_id_override
        self.schedule_resource_type = schedule_resource_type
        self.schedule_resource_status = schedule_resource_status

    @staticmethod
    def _arn(name: str) -> str:
        return f"arn:aws:scheduler:ap-northeast-1:{ACCOUNT}:schedule/default/{name}"

    def _schedule(self, name: str) -> dict[str, object]:
        suffix = name.removeprefix("stockmarketagent-refresh-")
        expression = {
            "ingest": "cron(55 8 ? * MON-FRI *)",
            "publish": "cron(5 9 ? * MON-FRI *)",
            "catch-up": "cron(20 9 ? * MON-FRI *)",
            "behavior-intraday": "cron(0/10 9-15 ? * MON-FRI *)",
            "behavior-post-close": "cron(10 15 ? * MON-FRI *)",
        }[suffix]
        target: dict[str, object] = {
            "Arn": "cluster",
            "DeadLetterConfig": {"Arn": f"arn:aws:sqs:ap-northeast-1:{ACCOUNT}:scheduler-dlq"},
            "EcsParameters": {
                "LaunchType": "FARGATE",
                "NetworkConfiguration": {
                    "awsvpcConfiguration": {
                        "AssignPublicIp": "ENABLED",
                        "SecurityGroups": ["sg"],
                        "Subnets": ["subnet-a", "subnet-b"],
                    }
                },
                "PlatformVersion": "1.4.0",
                "TaskCount": 1,
                "TaskDefinitionArn": "task-definition",
            },
            "Input": json.dumps(
                {
                    "containerOverrides": [
                        {
                            "name": "refresh",
                            "command": [aws_module.SCHEDULE_COMMANDS[suffix]],
                        }
                    ]
                },
                separators=(",", ":"),
            ),
            "RetryPolicy": {
                "MaximumEventAgeInSeconds": 900,
                "MaximumRetryAttempts": 2,
            },
            "RoleArn": f"arn:aws:iam::{ACCOUNT}:role/scheduler-role",
        }
        if name == self.drift_target_for:
            target["Arn"] = "drifted-cluster"
        schedule: dict[str, object] = {
            "Name": name,
            "Arn": self._arn(name),
            "GroupName": "default",
            "State": self.states[name],
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "ScheduleExpression": expression,
            "ScheduleExpressionTimezone": "Asia/Shanghai",
            "Target": target,
        }
        if name == self.drift_schedule_semantics_for:
            schedule["FlexibleTimeWindow"] = {
                "Mode": "FLEXIBLE",
                "MaximumWindowInMinutes": 15,
            }
            schedule["ScheduleExpression"] = "rate(1 day)"
            schedule["ScheduleExpressionTimezone"] = "UTC"
        return schedule

    def json(self, service: str, operation: str, *arguments: str) -> object:
        self.calls.append((service, operation))
        self.calls_with_arguments.append((service, operation, arguments))
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/release/test",
            }
        if (service, operation) == ("cloudformation", "describe-stacks"):
            return {
                "Stacks": [
                    {
                        "StackName": "stockmarketagent-refresh",
                        "StackId": (
                            "arn:aws:cloudformation:ap-northeast-1:"
                            f"{ACCOUNT}:stack/stockmarketagent-refresh/stack-id"
                        ),
                        "StackStatus": "UPDATE_COMPLETE",
                        "Outputs": [
                            {"OutputKey": "ProductSha", "OutputValue": self.product_sha},
                            {"OutputKey": "ImageDigest", "OutputValue": self.image_digest},
                            {"OutputKey": "RefreshClusterArn", "OutputValue": "cluster"},
                            {
                                "OutputKey": "RefreshTaskDefinitionArn",
                                "OutputValue": "task-definition",
                            },
                            {
                                "OutputKey": "PublicSubnetIds",
                                "OutputValue": "subnet-a,subnet-b",
                            },
                            {"OutputKey": "TaskSecurityGroupId", "OutputValue": "sg"},
                            {
                                "OutputKey": "SchedulerRoleArn",
                                "OutputValue": f"arn:aws:iam::{ACCOUNT}:role/scheduler-role",
                            },
                            {
                                "OutputKey": "SchedulerDeliveryQueueArn",
                                "OutputValue": (
                                    f"arn:aws:sqs:ap-northeast-1:{ACCOUNT}:scheduler-dlq"
                                ),
                            },
                        ],
                    }
                ]
            }
        if (service, operation) == ("cloudformation", "describe-stack-resources"):
            return {
                "StackResources": [
                    {
                        "LogicalResourceId": f"{suffix.title().replace('-', '')}Schedule",
                        "PhysicalResourceId": (
                            self.schedule_physical_id_override
                            if self.schedule_physical_id_override is not None
                            else name
                            if self.schedule_physical_ids_are_names
                            else self._arn(name)
                        ),
                        "ResourceType": self.schedule_resource_type,
                        "ResourceStatus": self.schedule_resource_status,
                    }
                    for suffix, name in (
                        ("ingest", "stockmarketagent-refresh-ingest"),
                        ("publish", "stockmarketagent-refresh-publish"),
                        ("catch-up", "stockmarketagent-refresh-catch-up"),
                        (
                            "behavior-intraday",
                            "stockmarketagent-refresh-behavior-intraday",
                        ),
                        (
                            "behavior-post-close",
                            "stockmarketagent-refresh-behavior-post-close",
                        ),
                    )
                ]
                + [
                    {
                        "LogicalResourceId": "SchedulerRole",
                        "PhysicalResourceId": "scheduler-role",
                        "ResourceType": "AWS::IAM::Role",
                        "ResourceStatus": "CREATE_COMPLETE",
                    },
                    {
                        "LogicalResourceId": "SchedulerDeliveryQueue",
                        "PhysicalResourceId": "scheduler-dlq",
                        "ResourceType": "AWS::SQS::Queue",
                        "ResourceStatus": "CREATE_COMPLETE",
                    },
                ]
            }
        if (service, operation) == ("scheduler", "get-schedule"):
            name = arguments[arguments.index("--name") + 1]
            if name in self.persistent_get_failure_for:
                raise subprocess.CalledProcessError(1, ["aws", "scheduler", "get-schedule"])
            if self._pending_readback_failure == name:
                self._pending_readback_failure = None
                raise subprocess.CalledProcessError(1, ["aws", "scheduler", "get-schedule"])
            return self._schedule(name)
        if (service, operation) == ("scheduler", "update-schedule"):
            payload = json.loads(arguments[arguments.index("--cli-input-json") + 1])
            name = payload["Name"]
            if payload["State"] == "DISABLED" and self.fail_compensation_for == name:
                self.fail_compensation_for = None
                raise subprocess.CalledProcessError(1, ["aws", "scheduler", "update-schedule"])
            if self.fail_before_update_for == name:
                self.fail_before_update_for = None
                raise subprocess.CalledProcessError(1, ["aws", "scheduler", "update-schedule"])
            self.states[name] = payload["State"]
            if self.fail_readback_for == name:
                self.fail_readback_for = None
                self._pending_readback_failure = name
            return {"ScheduleArn": self._arn(name)}
        raise AssertionError((service, operation, arguments))

    def text(self, service: str, operation: str, *arguments: str) -> str:
        raise AssertionError((service, operation, arguments))


def test_rollback_guard_requires_bound_identity_and_all_schedules_disabled() -> None:
    invoker = _ScheduleInvoker()
    assert_schedules_disabled(invoker, expected_account=ACCOUNT)
    invoker.states["stockmarketagent-refresh-publish"] = "ENABLED"
    with pytest.raises(ContinuousPreviewAwsError, match="SCHEDULE_NOT_DISABLED"):
        assert_schedules_disabled(invoker, expected_account=ACCOUNT)


class _FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.elapsed += seconds


class _RollbackInvoker(_ScheduleInvoker):
    cluster_arn = f"arn:aws:ecs:ap-northeast-1:{ACCOUNT}:cluster/stockmarketagent-refresh-cluster"
    task_definition_arn = (
        f"arn:aws:ecs:ap-northeast-1:{ACCOUNT}:task-definition/stockmarketagent-refresh-refresh:7"
    )
    active_task_arn = (
        f"arn:aws:ecs:ap-northeast-1:{ACCOUNT}:task/stockmarketagent-refresh-cluster/active"
    )

    def __init__(
        self,
        *,
        persistent_active_task: bool = False,
        active_last_status: str = "RUNNING",
        count_only_pending_task: bool = False,
    ) -> None:
        super().__init__()
        self.persistent_active_task = persistent_active_task
        self.active_last_status = active_last_status
        self.count_only_pending_task = count_only_pending_task
        self.schedule_sweeps = 0
        self.enable_on_final_schedule_sweep = False

    def json(self, service: str, operation: str, *arguments: str) -> object:
        if (service, operation) == ("scheduler", "get-schedule"):
            self.schedule_sweeps += 1
            if self.enable_on_final_schedule_sweep and self.schedule_sweeps > 3:
                name = arguments[arguments.index("--name") + 1]
                if name.endswith("publish"):
                    self.states[name] = "ENABLED"
            return super().json(service, operation, *arguments)
        self.calls.append((service, operation))
        self.calls_with_arguments.append((service, operation, arguments))
        if (service, operation) == ("ecs", "describe-clusters"):
            return {
                "failures": [],
                "clusters": [
                    {
                        "clusterArn": self.cluster_arn,
                        "clusterName": "stockmarketagent-refresh-cluster",
                        "status": "ACTIVE",
                        "pendingTasksCount": (
                            1
                            if self.count_only_pending_task
                            or (
                                self.persistent_active_task and self.active_last_status == "PENDING"
                            )
                            else 0
                        ),
                        "runningTasksCount": (
                            1
                            if self.persistent_active_task and self.active_last_status == "RUNNING"
                            else 0
                        ),
                    }
                ],
            }
        if (service, operation) == ("ecs", "list-tasks"):
            assert "--family" not in arguments
            status = arguments[arguments.index("--desired-status") + 1]
            assert status == "RUNNING"
            assert "--no-paginate" not in arguments
            return {"taskArns": ([self.active_task_arn] if self.persistent_active_task else [])}
        if (service, operation) == ("ecs", "describe-tasks"):
            return {
                "failures": [],
                "tasks": [
                    {
                        "taskArn": self.active_task_arn,
                        "clusterArn": self.cluster_arn,
                        "lastStatus": self.active_last_status,
                        "desiredStatus": "RUNNING",
                        "taskDefinitionArn": self.task_definition_arn,
                    }
                ],
            }
        return super().json(service, operation, *arguments)


class _RestorePublisher:
    clock: ClassVar[_FakeClock | None] = None
    calls: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, *, client: object, bucket: str) -> None:
        del client
        self.bucket = bucket

    def restore_pointer_version(
        self,
        *,
        version_id: str,
        expected_snapshot_id: str,
    ) -> tuple[SimpleNamespace, str]:
        assert self.clock is not None
        assert self.clock.elapsed >= 900
        self.calls.append((version_id, expected_snapshot_id))
        pointer = SimpleNamespace(
            snapshot_id=expected_snapshot_id,
            product_commit=PRODUCT_SHA,
        )
        return SimpleNamespace(validated=SimpleNamespace(pointer=pointer)), "restored-v2"


def _rollback(
    invoker: _RollbackInvoker,
    *,
    clock: _FakeClock,
) -> dict[str, str | None]:
    return rollback_pointer(
        invoker,
        client=object(),  # type: ignore[arg-type]
        expected_account=ACCOUNT,
        bucket="preview-bucket",
        pointer_version_id="pointer-v1",
        expected_snapshot_id="11111111-1111-4111-8111-111111111111",
        confirmation="RESTORE_PREVIOUS_POINTER",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def test_rollback_waits_full_scheduler_retry_window_and_two_empty_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    invoker = _RollbackInvoker()
    _RestorePublisher.clock = clock
    _RestorePublisher.calls = []
    monkeypatch.setattr(aws_module, "S3SnapshotPublisher", _RestorePublisher)
    restored = _rollback(invoker, clock=clock)
    assert restored["restored_pointer_version_id"] == "restored-v2"
    assert sum(clock.sleeps) >= 900
    assert _RestorePublisher.calls == [("pointer-v1", "11111111-1111-4111-8111-111111111111")]
    assert sum(call == ("ecs", "list-tasks") for call in invoker.calls) >= 2
    assert not any(call == ("ecs", "stop-task") for call in invoker.calls)
    assert not any(call[0] == "cloudformation" for call in invoker.calls)


def test_rollback_never_writes_pointer_while_a_bound_task_remains_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    invoker = _RollbackInvoker(persistent_active_task=True)
    _RestorePublisher.clock = clock
    _RestorePublisher.calls = []
    monkeypatch.setattr(aws_module, "S3SnapshotPublisher", _RestorePublisher)
    with pytest.raises(ContinuousPreviewAwsError, match="ROLLBACK_TASK_DRAIN_TIMEOUT"):
        _rollback(invoker, clock=clock)
    assert _RestorePublisher.calls == []
    assert not any(call == ("ecs", "stop-task") for call in invoker.calls)


def test_rollback_treats_pending_last_status_from_running_query_as_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    invoker = _RollbackInvoker(
        persistent_active_task=True,
        active_last_status="PENDING",
    )
    _RestorePublisher.clock = clock
    _RestorePublisher.calls = []
    monkeypatch.setattr(aws_module, "S3SnapshotPublisher", _RestorePublisher)
    with pytest.raises(ContinuousPreviewAwsError, match="ROLLBACK_TASK_DRAIN_TIMEOUT"):
        _rollback(invoker, clock=clock)
    assert _RestorePublisher.calls == []
    assert all(
        arguments[arguments.index("--desired-status") + 1] == "RUNNING"
        for service, operation, arguments in invoker.calls_with_arguments
        if (service, operation) == ("ecs", "list-tasks")
    )


def test_rollback_rejects_empty_task_list_while_cluster_pending_count_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    invoker = _RollbackInvoker(count_only_pending_task=True)
    _RestorePublisher.clock = clock
    _RestorePublisher.calls = []
    monkeypatch.setattr(aws_module, "S3SnapshotPublisher", _RestorePublisher)
    with pytest.raises(ContinuousPreviewAwsError, match="ROLLBACK_TASK_DRAIN_TIMEOUT"):
        _rollback(invoker, clock=clock)
    assert _RestorePublisher.calls == []


def test_rollback_final_schedule_drift_blocks_pointer_after_retry_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    invoker = _RollbackInvoker()
    invoker.enable_on_final_schedule_sweep = True
    _RestorePublisher.clock = clock
    _RestorePublisher.calls = []
    monkeypatch.setattr(aws_module, "S3SnapshotPublisher", _RestorePublisher)
    with pytest.raises(ContinuousPreviewAwsError, match="ROLLBACK_SCHEDULE_NOT_DISABLED"):
        _rollback(invoker, clock=clock)
    assert clock.elapsed >= 900
    assert _RestorePublisher.calls == []


def test_schedule_enable_compensates_a_mutated_schedule_when_readback_fails() -> None:
    invoker = _ScheduleInvoker()
    invoker.fail_readback_for = "stockmarketagent-refresh-publish"
    with pytest.raises(ContinuousPreviewAwsError, match="SCHEDULE_ENABLE_FAILED"):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
            state="ENABLED",
            confirmation="ENABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert set(invoker.states.values()) == {"DISABLED"}


def test_schedule_enable_accepts_cloudformation_name_physical_ids() -> None:
    invoker = _ScheduleInvoker(schedule_physical_ids_are_names=True)
    set_schedules(
        invoker,
        expected_account=ACCOUNT,
        product_sha=PRODUCT_SHA,
        image_digest=IMAGE_DIGEST,
        state="ENABLED",
        confirmation="ENABLE_CONTINUOUS_PREVIEW_SCHEDULES",
    )
    assert set(invoker.states.values()) == {"ENABLED"}


@pytest.mark.parametrize(
    "invoker",
    [
        _ScheduleInvoker(schedule_physical_id_override="unexpected-name"),
        _ScheduleInvoker(schedule_physical_id_override=[]),
        _ScheduleInvoker(schedule_resource_type="AWS::S3::Bucket"),
        _ScheduleInvoker(schedule_resource_status="UPDATE_IN_PROGRESS"),
    ],
)
def test_schedule_enable_rejects_unbound_stack_schedule_resources(
    invoker: _ScheduleInvoker,
) -> None:
    with pytest.raises(ContinuousPreviewAwsError, match="SCHEDULE_STACK_BINDING_MISMATCH"):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
            state="ENABLED",
            confirmation="ENABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert ("scheduler", "update-schedule") not in invoker.calls


def test_schedule_target_drift_blocks_before_every_update() -> None:
    invoker = _ScheduleInvoker()
    invoker.drift_target_for = "stockmarketagent-refresh-publish"
    with pytest.raises(ContinuousPreviewAwsError, match="SCHEDULE_IDENTITY_MISMATCH"):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
            state="ENABLED",
            confirmation="ENABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert ("scheduler", "update-schedule") not in invoker.calls


@pytest.mark.parametrize(
    ("product_sha", "image_digest"),
    [
        ("c" * 40, IMAGE_DIGEST),
        (PRODUCT_SHA, "sha256:" + "d" * 64),
    ],
)
def test_schedule_enable_rejects_old_candidate_before_every_update(
    product_sha: str,
    image_digest: str,
) -> None:
    invoker = _ScheduleInvoker(product_sha=product_sha, image_digest=image_digest)
    with pytest.raises(ContinuousPreviewAwsError, match="RUNTIME_CANDIDATE_MISMATCH"):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
            state="ENABLED",
            confirmation="ENABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert ("scheduler", "update-schedule") not in invoker.calls


def test_schedule_disable_remains_available_when_candidate_outputs_are_old() -> None:
    invoker = _ScheduleInvoker(
        state="ENABLED",
        product_sha="c" * 40,
        image_digest="sha256:" + "d" * 64,
    )
    set_schedules(
        invoker,
        expected_account=ACCOUNT,
        state="DISABLED",
        confirmation="DISABLE_CONTINUOUS_PREVIEW_SCHEDULES",
    )
    assert set(invoker.states.values()) == {"DISABLED"}


def test_schedule_disable_ignores_target_semantic_drift_and_continues_after_unknown() -> None:
    invoker = _ScheduleInvoker(state="ENABLED")
    invoker.drift_target_for = "stockmarketagent-refresh-ingest"
    invoker.drift_schedule_semantics_for = "stockmarketagent-refresh-ingest"
    invoker.persistent_get_failure_for.add("stockmarketagent-refresh-publish")
    with pytest.raises(
        ContinuousPreviewAwsError,
        match=(
            "SCHEDULE_DISABLE_INCOMPLETE:"
            "unknown=stockmarketagent-refresh-publish;"
            "enabled=stockmarketagent-refresh-publish"
        ),
    ):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            state="DISABLED",
            confirmation="DISABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert invoker.states == {
        "stockmarketagent-refresh-ingest": "DISABLED",
        "stockmarketagent-refresh-publish": "ENABLED",
        "stockmarketagent-refresh-catch-up": "DISABLED",
        "stockmarketagent-refresh-behavior-intraday": "DISABLED",
        "stockmarketagent-refresh-behavior-post-close": "DISABLED",
    }
    updated_names = {
        json.loads(arguments[arguments.index("--cli-input-json") + 1])["Name"]
        for service, operation, arguments in invoker.calls_with_arguments
        if (service, operation) == ("scheduler", "update-schedule")
    }
    assert updated_names == {
        "stockmarketagent-refresh-ingest",
        "stockmarketagent-refresh-catch-up",
        "stockmarketagent-refresh-behavior-intraday",
        "stockmarketagent-refresh-behavior-post-close",
    }


def test_schedule_enable_compensation_continues_after_one_restore_fails() -> None:
    invoker = _ScheduleInvoker()
    invoker.fail_readback_for = "stockmarketagent-refresh-publish"
    invoker.fail_compensation_for = "stockmarketagent-refresh-publish"
    with pytest.raises(
        ContinuousPreviewAwsError,
        match=(
            "SCHEDULE_ENABLE_COMPENSATION_FAILED:unknown=-;enabled=stockmarketagent-refresh-publish"
        ),
    ):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            image_digest=IMAGE_DIGEST,
            state="ENABLED",
            confirmation="ENABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert invoker.states["stockmarketagent-refresh-ingest"] == "DISABLED"
    assert invoker.states["stockmarketagent-refresh-publish"] == "ENABLED"


def test_schedule_disable_continues_and_reports_the_exact_still_enabled_schedule() -> None:
    invoker = _ScheduleInvoker(state="ENABLED")
    invoker.fail_before_update_for = "stockmarketagent-refresh-publish"
    with pytest.raises(
        ContinuousPreviewAwsError,
        match=("SCHEDULE_DISABLE_INCOMPLETE:unknown=-;enabled=stockmarketagent-refresh-publish"),
    ):
        set_schedules(
            invoker,
            expected_account=ACCOUNT,
            state="DISABLED",
            confirmation="DISABLE_CONTINUOUS_PREVIEW_SCHEDULES",
        )
    assert invoker.states == {
        "stockmarketagent-refresh-ingest": "DISABLED",
        "stockmarketagent-refresh-publish": "ENABLED",
        "stockmarketagent-refresh-catch-up": "DISABLED",
        "stockmarketagent-refresh-behavior-intraday": "DISABLED",
        "stockmarketagent-refresh-behavior-post-close": "DISABLED",
    }


class _DeployInvoker:
    def __init__(
        self,
        *,
        describe_stacks_error: subprocess.CalledProcessError | None = None,
    ) -> None:
        self.events: list[str] = []
        self.change_set_name = ""
        self.change_set_type = ""
        self.parameters: dict[str, str] = {}
        self.capabilities: list[str] = []
        self.tags: list[dict[str, str]] = []
        self.executed = False
        self.describe_stacks_error = describe_stacks_error

    def _outputs(self) -> list[dict[str, str]]:
        outputs = [
            {"OutputKey": "ProductSha", "OutputValue": "a" * 40},
            {"OutputKey": "PublicSnapshotBucketName", "OutputValue": "preview-bucket"},
            {"OutputKey": "PublicBaseUrl", "OutputValue": "https://demo.example"},
            {
                "OutputKey": "RepositoryUri",
                "OutputValue": (
                    f"{ACCOUNT}.dkr.ecr.ap-northeast-1.amazonaws.com/stockmarketagent-refresh"
                ),
            },
        ]
        if self.change_set_type != "CREATE":
            outputs.append({"OutputKey": "ImageDigest", "OutputValue": "sha256:" + "b" * 64})
        return outputs

    def json(self, service: str, operation: str, *arguments: str) -> object:
        self.events.append(f"{service}:{operation}")
        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/release/test",
            }
        if (service, operation) == ("secretsmanager", "describe-secret"):
            secret_arn = arguments[1]
            assert arguments[0] == "--secret-id"
            if secret_arn == PROMAX_SECRET_ARN:
                return {
                    "ARN": PROMAX_SECRET_ARN,
                    "Name": "stockmarketagent/research-data-promax-r1",
                }
            assert secret_arn == RESEARCH_DATA_SECRET_ARN
            return {
                "ARN": RESEARCH_DATA_SECRET_ARN,
                "Name": "stockmarketagent/research-data-r1",
            }
        if (service, operation) == ("cloudformation", "validate-template"):
            assert arguments[arguments.index("--template-body") + 1] == "reviewed-template"
            return {}
        if (service, operation) == ("cloudformation", "describe-stacks"):
            if self.describe_stacks_error is not None:
                error = self.describe_stacks_error
                self.describe_stacks_error = None
                raise error
            return {
                "Stacks": [
                    {
                        "StackName": "stockmarketagent-refresh",
                        "StackId": (
                            "arn:aws:cloudformation:ap-northeast-1:"
                            f"{ACCOUNT}:stack/stockmarketagent-refresh/stack-id"
                        ),
                        "StackStatus": "UPDATE_COMPLETE",
                        "Outputs": self._outputs(),
                    }
                ]
            }
        if (service, operation) == ("ecr", "describe-images"):
            return {
                "imageDetails": [
                    {
                        "registryId": ACCOUNT,
                        "repositoryName": "stockmarketagent-refresh",
                        "imageDigest": "sha256:" + "b" * 64,
                        "imageTags": ["a" * 40],
                    }
                ]
            }
        if (service, operation) == ("ec2", "describe-managed-prefix-lists"):
            assert arguments == ("--prefix-list-ids", CLOUDFRONT_PREFIX_LIST_ID)
            return {
                "PrefixLists": [
                    {
                        "PrefixListId": CLOUDFRONT_PREFIX_LIST_ID,
                        "PrefixListName": ("com.amazonaws.global.cloudfront.origin-facing"),
                        "OwnerId": "AWS",
                        "AddressFamily": "IPv4",
                        "State": "create-complete",
                    }
                ]
            }
        if (service, operation) == ("cloudformation", "create-change-set"):
            self.change_set_name = arguments[arguments.index("--change-set-name") + 1]
            self.change_set_type = arguments[arguments.index("--change-set-type") + 1]
            assert arguments[arguments.index("--template-body") + 1] == "reviewed-template"
            parameter_values = arguments[
                arguments.index("--parameters") + 1 : arguments.index("--tags")
            ]
            self.parameters = {
                key.removeprefix("ParameterKey="): value.removeprefix("ParameterValue=")
                for raw in parameter_values
                for key, value in [raw.split(",", 1)]
            }
            capability_index = arguments.index("--capabilities")
            self.capabilities = [arguments[capability_index + 1]]
            self.tags = [
                {
                    "Key": key.removeprefix("Key="),
                    "Value": value.removeprefix("Value="),
                }
                for raw in arguments[arguments.index("--tags") + 1 :]
                for key, value in [raw.split(",", 1)]
            ]
            return {
                "Id": (
                    "arn:aws:cloudformation:ap-northeast-1:"
                    f"{ACCOUNT}:changeSet/{self.change_set_name}/change-id"
                ),
                "StackId": (
                    "arn:aws:cloudformation:ap-northeast-1:"
                    f"{ACCOUNT}:stack/stockmarketagent-refresh/stack-id"
                ),
            }
        if (service, operation) == ("cloudformation", "describe-change-set"):
            return {
                "StackName": "stockmarketagent-refresh",
                "StackId": (
                    "arn:aws:cloudformation:ap-northeast-1:"
                    f"{ACCOUNT}:stack/stockmarketagent-refresh/stack-id"
                ),
                "ChangeSetId": (
                    "arn:aws:cloudformation:ap-northeast-1:"
                    f"{ACCOUNT}:changeSet/{self.change_set_name}/change-id"
                ),
                "Status": "CREATE_COMPLETE",
                "ExecutionStatus": "AVAILABLE",
                "ChangeSetType": self.change_set_type,
                "Capabilities": self.capabilities,
                "Tags": self.tags,
                "Parameters": [
                    {"ParameterKey": key, "ParameterValue": value}
                    for key, value in self.parameters.items()
                ],
                "Changes": [],
            }
        raise AssertionError((service, operation, arguments))

    def text(self, service: str, operation: str, *arguments: str) -> str:
        self.events.append(f"{service}:{operation}")
        if (service, operation) == ("cloudformation", "execute-change-set"):
            self.executed = True
            change_set_id = arguments[arguments.index("--change-set-name") + 1]
            assert change_set_id.endswith(f"/{self.change_set_name}/change-id")
        return ""


def test_digest_deploy_binds_clean_git_template_ecr_and_exact_change_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = _DeployInvoker()

    def local_release(*, product_sha: str, template: Path = TEMPLATE) -> bytes:
        del template
        assert product_sha == "a" * 40
        invoker.events.append("local:release-source")
        return b"reviewed-template"

    monkeypatch.setattr(aws_module, "verify_local_release_source", local_release)
    assert (
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha="a" * 40,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest="sha256:" + "b" * 64,
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
            research_data_secret_arn=RESEARCH_DATA_SECRET_ARN,
            promax_secret_arn=PROMAX_SECRET_ARN,
            cloudfront_origin_prefix_list_id=CLOUDFRONT_PREFIX_LIST_ID,
        )
        == ()
    )
    assert invoker.executed is True
    assert invoker.events.index("local:release-source") < invoker.events.index(
        "sts:get-caller-identity"
    )
    assert invoker.events.index("cloudformation:validate-template") < invoker.events.index(
        "ecr:describe-images"
    )
    assert invoker.events.index("ec2:describe-managed-prefix-lists") < invoker.events.index(
        "cloudformation:create-change-set"
    )
    assert invoker.parameters["ResearchDataApiSecretArn"] == RESEARCH_DATA_SECRET_ARN
    assert invoker.parameters["PromaxApiSecretArn"] == PROMAX_SECRET_ARN
    assert invoker.events.index("ecr:describe-images") < invoker.events.index(
        "cloudformation:create-change-set"
    )


@pytest.mark.parametrize(
    "retry_detail",
    ["", " (reached max retries: 2)"],
)
@pytest.mark.parametrize("error_prefix", ["", "aws: [ERROR]: "])
def test_deploy_creates_only_for_exact_describe_stacks_not_found(
    error_prefix: str,
    retry_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = subprocess.CalledProcessError(
        255,
        ["aws", "cloudformation", "describe-stacks"],
        stderr=(
            f"{error_prefix}An error occurred (ValidationError) when calling the "
            "DescribeStacks operation"
            f"{retry_detail}: "
            "Stack with id stockmarketagent-refresh does not exist"
        ),
    )
    invoker = _DeployInvoker(describe_stacks_error=missing)
    monkeypatch.setattr(
        aws_module,
        "verify_local_release_source",
        lambda **_kwargs: b"reviewed-template",
    )
    assert (
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest="",
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
        )
        == ()
    )
    assert invoker.change_set_type == "CREATE"


def test_digest_deploy_requires_cloudfront_origin_prefix_list_before_aws() -> None:
    invoker = _DeployInvoker()
    with pytest.raises(
        ContinuousPreviewAwsError,
        match="CLOUDFRONT_ORIGIN_PREFIX_LIST_REQUIRED",
    ):
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest=IMAGE_DIGEST,
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
        )
    assert invoker.events == []


def test_digest_deploy_requires_research_data_secret_before_aws() -> None:
    invoker = _DeployInvoker()
    with pytest.raises(ContinuousPreviewAwsError, match="RESEARCH_DATA_SECRET_REQUIRED"):
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest=IMAGE_DIGEST,
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
            cloudfront_origin_prefix_list_id=CLOUDFRONT_PREFIX_LIST_ID,
        )
    assert invoker.events == []


def test_digest_deploy_requires_promax_secret_before_aws() -> None:
    invoker = _DeployInvoker()
    with pytest.raises(ContinuousPreviewAwsError, match="PROMAX_SECRET_REQUIRED"):
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest=IMAGE_DIGEST,
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
            research_data_secret_arn=RESEARCH_DATA_SECRET_ARN,
            cloudfront_origin_prefix_list_id=CLOUDFRONT_PREFIX_LIST_ID,
        )
    assert invoker.events == []


def test_digest_deploy_requires_distinct_provider_secrets_before_aws() -> None:
    invoker = _DeployInvoker()
    with pytest.raises(
        ContinuousPreviewAwsError,
        match="RESEARCH_DATA_SECRET_ARNS_MUST_BE_DISTINCT",
    ):
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest=IMAGE_DIGEST,
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
            research_data_secret_arn=PROMAX_SECRET_ARN,
            promax_secret_arn=PROMAX_SECRET_ARN,
            cloudfront_origin_prefix_list_id=CLOUDFRONT_PREFIX_LIST_ID,
        )
    assert invoker.events == []


def test_research_data_secret_validation_binds_account_name_and_exact_arn() -> None:
    validate_research_data_secret(
        {
            "ARN": RESEARCH_DATA_SECRET_ARN,
            "Name": "stockmarketagent/research-data-r1",
        },
        expected_account=ACCOUNT,
        expected_arn=RESEARCH_DATA_SECRET_ARN,
    )
    with pytest.raises(ContinuousPreviewAwsError, match="RESEARCH_DATA_SECRET_ARN_INVALID"):
        validate_research_data_secret(
            {
                "ARN": RESEARCH_DATA_SECRET_ARN,
                "Name": "stockmarketagent/research-data-r1",
            },
            expected_account="999999999999",
            expected_arn=RESEARCH_DATA_SECRET_ARN,
        )
    with pytest.raises(ContinuousPreviewAwsError, match="RESEARCH_DATA_SECRET_ARN_INVALID"):
        validate_research_data_secret(
            {
                "ARN": PROMAX_SECRET_ARN,
                "Name": "stockmarketagent/research-data-promax-r1",
            },
            expected_account=ACCOUNT,
            expected_arn=PROMAX_SECRET_ARN,
        )


def test_promax_secret_validation_binds_account_name_and_exact_arn() -> None:
    validate_promax_data_secret(
        {
            "ARN": PROMAX_SECRET_ARN,
            "Name": "stockmarketagent/research-data-promax-r1",
        },
        expected_account=ACCOUNT,
        expected_arn=PROMAX_SECRET_ARN,
    )
    with pytest.raises(ContinuousPreviewAwsError, match="PROMAX_SECRET_ARN_INVALID"):
        validate_promax_data_secret(
            {
                "ARN": PROMAX_SECRET_ARN,
                "Name": "stockmarketagent/research-data-promax-r1",
            },
            expected_account="999999999999",
            expected_arn=PROMAX_SECRET_ARN,
        )


def test_cloudfront_origin_prefix_list_validation_rejects_non_aws_owner() -> None:
    with pytest.raises(
        ContinuousPreviewAwsError,
        match="CLOUDFRONT_ORIGIN_PREFIX_LIST_IDENTITY_MISMATCH",
    ):
        validate_cloudfront_origin_prefix_list(
            {
                "PrefixLists": [
                    {
                        "PrefixListId": CLOUDFRONT_PREFIX_LIST_ID,
                        "PrefixListName": ("com.amazonaws.global.cloudfront.origin-facing"),
                        "OwnerId": ACCOUNT,
                        "AddressFamily": "IPv4",
                        "State": "create-complete",
                    }
                ]
            },
            expected_prefix_list_id=CLOUDFRONT_PREFIX_LIST_ID,
        )


@pytest.mark.parametrize(
    "stderr",
    [
        "An error occurred (AccessDenied) when calling the DescribeStacks operation: denied",
        (
            "An error occurred (ValidationError) when calling the DescribeStacks operation: "
            "Stack status is unavailable"
        ),
        (
            "An error occurred (ValidationError) when calling the DescribeStacks operation "
            "(reached max retries: 2): Stack with id other-stack does not exist"
        ),
        (
            "An error occurred (ValidationError) when calling the DescribeStacks operation "
            "(reached max retries: 0): Stack with id stockmarketagent-refresh does not exist"
        ),
        "network transport closed unexpectedly",
    ],
)
def test_deploy_fails_closed_for_non_not_found_describe_errors_before_change_set(
    stderr: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = _DeployInvoker(
        describe_stacks_error=subprocess.CalledProcessError(
            255,
            ["aws", "cloudformation", "describe-stacks"],
            stderr=stderr,
        )
    )
    monkeypatch.setattr(
        aws_module,
        "verify_local_release_source",
        lambda **_kwargs: b"reviewed-template",
    )
    with pytest.raises(ContinuousPreviewAwsError, match="STACK_DISCOVERY_FAILED"):
        deploy(
            invoker,
            expected_account=ACCOUNT,
            product_sha=PRODUCT_SHA,
            bucket="preview-bucket",
            public_base_url="https://demo.example",
            image_digest="",
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
        )
    assert "cloudformation:create-change-set" not in invoker.events


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://demo.example",
        "https://demo.example/",
        "https://demo.example/path",
        "https://demo.example?query=1",
        "https://user@demo.example",
        "https://demo.example:443",
    ],
)
def test_deploy_rejects_public_url_before_git_or_aws(
    public_base_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        aws_module,
        "verify_local_release_source",
        lambda **kwargs: pytest.fail(f"Git preflight must not start: {kwargs}"),
    )
    with pytest.raises(ContinuousPreviewAwsError, match="PUBLIC_BASE_URL_INVALID"):
        deploy(
            _DeployInvoker(),
            expected_account=ACCOUNT,
            product_sha="a" * 40,
            bucket="preview-bucket",
            public_base_url=public_base_url,
            image_digest="",
            confirmation="DEPLOY_CONTINUOUS_PREVIEW",
        )


def test_change_set_and_ecr_binding_reject_extras_or_wrong_digest() -> None:
    change_set_without_describe_type = {
        "StackName": "stockmarketagent-refresh",
        "StackId": (
            f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:stack/stockmarketagent-refresh/id"
        ),
        "ChangeSetId": (f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:changeSet/exact/id"),
        "Status": "CREATE_COMPLETE",
        "ExecutionStatus": "AVAILABLE",
        "Capabilities": ["CAPABILITY_NAMED_IAM"],
        "Tags": [
            {"Key": "Project", "Value": "stockmarketagent-refresh"},
            {"Key": "ManagedBy", "Value": "StockMarketAgent"},
        ],
        "Parameters": [{"ParameterKey": "ProductSha", "ParameterValue": "a" * 40}],
    }
    validate_change_set_binding(
        change_set_without_describe_type,
        expected_account=ACCOUNT,
        expected_change_set_id=(
            f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:changeSet/exact/id"
        ),
        expected_stack_id=(
            f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:stack/stockmarketagent-refresh/id"
        ),
        expected_change_set_type="UPDATE",
        expected_parameters={"ProductSha": "a" * 40},
    )
    mismatched_type = {**change_set_without_describe_type, "ChangeSetType": "CREATE"}
    with pytest.raises(ContinuousPreviewAwsError, match="CHANGE_SET_NOT_EXECUTABLE"):
        validate_change_set_binding(
            mismatched_type,
            expected_account=ACCOUNT,
            expected_change_set_id=(
                f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:changeSet/exact/id"
            ),
            expected_stack_id=(
                f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:stack/stockmarketagent-refresh/id"
            ),
            expected_change_set_type="UPDATE",
            expected_parameters={"ProductSha": "a" * 40},
        )

    with pytest.raises(ContinuousPreviewAwsError, match="PARAMETER_MISMATCH"):
        validate_change_set_binding(
            {
                "StackName": "stockmarketagent-refresh",
                "StackId": (
                    "arn:aws:cloudformation:ap-northeast-1:"
                    f"{ACCOUNT}:stack/stockmarketagent-refresh/id"
                ),
                "ChangeSetId": (
                    f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:changeSet/exact/id"
                ),
                "Status": "CREATE_COMPLETE",
                "ExecutionStatus": "AVAILABLE",
                "ChangeSetType": "UPDATE",
                "Capabilities": ["CAPABILITY_NAMED_IAM"],
                "Tags": [
                    {"Key": "Project", "Value": "stockmarketagent-refresh"},
                    {"Key": "ManagedBy", "Value": "StockMarketAgent"},
                ],
                "Parameters": [
                    {"ParameterKey": "ProductSha", "ParameterValue": "a" * 40},
                    {"ParameterKey": "Unexpected", "ParameterValue": "value"},
                ],
            },
            expected_account=ACCOUNT,
            expected_change_set_id=(
                f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:changeSet/exact/id"
            ),
            expected_stack_id=(
                f"arn:aws:cloudformation:ap-northeast-1:{ACCOUNT}:stack/stockmarketagent-refresh/id"
            ),
            expected_change_set_type="UPDATE",
            expected_parameters={"ProductSha": "a" * 40},
        )
    with pytest.raises(ContinuousPreviewAwsError, match="PRODUCT_DIGEST_MISMATCH"):
        validate_ecr_product_image(
            {
                "imageDetails": [
                    {
                        "registryId": ACCOUNT,
                        "repositoryName": "stockmarketagent-refresh",
                        "imageDigest": "sha256:" + "c" * 64,
                        "imageTags": ["a" * 40],
                    }
                ]
            },
            expected_account=ACCOUNT,
            repository_uri=(
                f"{ACCOUNT}.dkr.ecr.ap-northeast-1.amazonaws.com/stockmarketagent-refresh"
            ),
            product_sha="a" * 40,
            image_digest="sha256:" + "b" * 64,
        )

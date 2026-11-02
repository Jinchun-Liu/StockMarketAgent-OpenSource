"""Locked infrastructure assertions for the private synthetic R2 preview."""

import json
import re
from pathlib import Path
from typing import cast

import yaml

TEMPLATE = Path("deploy/aws/r2-private-preview.yaml")
TMP_DIRECTORY = "/" + "tmp"
SUB_VARIABLE = re.compile(r"\$\{([A-Za-z0-9:._-]+)\}")


def _document() -> dict[str, object]:
    value = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _resources() -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], _document()["Resources"])


def _walk(value: object) -> list[object]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_walk(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_walk(child))
    return values


def test_every_fn_sub_variable_resolves_to_a_template_symbol() -> None:
    document = _document()
    known = {
        *cast(dict[str, object], document["Parameters"]),
        *cast(dict[str, object], document["Resources"]),
        "AWS::AccountId",
        "AWS::NoValue",
        "AWS::Partition",
        "AWS::Region",
        "AWS::StackName",
        "AWS::URLSuffix",
    }
    for value in _walk(document):
        if not isinstance(value, dict) or "Fn::Sub" not in value:
            continue
        substitution = value["Fn::Sub"]
        mapping: dict[str, object] = {}
        if isinstance(substitution, list):
            template = substitution[0]
            mapping = cast(dict[str, object], substitution[1])
        else:
            template = substitution
        assert isinstance(template, str)
        for variable in SUB_VARIABLE.findall(template):
            assert variable in mapping or variable.split(".", maxsplit=1)[0] in known, variable


def test_stack_is_sha_bound_and_activation_is_two_phase() -> None:
    document = _document()
    parameters = cast(dict[str, object], document["Parameters"])
    assert parameters["ProductSha"] == {
        "Type": "String",
        "AllowedPattern": "^[0-9a-f]{40}$",
    }
    assert parameters["ImageDigest"] == {
        "Type": "String",
        "Default": "",
        "AllowedPattern": "^$|^sha256:[0-9a-f]{64}$",
    }
    assert parameters["RuntimeDesiredCount"] == {
        "Type": "Number",
        "Default": 0,
        "AllowedValues": [0, 1],
    }
    outputs = cast(dict[str, object], document["Outputs"])
    assert outputs["ProductSha"] == {"Value": {"Ref": "ProductSha"}}
    assert outputs["ImageDigest"] == {"Value": {"Ref": "ImageDigest"}}
    rules = cast(dict[str, object], document["Rules"])
    activation = cast(dict[str, object], rules["RuntimeActivationRequiresImage"])
    assertions = cast(list[dict[str, object]], activation["Assertions"])
    expression = cast(dict[str, object], assertions[0]["Assert"])
    operands = cast(list[dict[str, object]], expression["Fn::Or"])
    assert operands[0] == {"Fn::Equals": [{"Ref": "RuntimeDesiredCount"}, "0"]}


def test_data_services_are_private_encrypted_and_retained() -> None:
    resources = _resources()
    database = cast(dict[str, object], resources["ResearchDatabase"]["Properties"])
    assert database["PubliclyAccessible"] is False
    assert database["StorageEncrypted"] is True
    assert database["DeletionProtection"] is True
    assert database["EngineVersion"] == "16.15"
    assert resources["ResearchDatabase"]["DeletionPolicy"] == "Snapshot"
    valkey = cast(dict[str, object], resources["ResearchValkey"]["Properties"])
    assert valkey["Engine"] == "valkey"
    assert valkey["EngineVersion"] == "8.2"
    assert valkey["TransitEncryptionEnabled"] is True
    assert valkey["TransitEncryptionMode"] == "required"
    assert valkey["AtRestEncryptionEnabled"] is True
    assert resources["ResearchValkey"]["DeletionPolicy"] == "Retain"


def test_artifact_bucket_preserves_retention_and_separates_writes_from_history() -> None:
    resources = _resources()
    bucket = cast(dict[str, object], resources["ResearchArtifactBucket"]["Properties"])
    assert bucket["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    lifecycle = cast(dict[str, object], bucket["LifecycleConfiguration"])
    rule = cast(list[dict[str, object]], lifecycle["Rules"])[0]
    assert rule["Prefix"] == "releases/"
    assert rule["ExpirationInDays"] == 7
    role = cast(dict[str, object], resources["RuntimeTaskRole"]["Properties"])
    policies = cast(list[dict[str, object]], role["Policies"])
    policy = cast(dict[str, object], policies[0]["PolicyDocument"])
    assert policy["Statement"] == [
        {
            "Effect": "Allow",
            "Action": "s3:PutObject",
            "Resource": {
                "Fn::Sub": "${ResearchArtifactBucket.Arn}/releases/${ProductSha}/research/*"
            },
        },
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:GetObjectVersion", "s3:DeleteObjectVersion"],
            "Resource": {"Fn::Sub": "${ResearchArtifactBucket.Arn}/releases/*/research/*"},
        },
        {
            "Effect": "Allow",
            "Action": "s3:ListBucket",
            "Resource": {"Fn::GetAtt": ["ResearchArtifactBucket", "Arn"]},
            "Condition": {
                "StringLike": {"s3:prefix": {"Fn::Sub": "releases/${ProductSha}/research/*"}}
            },
        },
    ]


def test_alb_accepts_only_cloudfront_and_tasks_have_bounded_egress() -> None:
    resources = _resources()
    ingress = cast(dict[str, object], resources["LoadBalancerIngressFromCloudFront"]["Properties"])
    assert ingress["SourcePrefixListId"] == {"Ref": "CloudFrontOriginFacingPrefixListId"}
    assert "CidrIp" not in ingress
    listener = cast(dict[str, object], resources["ApiListener"]["Properties"])
    assert listener["DefaultActions"] == [
        {
            "Type": "fixed-response",
            "FixedResponseConfig": {
                "ContentType": "application/json",
                "MessageBody": '{"detail":"origin not authorized"}',
                "StatusCode": "403",
            },
        }
    ]
    health_deny = cast(
        dict[str, object],
        resources["ApiListenerDenyPublicHealthPaths"]["Properties"],
    )
    assert health_deny["Priority"] == 5
    assert health_deny["Conditions"] == [
        {
            "Field": "path-pattern",
            "PathPatternConfig": {"Values": ["/api/v1/health/*"]},
        }
    ]
    assert health_deny["Actions"] == [
        {
            "Type": "fixed-response",
            "FixedResponseConfig": {
                "ContentType": "application/json",
                "MessageBody": '{"detail":"not found"}',
                "StatusCode": "404",
            },
        }
    ]
    rule = cast(
        dict[str, object],
        resources["ApiListenerForwardFromProjectCloudFront"]["Properties"],
    )
    assert rule["Conditions"] == [
        {
            "Field": "http-header",
            "HttpHeaderConfig": {
                "HttpHeaderName": "X-StockMarketAgent-Origin-Verify",
                "Values": [
                    {
                        "Fn::Sub": (
                            "{{resolve:secretsmanager:${CloudFrontOriginVerifySecret}:"
                            "SecretString:header_value}}"
                        )
                    }
                ],
            },
        }
    ]
    assert resources["ApiService"]["DependsOn"] == ("ApiListenerForwardFromProjectCloudFront")
    for name in (
        "TaskSecurityGroup",
        "ApiTaskSecurityGroup",
        "WorkerTaskSecurityGroup",
        "SchedulerTaskSecurityGroup",
        "EgressProxySecurityGroup",
    ):
        group = cast(dict[str, object], resources[name]["Properties"])
        assert group["SecurityGroupEgress"] == []
    assert "WorkerHttpsEgress" not in resources
    assert "ApiHttpsEgress" not in resources
    legacy_reference = "['TaskSecurityGroup', 'GroupId']"
    assert all(
        legacy_reference not in str(resource)
        for name, resource in resources.items()
        if name != "TaskSecurityGroup"
    )
    worker_proxy = cast(dict[str, object], resources["WorkerEgressToProxy"]["Properties"])
    assert worker_proxy["DestinationSecurityGroupId"] == {
        "Fn::GetAtt": ["EgressProxySecurityGroup", "GroupId"]
    }
    assert worker_proxy["FromPort"] == 3128
    api_proxy = cast(dict[str, object], resources["ApiEgressToProxy"]["Properties"])
    assert api_proxy["DestinationSecurityGroupId"] == {
        "Fn::GetAtt": ["EgressProxySecurityGroup", "GroupId"]
    }
    assert api_proxy["FromPort"] == 3128
    assert "SchedulerEgressToProxy" not in resources
    assert "EgressProxyIngressFromScheduler" not in resources
    proxy_https = cast(dict[str, object], resources["EgressProxyHttpsEgress"]["Properties"])
    assert proxy_https["CidrIp"] == "0.0.0.0/0"
    assert resources["S3GatewayEndpoint"]["Type"] == "AWS::EC2::VPCEndpoint"
    for name in ("EcrApiEndpoint", "EcrDockerEndpoint", "LogsEndpoint", "SecretsManagerEndpoint"):
        endpoint = cast(dict[str, object], resources[name]["Properties"])
        assert endpoint["VpcEndpointType"] == "Interface"
        assert endpoint["PrivateDnsEnabled"] is True
    for name in (
        "ApiEgressToS3Endpoint",
        "WorkerEgressToS3Endpoint",
        "SchedulerEgressToS3Endpoint",
    ):
        egress = cast(dict[str, object], resources[name]["Properties"])
        assert egress["DestinationPrefixListId"] == {"Ref": "S3ManagedPrefixListId"}
    assert not any(
        resource.get("Properties", {}).get("CidrIp") == "0.0.0.0/0"
        for name, resource in resources.items()
        if name.startswith(("Api", "Worker", "Scheduler"))
    )


def test_egress_proxy_allows_every_enabled_research_web_domain() -> None:
    resources = _resources()
    proxy = resources["EgressProxyTaskDefinition"]["Properties"]["ContainerDefinitions"][0]
    command = cast(list[object], proxy["Command"])
    allowed_hosts = next(
        value["Fn::Sub"] for value in command if isinstance(value, dict) and "Fn::Sub" in value
    )
    assert isinstance(allowed_hosts, str)
    configured = {value.strip() for value in allowed_hosts.split(",")}
    allowlist = json.loads(
        Path("resources/research_sources/web-allowlist.v1.json").read_text(encoding="utf-8")
    )
    enabled = {item["domain"] for item in allowlist["sources"] if item["enabled"]}
    assert enabled <= configured

    pdf_only = next(item for item in allowlist["sources"] if item["source_id"] == "cninfo_static")
    assert pdf_only["enabled"] is True
    assert pdf_only["domain"] in configured
    assert pdf_only["release_tier"] == "dev_only"
    assert pdf_only["rights_status"] == "unverified"
    assert pdf_only["research_use"] == "excerpt_bound_fact"


def test_origin_secret_is_generated_and_only_its_arn_is_exported() -> None:
    document = _document()
    resources = _resources()
    secret = cast(dict[str, object], resources["CloudFrontOriginVerifySecret"])
    properties = cast(dict[str, object], secret["Properties"])
    assert secret["DeletionPolicy"] == "Retain"
    assert properties["GenerateSecretString"] == {
        "SecretStringTemplate": '{"purpose":"cloudfront-origin-verification"}',
        "GenerateStringKey": "header_value",
        "PasswordLength": 64,
        "ExcludePunctuation": True,
    }
    outputs = cast(dict[str, object], document["Outputs"])
    assert outputs["CloudFrontOriginVerifySecretArn"] == {
        "Value": {"Ref": "CloudFrontOriginVerifySecret"}
    }
    assert "header_value" not in str(outputs)


def test_cognito_disables_self_registration_and_mfa_but_keeps_pkce() -> None:
    resources = _resources()
    pool = cast(dict[str, object], resources["UserPool"]["Properties"])
    assert pool["AdminCreateUserConfig"] == {"AllowAdminCreateUserOnly": True}
    assert pool["MfaConfiguration"] == "OFF"
    assert "EnabledMfas" not in pool
    assert "SoftwareTokenMfaConfiguration" not in pool
    client = cast(dict[str, object], resources["UserPoolClient"]["Properties"])
    assert client["AllowedOAuthFlows"] == ["code"]
    assert client["GenerateSecret"] is False
    assert client["AllowedOAuthScopes"] == ["openid"]
    assert client["AccessTokenValidity"] == 24
    assert client["IdTokenValidity"] == 24
    assert client["RefreshTokenValidity"] == 1
    assert client["TokenValidityUnits"] == {
        "AccessToken": "hours",
        "IdToken": "hours",
        "RefreshToken": "days",
    }


def test_runtime_has_only_r2_api_worker_scheduler_and_no_r1_schedule() -> None:
    resources = _resources()
    assert {"ApiService", "WorkerService", "SchedulerService", "EgressProxyService"} <= set(
        resources
    )
    assert not any(
        resource["Type"] == "AWS::Scheduler::Schedule" for resource in resources.values()
    )
    text = TEMPLATE.read_text(encoding="utf-8")
    assert 'ENABLE_R1_FIXTURE, Value: "false"' in text
    assert 'ENABLE_R2_PRIVATE_PREVIEW, Value: "true"' in text
    assert "09:05" not in text
    assert "premarket" not in text.casefold()
    assert resources["WorkerService"]["DependsOn"] == "EgressProxyService"
    assert resources["EgressProxyService"]["Properties"]["ServiceRegistries"] == [
        {
            "RegistryArn": {
                "Fn::GetAtt": ["EgressProxyDiscoveryService", "Arn"],
            },
            "ContainerName": "egress-proxy",
        }
    ]
    proxy_container = resources["EgressProxyTaskDefinition"]["Properties"]["ContainerDefinitions"][
        0
    ]
    assert proxy_container["PortMappings"] == [
        {"ContainerPort": 3128, "Name": "connect", "Protocol": "tcp"}
    ]
    assert "DependsOn" not in resources["SchedulerService"]
    assert "HTTPS_PROXY" not in str(resources["SchedulerTaskDefinition"])
    assert "HTTP_PROXY" not in str(resources["SchedulerTaskDefinition"])
    api_task = str(resources["ApiTaskDefinition"])
    assert "COGNITO_JWKS_PROXY_URL" in api_task
    assert "HTTPS_PROXY" not in api_task
    assert "HTTP_PROXY" not in api_task
    assert "COGNITO_JWKS_PROXY_URL" not in str(resources["WorkerTaskDefinition"])
    assert "COGNITO_JWKS_PROXY_URL" not in str(resources["SchedulerTaskDefinition"])
    for task_name in ("WorkerTaskDefinition", "SchedulerTaskDefinition"):
        task = str(resources[task_name])
        assert "COGNITO_CLIENT_ID" not in task
        assert "COGNITO_ISSUER" not in task
        assert "COGNITO_JWKS_URL" not in task


def test_worker_keeps_read_only_root_with_ephemeral_tmp_volume() -> None:
    resources = _resources()
    worker = cast(dict[str, object], resources["WorkerTaskDefinition"]["Properties"])
    assert worker["Volumes"] == [{"Name": "worker-tmp"}]
    containers = cast(list[dict[str, object]], worker["ContainerDefinitions"])
    assert len(containers) == 1
    container = containers[0]
    assert container["ReadonlyRootFilesystem"] is True
    assert container["User"] == "app"
    assert container["MountPoints"] == [
        {
            "ContainerPath": TMP_DIRECTORY,
            "ReadOnly": False,
            "SourceVolume": "worker-tmp",
        }
    ]
    dockerfile = Path("deploy/docker/python.Dockerfile").read_text(encoding="utf-8")
    assert 'VOLUME ["/tmp"]' in dockerfile


def test_waf_logs_and_alarms_are_present() -> None:
    resources = _resources()
    assert resources["ApiWebAcl"]["Type"] == "AWS::WAFv2::WebACL"
    assert resources["ApiWebAclAssociation"]["Type"] == "AWS::WAFv2::WebACLAssociation"
    assert resources["ApiUnhealthyAlarm"]["Type"] == "AWS::CloudWatch::Alarm"
    assert resources["WorkerStoppedAlarm"]["Type"] == "AWS::CloudWatch::Alarm"
    assert resources["EgressProxyStoppedAlarm"]["Type"] == "AWS::CloudWatch::Alarm"
    for name in ("ApiLogGroup", "WorkerLogGroup", "SchedulerLogGroup", "EgressProxyLogGroup"):
        assert resources[name]["Type"] == "AWS::Logs::LogGroup"


def test_model_secrets_are_injected_only_into_worker_execution_context() -> None:
    document = _document()
    parameters = cast(dict[str, object], document["Parameters"])
    assert "DeepSeekApiSecretArn" in parameters
    assert "TavilyApiSecretArn" in parameters
    resources = _resources()
    worker_role = cast(dict[str, object], resources["WorkerExecutionRole"]["Properties"])
    assert "DeepSeekApiSecretArn" in str(worker_role)
    assert "TavilyApiSecretArn" in str(worker_role)
    base_role = cast(dict[str, object], resources["ExecutionRole"]["Properties"])
    assert "DeepSeekApiSecretArn" not in str(base_role)
    worker = cast(dict[str, object], resources["WorkerTaskDefinition"]["Properties"])
    worker_text = str(worker)
    assert "DEEPSEEK_API_KEY" in worker_text
    assert "TAVILY_API_KEY" in worker_text
    assert "ENABLE_R2_WEB_FALLBACK" in worker_text
    for name in ("ApiTaskDefinition", "SchedulerTaskDefinition"):
        assert "DEEPSEEK_API_KEY" not in str(resources[name])
        assert "TAVILY_API_KEY" not in str(resources[name])


def test_every_db_service_uses_bounded_fresh_credential_health() -> None:
    resources = _resources()
    for name in ("Api", "Worker", "Scheduler"):
        properties = cast(dict[str, object], resources[f"{name}TaskDefinition"]["Properties"])
        container = cast(list[dict[str, object]], properties["ContainerDefinitions"])[0]
        assert container["Essential"] is True
        assert container["HealthCheck"] == {
            "Command": ["CMD", "python", "-m", "apps.runtime.health"],
            "Interval": 60,
            "Timeout": 10,
            "Retries": 3,
            "StartPeriod": 90,
        }

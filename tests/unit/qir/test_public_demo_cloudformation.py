from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = ROOT / "deploy" / "aws" / "public-demo.yaml"


def _template() -> dict[str, object]:
    document = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, object], document)


def _resources() -> dict[str, dict[str, object]]:
    resources = _template()["Resources"]
    assert isinstance(resources, dict)
    return cast(dict[str, dict[str, object]], resources)


def test_public_demo_bucket_is_private_versioned_and_encrypted() -> None:
    bucket = _resources()["PublicDemoBucket"]
    properties = cast(dict[str, object], bucket["Properties"])

    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert properties["OwnershipControls"] == {
        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
    }
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert properties["BucketEncryption"] == {
        "ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
        ]
    }
    assert "WebsiteConfiguration" not in properties
    assert "AccessControl" not in properties


def test_cloudfront_uses_signed_private_origin_and_minimal_bucket_policy() -> None:
    resources = _resources()
    origin_access = resources["PublicDemoOriginAccessControl"]["Properties"]
    assert origin_access == {
        "OriginAccessControlConfig": {
            "Description": (
                "Signed access from the public demo distribution to its private S3 origin"
            ),
            "Name": {"Fn::Sub": "${AWS::StackName}-oac"},
            "OriginAccessControlOriginType": "s3",
            "SigningBehavior": "always",
            "SigningProtocol": "sigv4",
        }
    }

    distribution = cast(dict[str, object], resources["PublicDemoDistribution"]["Properties"])
    config = cast(dict[str, object], distribution["DistributionConfig"])
    origins = cast(list[dict[str, object]], config["Origins"])
    assert origins == [
        {
            "DomainName": {"Fn::GetAtt": ["PublicDemoBucket", "RegionalDomainName"]},
            "Id": "private-s3-origin",
            "OriginAccessControlId": {"Ref": "PublicDemoOriginAccessControl"},
            "S3OriginConfig": {"OriginAccessIdentity": ""},
        },
        {
            "Fn::If": [
                "HasApiOrigin",
                {
                    "CustomOriginConfig": {
                        "HTTPPort": 80,
                        "OriginKeepaliveTimeout": 5,
                        "OriginProtocolPolicy": "http-only",
                        "OriginReadTimeout": 60,
                        "OriginSSLProtocols": ["TLSv1.2"],
                    },
                    "DomainName": {"Ref": "ApiOriginDomainName"},
                    "Id": "runtime-api-origin",
                    "OriginCustomHeaders": [
                        {
                            "HeaderName": "X-StockMarketAgent-Origin-Verify",
                            "HeaderValue": {
                                "Fn::Sub": (
                                    "{{resolve:secretsmanager:${ApiOriginVerifySecretArn}:"
                                    "SecretString:header_value}}"
                                )
                            },
                        }
                    ],
                },
                {"Ref": "AWS::NoValue"},
            ]
        },
    ]

    policy = cast(dict[str, object], resources["PublicDemoBucketPolicy"]["Properties"])
    document = cast(dict[str, object], policy["PolicyDocument"])
    statements = cast(list[dict[str, object]], document["Statement"])
    assert statements == [
        {
            "Action": "s3:*",
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            "Effect": "Deny",
            "Principal": "*",
            "Resource": [
                {"Fn::GetAtt": ["PublicDemoBucket", "Arn"]},
                {"Fn::Sub": "${PublicDemoBucket.Arn}/*"},
            ],
            "Sid": "DenyInsecureTransport",
        },
        {
            "Action": "s3:GetObject",
            "Condition": {
                "StringEquals": {
                    "AWS:SourceArn": {
                        "Fn::Sub": (
                            "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:"
                            "distribution/${PublicDemoDistribution}"
                        )
                    }
                }
            },
            "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Resource": {"Fn::Sub": "${PublicDemoBucket.Arn}/*"},
            "Sid": "AllowOnlyThisCloudFrontDistribution",
        },
    ]


def test_distribution_has_locked_delivery_cache_without_global_error_interception() -> None:
    resources = _resources()
    distribution = cast(dict[str, object], resources["PublicDemoDistribution"]["Properties"])
    config = cast(dict[str, object], distribution["DistributionConfig"])

    assert config["Enabled"] is True
    assert config["HttpVersion"] == "http2and3"
    assert config["PriceClass"] == "PriceClass_200"
    assert config["DefaultRootObject"] == "index.html"
    assert config["ViewerCertificate"] == {"CloudFrontDefaultCertificate": True}
    assert "CustomErrorResponses" not in config

    default = cast(dict[str, object], config["DefaultCacheBehavior"])
    assert default["ViewerProtocolPolicy"] == "redirect-to-https"
    assert default["CachePolicyId"] == {"Ref": "PublicDemoShortCachePolicy"}
    assert default["ResponseHeadersPolicyId"] == {"Ref": "PublicDemoSecurityHeadersPolicy"}
    assert default["FunctionAssociations"] == [
        {
            "EventType": "viewer-request",
            "FunctionARN": {"Fn::GetAtt": ["PublicDemoRewriteFunction", "FunctionARN"]},
        }
    ]

    cache_behaviors = cast(list[dict[str, object]], config["CacheBehaviors"])
    assert cache_behaviors == [
        {
            "Fn::If": [
                "HasApiOrigin",
                {
                    "AllowedMethods": [
                        "DELETE",
                        "GET",
                        "HEAD",
                        "OPTIONS",
                        "PATCH",
                        "POST",
                        "PUT",
                    ],
                    "CachePolicyId": {
                        "Fn::If": [
                            "HasExternalPrivatePreviewPolicies",
                            {"Ref": "PrivatePreviewApiNoCachePolicyId"},
                            {"Ref": "PrivatePreviewApiNoCachePolicy"},
                        ]
                    },
                    "CachedMethods": ["GET", "HEAD", "OPTIONS"],
                    "Compress": False,
                    "OriginRequestPolicyId": {
                        "Fn::If": [
                            "HasExternalPrivatePreviewPolicies",
                            {"Ref": "PrivatePreviewApiOriginRequestPolicyId"},
                            {"Ref": "PrivatePreviewApiOriginRequestPolicy"},
                        ]
                    },
                    "PathPattern": "api/v2/*",
                    "ResponseHeadersPolicyId": {"Ref": "PublicDemoSecurityHeadersPolicy"},
                    "TargetOriginId": "runtime-api-origin",
                    "ViewerProtocolPolicy": "redirect-to-https",
                },
                {"Ref": "AWS::NoValue"},
            ]
        },
        {
            "Fn::If": [
                "HasApiOrigin",
                {
                    "AllowedMethods": ["GET", "HEAD", "OPTIONS"],
                    "CachePolicyId": {"Ref": "PublicDemoNoCachePolicy"},
                    "CachedMethods": ["GET", "HEAD"],
                    "Compress": False,
                    "OriginRequestPolicyId": {"Ref": "PublicApiOriginRequestPolicy"},
                    "PathPattern": "api/*",
                    "ResponseHeadersPolicyId": {"Ref": "PublicDemoSecurityHeadersPolicy"},
                    "TargetOriginId": "runtime-api-origin",
                    "ViewerProtocolPolicy": "redirect-to-https",
                },
                {"Ref": "AWS::NoValue"},
            ]
        },
        {
            "AllowedMethods": ["GET", "HEAD"],
            "CachePolicyId": {"Ref": "PublicDemoNoCachePolicy"},
            "CachedMethods": ["GET", "HEAD"],
            "Compress": True,
            "PathPattern": "data/current.json",
            "ResponseHeadersPolicyId": {"Ref": "PublicDemoSecurityHeadersPolicy"},
            "TargetOriginId": "private-s3-origin",
            "ViewerProtocolPolicy": "redirect-to-https",
        },
        {
            "AllowedMethods": ["GET", "HEAD"],
            "CachePolicyId": {"Ref": "PublicDemoImmutableCachePolicy"},
            "CachedMethods": ["GET", "HEAD"],
            "Compress": True,
            "PathPattern": "data/snapshots/*",
            "ResponseHeadersPolicyId": {"Ref": "PublicDemoSecurityHeadersPolicy"},
            "TargetOriginId": "private-s3-origin",
            "ViewerProtocolPolicy": "redirect-to-https",
        },
        {
            "AllowedMethods": ["GET", "HEAD"],
            "CachePolicyId": {"Ref": "PublicDemoImmutableCachePolicy"},
            "CachedMethods": ["GET", "HEAD"],
            "Compress": True,
            "PathPattern": "_next/static/*",
            "ResponseHeadersPolicyId": {"Ref": "PublicDemoSecurityHeadersPolicy"},
            "TargetOriginId": "private-s3-origin",
            "ViewerProtocolPolicy": "redirect-to-https",
        },
    ]

    short = cast(
        dict[str, object],
        resources["PublicDemoShortCachePolicy"]["Properties"]["CachePolicyConfig"],
    )
    assert short["MinTTL"] == 0
    assert short["DefaultTTL"] == 60
    assert short["MaxTTL"] == 300

    no_cache = cast(
        dict[str, object],
        resources["PublicDemoNoCachePolicy"]["Properties"]["CachePolicyConfig"],
    )
    assert no_cache["MinTTL"] == 0
    assert no_cache["DefaultTTL"] == 0
    assert no_cache["MaxTTL"] == 0
    no_cache_parameters = cast(
        dict[str, object],
        no_cache["ParametersInCacheKeyAndForwardedToOrigin"],
    )
    assert no_cache_parameters["EnableAcceptEncodingBrotli"] is False
    assert no_cache_parameters["EnableAcceptEncodingGzip"] is False

    r2_no_cache = cast(
        dict[str, object],
        resources["PrivatePreviewApiNoCachePolicy"]["Properties"]["CachePolicyConfig"],
    )
    assert resources["PrivatePreviewApiNoCachePolicy"]["Condition"] == (
        "CreatePrivatePreviewPolicies"
    )
    assert (r2_no_cache["MinTTL"], r2_no_cache["DefaultTTL"], r2_no_cache["MaxTTL"]) == (
        0,
        0,
        0,
    )
    r2_cache_parameters = cast(
        dict[str, object],
        r2_no_cache["ParametersInCacheKeyAndForwardedToOrigin"],
    )
    assert r2_cache_parameters["HeadersConfig"] == {"HeaderBehavior": "none"}
    assert r2_cache_parameters["QueryStringsConfig"] == {"QueryStringBehavior": "none"}

    r2_origin = cast(
        dict[str, object],
        resources["PrivatePreviewApiOriginRequestPolicy"]["Properties"][
            "OriginRequestPolicyConfig"
        ],
    )
    assert resources["PrivatePreviewApiOriginRequestPolicy"]["Condition"] == (
        "CreatePrivatePreviewPolicies"
    )
    assert r2_origin["CookiesConfig"] == {"CookieBehavior": "none"}
    assert r2_origin["HeadersConfig"] == {
        "HeaderBehavior": "allExcept",
        "Headers": ["Host"],
    }
    assert r2_origin["QueryStringsConfig"] == {"QueryStringBehavior": "all"}

    immutable = cast(
        dict[str, object],
        resources["PublicDemoImmutableCachePolicy"]["Properties"]["CachePolicyConfig"],
    )
    assert immutable["MinTTL"] == 31536000
    assert immutable["DefaultTTL"] == 31536000
    assert immutable["MaxTTL"] == 31536000


def test_viewer_request_rewrites_directory_and_extensionless_routes() -> None:
    function = _resources()["PublicDemoRewriteFunction"]
    properties = cast(dict[str, object], function["Properties"])
    code = cast(str, properties["FunctionCode"])

    assert properties["AutoPublish"] is True
    assert properties["FunctionConfig"] == {
        "Comment": "Rewrite known static routes and extensionless misses safely",
        "Runtime": "cloudfront-js-2.0",
    }
    assert 'request.uri = "/evidence/current/index.html"' in code
    assert "^\\/evidence\\/" in code
    assert 'uri === "" || uri === "/"' in code
    assert '"/auth/callback": true' in code
    assert '"/history": true' in code
    assert '"/research": true' in code
    assert '!lastSegment.includes(".")' in code
    assert 'request.uri = normalizedUri + "/index.html"' in code
    assert 'request.uri = "/404.html"' in code


def test_security_headers_are_explicit_and_apply_to_all_cache_behaviors() -> None:
    resources = _resources()
    policy = cast(
        dict[str, object],
        resources["PublicDemoSecurityHeadersPolicy"]["Properties"]["ResponseHeadersPolicyConfig"],
    )
    security = cast(dict[str, object], policy["SecurityHeadersConfig"])

    assert security["ContentTypeOptions"] == {"Override": True}
    assert security["FrameOptions"] == {"FrameOption": "DENY", "Override": True}
    assert security["StrictTransportSecurity"] == {
        "AccessControlMaxAgeSec": 31536000,
        "IncludeSubdomains": True,
        "Override": True,
        "Preload": True,
    }
    assert security["ReferrerPolicy"] == {
        "Override": True,
        "ReferrerPolicy": "strict-origin-when-cross-origin",
    }
    csp = cast(dict[str, object], security["ContentSecurityPolicy"])
    assert "object-src 'none'" in str(csp["ContentSecurityPolicy"])
    assert "frame-ancestors 'none'" in str(csp["ContentSecurityPolicy"])

    config = cast(
        dict[str, object],
        resources["PublicDemoDistribution"]["Properties"]["DistributionConfig"],
    )
    conditional_api = cast(list[object], config["CacheBehaviors"])[:2]
    api_behaviors: list[dict[str, object]] = []
    for conditional in conditional_api:
        assert isinstance(conditional, dict)
        api_behavior = cast(list[object], conditional["Fn::If"])[1]
        assert isinstance(api_behavior, dict)
        api_behaviors.append(api_behavior)
    behaviors = [
        cast(dict[str, object], config["DefaultCacheBehavior"]),
        *api_behaviors,
        *cast(list[dict[str, object]], config["CacheBehaviors"])[2:],
    ]
    assert all(
        behavior["ResponseHeadersPolicyId"] == {"Ref": "PublicDemoSecurityHeadersPolicy"}
        for behavior in behaviors
    )


def test_all_resources_are_retained_and_forbidden_public_features_are_absent() -> None:
    resources = _resources()
    assert resources
    assert all(resource["DeletionPolicy"] == "Retain" for resource in resources.values())
    assert all(resource["UpdateReplacePolicy"] == "Retain" for resource in resources.values())

    forbidden_types = {
        "AWS::IAM::Role",
        "AWS::Logs::LogGroup",
        "AWS::WAFv2::WebACL",
    }
    assert forbidden_types.isdisjoint(
        {cast(str, resource["Type"]) for resource in resources.values()}
    )

    distribution = cast(dict[str, object], resources["PublicDemoDistribution"]["Properties"])
    config = cast(dict[str, object], distribution["DistributionConfig"])
    assert "Aliases" not in config
    assert "Logging" not in config
    assert "WebACLId" not in config

    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "WebsiteConfiguration" not in template_text
    assert "PublicRead" not in template_text
    assert "AWS::IAM" not in template_text


def test_template_binds_the_exact_product_commit() -> None:
    template = _template()
    assert template["Parameters"] == {
        "ProductSha": {
            "Type": "String",
            "AllowedPattern": "^[0-9a-f]{40}$",
            "Description": "Exact Git commit used to build the static public demo",
        },
        "ApiOriginDomainName": {
            "Type": "String",
            "Default": "",
            "AllowedPattern": (
                "^$|^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\\.elb\\.amazonaws\\.com$"
            ),
        },
        "ApiOriginVerifySecretArn": {
            "Type": "String",
            "Default": "",
            "AllowedPattern": (
                "^$|^arn:aws:secretsmanager:ap-northeast-1:[0-9]{12}:secret:"
                "[A-Za-z0-9/_+=.@-]{1,512}$"
            ),
        },
        "CognitoDomainName": {
            "Type": "String",
            "Default": "",
            "AllowedPattern": (
                "^$|^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\\.auth\\.ap-northeast-1\\.amazoncognito\\.com$"
            ),
        },
        "PrivatePreviewApiNoCachePolicyId": {
            "Type": "String",
            "Default": "",
            "AllowedPattern": ("^$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
        },
        "PrivatePreviewApiOriginRequestPolicyId": {
            "Type": "String",
            "Default": "",
            "AllowedPattern": ("^$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
        },
    }
    assert template["Rules"] == {
        "ApiOriginRequiresVerificationSecret": {
            "Assertions": [
                {
                    "Assert": {
                        "Fn::Or": [
                            {"Fn::Equals": [{"Ref": "ApiOriginDomainName"}, ""]},
                            {
                                "Fn::Not": [
                                    {
                                        "Fn::Equals": [
                                            {"Ref": "ApiOriginVerifySecretArn"},
                                            "",
                                        ]
                                    }
                                ]
                            },
                        ]
                    },
                    "AssertDescription": (
                        "API origin requires the R2 runtime origin verification secret ARN."
                    ),
                },
                {
                    "Assert": {
                        "Fn::Or": [
                            {"Fn::Equals": [{"Ref": "ApiOriginVerifySecretArn"}, ""]},
                            {
                                "Fn::Not": [
                                    {
                                        "Fn::Equals": [
                                            {"Ref": "ApiOriginDomainName"},
                                            "",
                                        ]
                                    }
                                ]
                            },
                        ]
                    },
                    "AssertDescription": (
                        "API origin verification secret requires the R2 runtime ALB domain."
                    ),
                },
            ]
        },
        "PrivatePreviewPolicyIdsArePaired": {
            "Assertions": [
                {
                    "Assert": {
                        "Fn::Or": [
                            {
                                "Fn::Equals": [
                                    {"Ref": "PrivatePreviewApiNoCachePolicyId"},
                                    "",
                                ]
                            },
                            {
                                "Fn::Not": [
                                    {
                                        "Fn::Equals": [
                                            {"Ref": ("PrivatePreviewApiOriginRequestPolicyId")},
                                            "",
                                        ]
                                    }
                                ]
                            },
                        ]
                    },
                    "AssertDescription": (
                        "Existing R2 cache policy requires the matching origin request policy."
                    ),
                },
                {
                    "Assert": {
                        "Fn::Or": [
                            {
                                "Fn::Equals": [
                                    {"Ref": "PrivatePreviewApiOriginRequestPolicyId"},
                                    "",
                                ]
                            },
                            {
                                "Fn::Not": [
                                    {
                                        "Fn::Equals": [
                                            {"Ref": "PrivatePreviewApiNoCachePolicyId"},
                                            "",
                                        ]
                                    }
                                ]
                            },
                        ]
                    },
                    "AssertDescription": (
                        "Existing R2 origin request policy requires the matching cache policy."
                    ),
                },
                {
                    "Assert": {
                        "Fn::Or": [
                            {
                                "Fn::Equals": [
                                    {"Ref": "PrivatePreviewApiNoCachePolicyId"},
                                    "",
                                ]
                            },
                            {
                                "Fn::Not": [
                                    {
                                        "Fn::Equals": [
                                            {"Ref": "ApiOriginDomainName"},
                                            "",
                                        ]
                                    }
                                ]
                            },
                        ]
                    },
                    "AssertDescription": (
                        "Existing R2 policies may only be supplied with the R2 API origin."
                    ),
                },
            ]
        },
    }
    outputs = cast(dict[str, object], template["Outputs"])
    assert outputs["ProductSha"] == {
        "Description": "Exact product commit bound to this deployment",
        "Value": {"Ref": "ProductSha"},
    }
    resources = _resources()
    expected_tag = {"Key": "ProductSha", "Value": {"Ref": "ProductSha"}}
    assert expected_tag in resources["PublicDemoBucket"]["Properties"]["Tags"]
    assert expected_tag in resources["PublicDemoDistribution"]["Properties"]["Tags"]

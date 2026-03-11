"""Tests for Forge config schema models."""

import pytest
from forge_config.schema import (
    AgentDef,
    AgentsConfig,
    AgentWeaveConfig,
    AuthConfig,
    AuthType,
    ForgeConfig,
    ForgeMetadata,
    HTTPMethod,
    LiteLLMConfig,
    LiteLLMMode,
    LLMConfig,
    ManualTool,
    ManualToolAPI,
    OpenAPISource,
    ParameterDef,
    ParamType,
    PeerAgent,
    ResponseMapping,
    SecretRef,
    SecretSource,
    SecurityConfig,
    ToolsConfig,
    TrustLevel,
    TrustPolicy,
    Workflow,
    WorkflowStep,
)
from pydantic import ValidationError


class TestSecretRef:
    def test_env_secret(self) -> None:
        ref = SecretRef(source=SecretSource.ENV, name="MY_VAR")
        assert ref.source == SecretSource.ENV
        assert ref.name == "MY_VAR"
        assert ref.key is None

    def test_k8s_secret_requires_key(self) -> None:
        with pytest.raises(ValidationError, match="key is required"):
            SecretRef(source=SecretSource.K8S_SECRET, name="my-secret")

    def test_k8s_secret_with_key(self) -> None:
        ref = SecretRef(source=SecretSource.K8S_SECRET, name="my-secret", key="api-key")
        assert ref.key == "api-key"


class TestLiteLLMConfig:
    def test_embedded_no_endpoint(self) -> None:
        config = LiteLLMConfig(mode=LiteLLMMode.EMBEDDED)
        assert config.endpoint is None

    def test_sidecar_requires_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="endpoint is required"):
            LiteLLMConfig(mode=LiteLLMMode.SIDECAR)

    def test_external_with_endpoint(self) -> None:
        config = LiteLLMConfig(mode=LiteLLMMode.EXTERNAL, endpoint="http://litellm:4000")
        assert config.endpoint == "http://litellm:4000"

    def test_defaults(self) -> None:
        config = LiteLLMConfig()
        assert config.timeout == 30.0
        assert config.max_retries == 3
        assert config.model_list == []


class TestAuthConfig:
    def test_none_auth(self) -> None:
        auth = AuthConfig(type=AuthType.NONE)
        assert auth.type == AuthType.NONE

    def test_bearer_requires_token(self) -> None:
        with pytest.raises(ValidationError, match="token is required"):
            AuthConfig(type=AuthType.BEARER)

    def test_bearer_with_token(self) -> None:
        token = SecretRef(source=SecretSource.ENV, name="TOKEN")
        auth = AuthConfig(type=AuthType.BEARER, token=token)
        assert auth.token == token

    def test_api_key_requires_token(self) -> None:
        with pytest.raises(ValidationError, match="token is required"):
            AuthConfig(type=AuthType.API_KEY)

    def test_basic_requires_username_password(self) -> None:
        with pytest.raises(ValidationError, match="username and password"):
            AuthConfig(type=AuthType.BASIC)

    def test_basic_complete(self) -> None:
        auth = AuthConfig(
            type=AuthType.BASIC,
            username=SecretRef(source=SecretSource.ENV, name="USER"),
            password=SecretRef(source=SecretSource.ENV, name="PASS"),
        )
        assert auth.username is not None
        assert auth.password is not None


class TestManualTool:
    def test_minimal_tool(self) -> None:
        tool = ManualTool(
            name="test",
            description="A test tool",
            api=ManualToolAPI(url="https://example.com/api"),
        )
        assert tool.name == "test"
        assert tool.parameters == []
        assert tool.api.method == HTTPMethod.GET

    def test_tool_with_params(self) -> None:
        tool = ManualTool(
            name="search",
            description="Search something",
            parameters=[
                ParameterDef(name="query", type=ParamType.STRING, required=True),
                ParameterDef(name="limit", type=ParamType.INTEGER, required=False, default=10),
            ],
            api=ManualToolAPI(
                url="https://example.com/search",
                method=HTTPMethod.POST,
                body_template={"q": "{{ query }}", "limit": "{{ limit }}"},
            ),
        )
        assert len(tool.parameters) == 2
        assert tool.parameters[1].default == 10


class TestManualToolAPI:
    def test_url_only(self) -> None:
        api = ManualToolAPI(url="https://example.com/api")
        assert api.url == "https://example.com/api"
        assert api.resolved_url == "https://example.com/api"

    def test_base_url_and_endpoint(self) -> None:
        api = ManualToolAPI(
            base_url="https://api.openweathermap.org/data/2.5",
            endpoint="/weather",
        )
        assert api.resolved_url == "https://api.openweathermap.org/data/2.5/weather"

    def test_base_url_and_endpoint_strips_trailing_slash(self) -> None:
        api = ManualToolAPI(
            base_url="https://api.example.com/",
            endpoint="/items",
        )
        assert api.resolved_url == "https://api.example.com/items"

    def test_base_url_and_endpoint_override_url(self) -> None:
        api = ManualToolAPI(
            url="https://fallback.com",
            base_url="https://primary.com",
            endpoint="/v1",
        )
        assert api.resolved_url == "https://primary.com/v1"

    def test_requires_url_or_base_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="Either url or both base_url and endpoint"):
            ManualToolAPI()

    def test_base_url_alone_is_invalid(self) -> None:
        with pytest.raises(ValidationError, match="Either url or both base_url and endpoint"):
            ManualToolAPI(base_url="https://example.com")

    def test_endpoint_alone_is_invalid(self) -> None:
        with pytest.raises(ValidationError, match="Either url or both base_url and endpoint"):
            ManualToolAPI(endpoint="/weather")


class TestResponseMapping:
    def test_defaults(self) -> None:
        mapping = ResponseMapping()
        assert mapping.result_path == "$"
        assert mapping.error_path is None
        assert mapping.status_field is None
        assert mapping.field_map == {}

    def test_field_map(self) -> None:
        mapping = ResponseMapping(
            field_map={
                "temperature": "$.main.temp",
                "description": "$.weather[0].description",
            }
        )
        assert mapping.field_map["temperature"] == "$.main.temp"
        assert mapping.field_map["description"] == "$.weather[0].description"

    def test_field_map_with_existing_fields(self) -> None:
        mapping = ResponseMapping(
            result_path="$.data",
            error_path="$.error",
            status_field="status",
            field_map={"name": "$.data.name"},
        )
        assert mapping.result_path == "$.data"
        assert mapping.field_map["name"] == "$.data.name"


class TestOpenAPISource:
    def test_requires_url_or_path_or_spec(self) -> None:
        with pytest.raises(ValidationError, match="Either url, path, or spec"):
            OpenAPISource(name="test")

    def test_with_url(self) -> None:
        src = OpenAPISource(name="github", url="https://example.com/openapi.json")
        assert src.url is not None
        assert src.name == "github"

    def test_with_path(self) -> None:
        src = OpenAPISource(name="local", path="/etc/forge/spec.yaml")
        assert src.path is not None

    def test_with_spec_url(self) -> None:
        src = OpenAPISource(name="github", spec="https://api.github.com/openapi.json")
        assert src.url == "https://api.github.com/openapi.json"
        assert src.spec == "https://api.github.com/openapi.json"

    def test_with_spec_path(self) -> None:
        src = OpenAPISource(name="local", spec="/etc/forge/spec.yaml")
        assert src.path == "/etc/forge/spec.yaml"
        assert src.spec == "/etc/forge/spec.yaml"

    def test_namespace_field(self) -> None:
        src = OpenAPISource(
            name="github",
            url="https://example.com/openapi.json",
            namespace="github",
        )
        assert src.namespace == "github"
        assert src.prefix == "github"  # Synced from namespace

    def test_prefix_syncs_to_namespace(self) -> None:
        src = OpenAPISource(
            name="github",
            url="https://example.com/openapi.json",
            prefix="gh",
        )
        assert src.prefix == "gh"
        assert src.namespace == "gh"  # Synced from prefix

    def test_namespace_takes_precedence(self) -> None:
        src = OpenAPISource(
            name="github",
            url="https://example.com/openapi.json",
            namespace="github",
            prefix="gh",
        )
        # Both are explicitly set, no syncing needed
        assert src.namespace == "github"
        assert src.prefix == "gh"

    def test_include_tags(self) -> None:
        src = OpenAPISource(
            name="github",
            url="https://example.com/openapi.json",
            include_tags=["repos", "issues", "pulls"],
        )
        assert src.include_tags == ["repos", "issues", "pulls"]

    def test_include_operations(self) -> None:
        src = OpenAPISource(
            name="stripe",
            url="https://example.com/openapi.json",
            include_operations=["list_charges", "create_charge"],
        )
        assert src.include_operations == ["list_charges", "create_charge"]

    def test_include_defaults_to_empty(self) -> None:
        src = OpenAPISource(name="test", url="https://example.com/openapi.json")
        assert src.include_tags == []
        assert src.include_operations == []

    def test_route_map_preserved(self) -> None:
        src = OpenAPISource(
            name="github",
            url="https://example.com/openapi.json",
            route_map={"/repos": "list_repos"},
        )
        assert src.route_map == {"/repos": "list_repos"}


class TestToolsConfig:
    def test_new_field_names(self) -> None:
        config = ToolsConfig(
            openapi_sources=[OpenAPISource(name="test", url="https://example.com/openapi.json")],
            manual_tools=[
                ManualTool(
                    name="echo",
                    description="Echo",
                    api=ManualToolAPI(url="https://httpbin.org/post"),
                )
            ],
        )
        assert len(config.openapi_sources) == 1
        assert len(config.manual_tools) == 1

    def test_legacy_field_names(self) -> None:
        """Old field names (openapi, manual) should still work via model_validator."""
        config = ToolsConfig.model_validate(
            {
                "openapi": [{"name": "test", "url": "https://example.com/openapi.json"}],
                "manual": [
                    {
                        "name": "echo",
                        "description": "Echo",
                        "api": {"url": "https://httpbin.org/post"},
                    }
                ],
            }
        )
        assert len(config.openapi_sources) == 1
        assert len(config.manual_tools) == 1

    def test_backward_compat_properties(self) -> None:
        config = ToolsConfig(
            openapi_sources=[OpenAPISource(name="test", url="https://example.com/openapi.json")],
            manual_tools=[
                ManualTool(
                    name="echo",
                    description="Echo",
                    api=ManualToolAPI(url="https://httpbin.org/post"),
                )
            ],
        )
        # .openapi and .manual properties should work as aliases
        assert len(config.openapi) == 1
        assert len(config.manual) == 1
        assert config.openapi[0].name == "test"
        assert config.manual[0].name == "echo"


class TestWorkflow:
    def test_requires_steps(self) -> None:
        with pytest.raises(ValidationError):
            Workflow(name="test", description="test", steps=[])

    def test_valid_workflow(self) -> None:
        wf = Workflow(
            name="pipeline",
            description="A pipeline",
            steps=[
                WorkflowStep(tool="step1", output_as="result1"),
                WorkflowStep(tool="step2", params={"input": "{{ result1 }}"}),
            ],
        )
        assert len(wf.steps) == 2
        assert wf.steps[0].output_as == "result1"


class TestPeerAgent:
    def test_minimal_peer(self) -> None:
        peer = PeerAgent(name="data-forge", endpoint="https://data-forge.internal")
        assert peer.name == "data-forge"
        assert peer.endpoint == "https://data-forge.internal"
        assert peer.trust_level == TrustLevel.LOW
        assert peer.capabilities == []

    def test_full_peer(self) -> None:
        peer = PeerAgent(
            name="data-forge",
            endpoint="https://data-forge.hvs.internal",
            trust_level=TrustLevel.HIGH,
            capabilities=["data_query", "reporting"],
        )
        assert peer.trust_level == TrustLevel.HIGH
        assert peer.capabilities == ["data_query", "reporting"]

    def test_trust_level_enum(self) -> None:
        assert TrustLevel.HIGH == "high"
        assert TrustLevel.MEDIUM == "medium"
        assert TrustLevel.LOW == "low"

    def test_peer_from_dict(self) -> None:
        peer = PeerAgent.model_validate(
            {
                "name": "data-forge",
                "endpoint": "https://data-forge.internal",
                "trust_level": "medium",
                "capabilities": ["data_query"],
            }
        )
        assert peer.trust_level == TrustLevel.MEDIUM


class TestAgentsConfig:
    def test_defaults(self) -> None:
        config = AgentsConfig()
        assert config.default == "assistant"
        assert config.agents == []
        assert config.peers == []

    def test_with_peers(self) -> None:
        config = AgentsConfig(
            default="assistant",
            agents=[AgentDef(name="assistant", description="Helper")],
            peers=[
                PeerAgent(
                    name="data-forge",
                    endpoint="https://data-forge.internal",
                    trust_level=TrustLevel.HIGH,
                    capabilities=["data_query", "reporting"],
                )
            ],
        )
        assert len(config.peers) == 1
        assert config.peers[0].name == "data-forge"
        assert config.peers[0].trust_level == TrustLevel.HIGH


class TestAgentWeaveConfig:
    def test_defaults(self) -> None:
        config = AgentWeaveConfig()
        assert config.enabled is True
        assert config.identity_secret is None
        assert config.trust_policy == TrustPolicy.STRICT

    def test_with_identity_secret(self) -> None:
        config = AgentWeaveConfig(
            identity_secret="forge-identity-keypair",
            trust_policy=TrustPolicy.PERMISSIVE,
        )
        assert config.identity_secret == "forge-identity-keypair"
        assert config.trust_policy == TrustPolicy.PERMISSIVE

    def test_trust_policy_enum(self) -> None:
        assert TrustPolicy.STRICT == "strict"
        assert TrustPolicy.PERMISSIVE == "permissive"

    def test_trust_policy_from_string(self) -> None:
        config = AgentWeaveConfig.model_validate(
            {
                "trust_policy": "permissive",
            }
        )
        assert config.trust_policy == TrustPolicy.PERMISSIVE


class TestForgeConfig:
    def test_defaults(self) -> None:
        config = ForgeConfig()
        assert config.metadata.name == "forge"
        assert config.llm.default_model == "gpt-4o"
        assert config.security.rate_limit_rpm == 60
        assert config.agents.default == "assistant"

    def test_full_config(self) -> None:
        config = ForgeConfig(
            metadata=ForgeMetadata(name="test", environment="production"),
            llm=LLMConfig(default_model="claude-sonnet", temperature=0.3),
            tools=ToolsConfig(
                manual_tools=[
                    ManualTool(
                        name="echo",
                        description="Echo",
                        api=ManualToolAPI(url="https://httpbin.org/post", method=HTTPMethod.POST),
                    )
                ]
            ),
            security=SecurityConfig(
                agentweave=AgentWeaveConfig(enabled=False),
                rate_limit_rpm=120,
            ),
            agents=AgentsConfig(
                default="analyst",
                agents=[AgentDef(name="analyst", description="Data analyst")],
            ),
        )
        assert config.metadata.environment == "production"
        assert len(config.tools.manual_tools) == 1
        assert config.security.rate_limit_rpm == 120

    def test_full_config_with_plan_fields(self) -> None:
        """Test a config that uses all the new plan-aligned fields."""
        config = ForgeConfig(
            metadata=ForgeMetadata(name="forge-prod", environment="production"),
            tools=ToolsConfig(
                openapi_sources=[
                    OpenAPISource(
                        name="github",
                        spec="https://api.github.com/openapi.json",
                        namespace="github",
                        include_tags=["repos", "issues", "pulls"],
                    ),
                ],
                manual_tools=[
                    ManualTool(
                        name="weather",
                        description="Get weather",
                        parameters=[
                            ParameterDef(name="city", type=ParamType.STRING),
                        ],
                        api=ManualToolAPI(
                            base_url="https://api.openweathermap.org/data/2.5",
                            endpoint="/weather",
                            method=HTTPMethod.GET,
                            response_mapping=ResponseMapping(
                                field_map={
                                    "temperature": "$.main.temp",
                                    "description": "$.weather[0].description",
                                },
                            ),
                        ),
                    ),
                ],
            ),
            security=SecurityConfig(
                agentweave=AgentWeaveConfig(
                    identity_secret="forge-identity-keypair",
                    trust_policy=TrustPolicy.STRICT,
                ),
            ),
            agents=AgentsConfig(
                default="assistant",
                agents=[AgentDef(name="assistant", description="Main agent")],
                peers=[
                    PeerAgent(
                        name="data-forge",
                        endpoint="https://data-forge.hvs.internal",
                        trust_level=TrustLevel.HIGH,
                        capabilities=["data_query", "reporting"],
                    ),
                ],
            ),
        )
        # Verify OpenAPI source
        assert config.tools.openapi_sources[0].name == "github"
        assert config.tools.openapi_sources[0].namespace == "github"
        assert config.tools.openapi_sources[0].include_tags == ["repos", "issues", "pulls"]
        # Verify manual tool with base_url + endpoint
        weather_api = config.tools.manual_tools[0].api
        assert weather_api.resolved_url == ("https://api.openweathermap.org/data/2.5/weather")
        assert weather_api.response_mapping.field_map["temperature"] == "$.main.temp"
        # Verify security
        assert config.security.agentweave.identity_secret == "forge-identity-keypair"
        assert config.security.agentweave.trust_policy == TrustPolicy.STRICT
        # Verify peers
        assert len(config.agents.peers) == 1
        assert config.agents.peers[0].trust_level == TrustLevel.HIGH

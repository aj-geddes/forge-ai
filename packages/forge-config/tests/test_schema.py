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
    SecretRef,
    SecretSource,
    SecurityConfig,
    ToolsConfig,
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


class TestOpenAPISource:
    def test_requires_url_or_path(self) -> None:
        with pytest.raises(ValidationError, match="Either url or path"):
            OpenAPISource()

    def test_with_url(self) -> None:
        src = OpenAPISource(url="https://example.com/openapi.json")
        assert src.url is not None

    def test_with_path(self) -> None:
        src = OpenAPISource(path="/etc/forge/spec.yaml")
        assert src.path is not None


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
                manual=[
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
        assert len(config.tools.manual) == 1
        assert config.security.rate_limit_rpm == 120

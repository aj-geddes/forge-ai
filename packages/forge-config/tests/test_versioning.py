"""Tests for config surface versioning."""

from forge_config.schema import ForgeConfig, ForgeMetadata, LLMConfig
from forge_config.versioning import compute_surface_version, has_surface_changed


class TestVersioning:
    def test_same_config_same_hash(self) -> None:
        c1 = ForgeConfig()
        c2 = ForgeConfig()
        assert compute_surface_version(c1) == compute_surface_version(c2)

    def test_different_tools_different_hash(self) -> None:
        from forge_config.schema import ManualTool, ManualToolAPI, ToolsConfig

        c1 = ForgeConfig()
        c2 = ForgeConfig(
            tools=ToolsConfig(
                manual=[
                    ManualTool(
                        name="new_tool",
                        description="test",
                        api=ManualToolAPI(url="https://example.com"),
                    )
                ]
            )
        )
        assert compute_surface_version(c1) != compute_surface_version(c2)

    def test_metadata_change_ignored(self) -> None:
        c1 = ForgeConfig(metadata=ForgeMetadata(name="alpha"))
        c2 = ForgeConfig(metadata=ForgeMetadata(name="beta"))
        assert compute_surface_version(c1) == compute_surface_version(c2)

    def test_has_surface_changed(self) -> None:
        c1 = ForgeConfig()
        version = compute_surface_version(c1)

        assert not has_surface_changed(version, ForgeConfig())
        assert has_surface_changed(
            version,
            ForgeConfig(llm=LLMConfig(default_model="different-model")),
        )

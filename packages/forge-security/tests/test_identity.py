"""Tests for forge_security.identity."""

from forge_security.identity import (
    ForgeIdentityManager,
    ForgeKeypair,
    MockIdentityProvider,
)


class TestForgeKeypair:
    def test_generate_produces_keypair(self):
        kp = ForgeKeypair.generate()
        assert kp.private_key is not None
        assert kp.public_key is not None

    def test_private_bytes_length(self):
        kp = ForgeKeypair.generate()
        assert len(kp.private_bytes()) == 32

    def test_public_bytes_length(self):
        kp = ForgeKeypair.generate()
        assert len(kp.public_bytes()) == 32

    def test_two_keypairs_differ(self):
        kp1 = ForgeKeypair.generate()
        kp2 = ForgeKeypair.generate()
        assert kp1.public_bytes() != kp2.public_bytes()


class TestMockIdentityProvider:
    async def test_default_spiffe_id(self):
        provider = MockIdentityProvider()
        identity = await provider.get_identity()
        assert identity == "spiffe://forge.test/agent/mock"

    async def test_custom_spiffe_id(self):
        provider = MockIdentityProvider(spiffe_id="spiffe://custom/agent/test")
        assert await provider.get_identity() == "spiffe://custom/agent/test"

    async def test_get_svid(self):
        provider = MockIdentityProvider()
        svid = await provider.get_svid()
        assert "mock-svid" in svid

    async def test_get_trust_bundle(self):
        provider = MockIdentityProvider()
        bundle = await provider.get_trust_bundle()
        assert bundle == "mock-trust-bundle"

    async def test_create_tls_context(self):
        provider = MockIdentityProvider()
        ctx = await provider.create_tls_context()
        assert ctx is not None


class TestForgeIdentityManager:
    async def test_test_mode_enabled_when_no_provider(self):
        mgr = ForgeIdentityManager()
        assert mgr.is_test_mode is True

    async def test_get_identity_test_mode(self):
        mgr = ForgeIdentityManager(trust_domain="example.com", agent_name="test-agent")
        identity = await mgr.get_identity()
        assert identity == "spiffe://example.com/agent/test-agent"

    async def test_get_svid_test_mode(self):
        mgr = ForgeIdentityManager()
        svid = await mgr.get_svid()
        assert svid is not None

    async def test_get_or_create_keypair(self):
        mgr = ForgeIdentityManager()
        kp1 = mgr.get_or_create_keypair()
        kp2 = mgr.get_or_create_keypair()
        # Same keypair should be returned on repeated calls
        assert kp1 is kp2

    async def test_get_public_key(self):
        mgr = ForgeIdentityManager()
        pub = mgr.get_public_key()
        assert pub is not None

    async def test_custom_provider_disables_test_mode(self):
        provider = MockIdentityProvider(spiffe_id="spiffe://prod/agent/real")
        mgr = ForgeIdentityManager(provider=provider)
        assert mgr.is_test_mode is False
        assert await mgr.get_identity() == "spiffe://prod/agent/real"

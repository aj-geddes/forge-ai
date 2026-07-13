"""Tests for OIDC HTTP client CA trust (private-CA bug fix).

Dex (`dex.hvslocal`) presents a certificate signed by the hvslocal private
Root CA, which is not in the public CA bundle httpx/certifi trusts by
default. ``_oidc_ca_verify`` resolves a CA bundle path to pass as httpx's
``verify=`` kwarg so the gateway's OIDC HTTP clients (discovery, JWKS, and
the Dex token-exchange) can trust that private CA -- without ever disabling
verification (``verify=False``) entirely.
"""

from __future__ import annotations

import datetime
import logging
import ssl
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from forge_gateway.app import _httpx_verify_kwarg, _oidc_ca_verify


def _self_signed_ca_pem() -> bytes:
    """A real (self-signed, throwaway) CA certificate in PEM form -- needed
    because ``ssl.create_default_context(cafile=...)`` parses its contents
    immediately, unlike the plain string a bare ``verify=<path>`` kwarg
    used to accept without ever reading the file."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge-test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


# ---------------------------------------------------------------------------
# _oidc_ca_verify() resolution logic
# ---------------------------------------------------------------------------


class TestOidcCaVerifyResolution:
    def test_forge_oidc_ca_bundle_pointing_at_existing_file_returns_that_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FORGE_OIDC_CA_BUNDLE pointing at an existing file makes the OIDC
        client verify against that path."""
        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n")
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", str(bundle))
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        assert _oidc_ca_verify() == str(bundle)

    def test_ssl_cert_file_used_as_fallback_when_forge_oidc_ca_bundle_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SSL_CERT_FILE is used as fallback when FORGE_OIDC_CA_BUNDLE is unset."""
        ssl_cert_file = tmp_path / "system-ca.pem"
        ssl_cert_file.write_text("cert bytes")
        monkeypatch.delenv("FORGE_OIDC_CA_BUNDLE", raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", str(ssl_cert_file))

        assert _oidc_ca_verify() == str(ssl_cert_file)

    def test_forge_oidc_ca_bundle_takes_priority_over_ssl_cert_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both are set and both exist, FORGE_OIDC_CA_BUNDLE wins."""
        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_text("bundle")
        ssl_cert_file = tmp_path / "system-ca.pem"
        ssl_cert_file.write_text("system")
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", str(bundle))
        monkeypatch.setenv("SSL_CERT_FILE", str(ssl_cert_file))

        assert _oidc_ca_verify() == str(bundle)

    def test_neither_env_var_set_defaults_to_verify_true_not_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Neither set -> verify defaults to True (not False -- must never
        silently disable certificate verification)."""
        monkeypatch.delenv("FORGE_OIDC_CA_BUNDLE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        result = _oidc_ca_verify()

        assert result is True
        assert result is not False

    def test_configured_but_missing_bundle_file_does_not_become_verify_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A configured-but-missing FORGE_OIDC_CA_BUNDLE path must never
        silently become verify=False -- it falls through to the safe
        default instead (True, when no other bundle is available)."""
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", "/no/such/path/hvslocal-ca.crt")
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        result = _oidc_ca_verify()

        assert result is not False
        assert result is True

    def test_configured_but_missing_bundle_falls_through_to_ssl_cert_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing FORGE_OIDC_CA_BUNDLE still allows a valid SSL_CERT_FILE
        to be used, per the documented if/elif/else resolution order."""
        ssl_cert_file = tmp_path / "system-ca.pem"
        ssl_cert_file.write_text("system")
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", "/no/such/path/hvslocal-ca.crt")
        monkeypatch.setenv("SSL_CERT_FILE", str(ssl_cert_file))

        assert _oidc_ca_verify() == str(ssl_cert_file)

    def test_missing_bundle_warns_instead_of_silently_ignoring(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A configured-but-missing bundle path logs a warning so the
        misconfiguration is visible, rather than failing silently."""
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", "/no/such/path/hvslocal-ca.crt")
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        with caplog.at_level(logging.WARNING, logger="forge.gateway"):
            _oidc_ca_verify()

        assert any("/no/such/path/hvslocal-ca.crt" in r.message for r in caplog.records)

    def test_configured_bundle_logs_at_info_which_bundle_is_in_use(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a bundle is configured and used, it's logged at INFO (path
        only -- it isn't a secret)."""
        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_text("bundle")
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", str(bundle))
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        with caplog.at_level(logging.INFO, logger="forge.gateway"):
            _oidc_ca_verify()

        assert any(str(bundle) in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# The client actually constructed in _init_auth uses the resolved bundle
# ---------------------------------------------------------------------------


class TestInitAuthHttpClientUsesCaBundle:
    async def test_init_auth_constructs_oidc_http_client_with_resolved_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_init_auth must build its httpx.AsyncClient with verify= set to
        the resolved CA bundle path, not the httpx default."""
        from forge_config.schema import ForgeConfig
        from forge_gateway import security
        from forge_gateway.app import _init_auth

        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_bytes(_self_signed_ca_pem())
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", str(bundle))
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=OSError("no network in unit test"))
        mock_client.aclose = AsyncMock()

        with patch("forge_gateway.app.httpx.AsyncClient", return_value=mock_client) as ctor:
            await _init_auth(ForgeConfig())
            try:
                ctor.assert_called_once()
                verify_kwarg = ctor.call_args.kwargs.get("verify")
                # Not a bare path string (that's the deprecated httpx form) --
                # an SSLContext pinned to the resolved bundle.
                assert isinstance(verify_kwarg, ssl.SSLContext)
                assert not isinstance(verify_kwarg, str)
            finally:
                await security.shutdown_auth()
                security.reset_auth()

    async def test_init_auth_never_passes_verify_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No configuration of this helper may ever result in verify=False
        being passed to the OIDC httpx client -- that would defeat TLS
        verification entirely."""
        from forge_config.schema import ForgeConfig
        from forge_gateway import security
        from forge_gateway.app import _init_auth

        monkeypatch.delenv("FORGE_OIDC_CA_BUNDLE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=OSError("no network in unit test"))
        mock_client.aclose = AsyncMock()

        with patch("forge_gateway.app.httpx.AsyncClient", return_value=mock_client) as ctor:
            await _init_auth(ForgeConfig())
            try:
                assert ctor.call_args.kwargs.get("verify") is not False
            finally:
                await security.shutdown_auth()
                security.reset_auth()


# ---------------------------------------------------------------------------
# Token-exchange client (routes/auth.py) uses the same shared, CA-aware client
# ---------------------------------------------------------------------------


class TestTokenExchangeUsesSameCaBundle:
    async def test_token_exchange_client_uses_same_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The token-exchange call in routes/auth.py must use the exact same
        CA-aware httpx client that discovery/JWKS use -- not a second,
        unconfigured client -- so it also trusts the private CA."""
        from forge_config.schema import ForgeConfig
        from forge_gateway import security
        from forge_gateway.app import _init_auth
        from forge_gateway.routes.auth import _exchange_code

        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_bytes(_self_signed_ca_pem())
        monkeypatch.setenv("FORGE_OIDC_CA_BUNDLE", str(bundle))
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        real_client = MagicMock()
        real_client.post = AsyncMock()
        real_client.aclose = AsyncMock()

        with patch("forge_gateway.app.httpx.AsyncClient", return_value=real_client) as ctor:
            await _init_auth(ForgeConfig())
            try:
                # The client used by _exchange_code (via security.get_http_client())
                # must be the exact same instance constructed with the CA bundle.
                exchange_client = security.get_http_client()
                assert exchange_client is real_client
                assert isinstance(ctor.call_args.kwargs.get("verify"), ssl.SSLContext)

                real_client.post.return_value.raise_for_status = MagicMock()
                real_client.post.return_value.json = MagicMock(return_value={"ok": True})

                await _exchange_code(
                    http_client=exchange_client,
                    token_endpoint="https://dex.hvslocal/dex/token",
                    code="abc",
                    redirect_uri="https://forgeai.hvslocal/auth/callback",
                    code_verifier="verifier",
                    client_id="forge-ai",
                )
                real_client.post.assert_awaited_once()
            finally:
                await security.shutdown_auth()
                security.reset_auth()


# ---------------------------------------------------------------------------
# _httpx_verify_kwarg() -- avoids httpx's verify=<str> DeprecationWarning
# ---------------------------------------------------------------------------


class TestHttpxVerifyKwarg:
    def test_bundle_path_becomes_ssl_context(self, tmp_path: Path) -> None:
        """A resolved bundle path is converted to an ssl.SSLContext, not
        passed through as a bare string."""
        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_bytes(_self_signed_ca_pem())

        result = _httpx_verify_kwarg(str(bundle))

        assert isinstance(result, ssl.SSLContext)

    def test_true_passes_through_unchanged(self) -> None:
        """The 'no bundle configured' sentinel (True) is untouched."""
        assert _httpx_verify_kwarg(True) is True

    def test_never_returns_false(self) -> None:
        """This helper must never produce verify=False -- that would
        disable TLS verification entirely."""
        assert _httpx_verify_kwarg(True) is not False

    def test_constructing_httpx_client_with_result_emits_no_deprecation_warning(
        self, tmp_path: Path
    ) -> None:
        """The actual regression this fix addresses: constructing
        httpx.AsyncClient(verify=...) with the resolved value must not
        raise httpx's 'verify=<str> is deprecated' DeprecationWarning."""
        import httpx

        bundle = tmp_path / "hvslocal-ca.crt"
        bundle.write_bytes(_self_signed_ca_pem())

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            client = httpx.AsyncClient(verify=_httpx_verify_kwarg(str(bundle)))
        assert client is not None

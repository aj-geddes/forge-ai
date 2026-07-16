"""Tests for the SSRF host/IP classifier plane (ADR-0006)."""

from __future__ import annotations

import ipaddress
import socket

import pytest
from forge_config.schema import EgressAction, EgressPolicy
from forge_security.egress.classify import (
    candidate_ips,
    is_blocked_hostname,
    is_internal_ip,
    validate_endpoint,
)


class TestIsInternalIpMultiCloudMetadata:
    """The blocklist must cover the full multi-cloud metadata set, not just
    the AWS 169.254.169.254 IMDS."""

    @pytest.mark.parametrize(
        ("addr", "label"),
        [
            ("169.254.169.254", "AWS/GCP IMDS"),
            ("100.100.100.200", "Alibaba metadata"),
            ("192.0.0.192", "Oracle metadata"),
            ("fd00:ec2::254", "AWS IPv6 IMDS"),
            ("127.0.0.1", "loopback"),
            ("10.0.0.1", "private 10/8"),
            ("172.16.5.5", "private 172.16/12"),
            ("192.168.1.1", "private 192.168/16"),
            ("::1", "IPv6 loopback"),
            ("fd12::1", "IPv6 ULA"),
            ("fe80::1", "IPv6 link-local"),
            ("0.0.0.0", "unspecified"),  # noqa: S104
        ],
    )
    def test_is_internal_ip_covers_multicloud_metadata(self, addr: str, label: str) -> None:
        assert is_internal_ip(ipaddress.ip_address(addr)) is True, label

    @pytest.mark.parametrize("addr", ["93.184.216.34", "203.0.113.10", "8.8.8.8"])
    def test_public_ips_are_not_internal(self, addr: str) -> None:
        assert is_internal_ip(ipaddress.ip_address(addr)) is False


class TestCandidateIpsCanonicalization:
    """candidate_ips must canonicalize every numeric encoding so an internal
    address cannot slip past disguised as decimal/hex/octal/short/mapped."""

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("2852039166", "169.254.169.254"),  # 32-bit decimal
            ("2130706433", "127.0.0.1"),
            ("0x7f000001", "127.0.0.1"),  # hex
            ("0177.0.0.1", "127.0.0.1"),  # octal first octet
            ("127.1", "127.0.0.1"),  # short-dotted
        ],
    )
    def test_candidate_ips_canonicalizes_encodings(self, host: str, expected: str) -> None:
        cands = candidate_ips(host)
        assert ipaddress.ip_address(expected) in cands

    def test_ipv4_mapped_ipv6_expands_embedded_ipv4(self) -> None:
        cands = candidate_ips("::ffff:169.254.169.254")
        assert ipaddress.ip_address("169.254.169.254") in cands

    def test_unresolvable_name_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a: object, **_k: object) -> list:
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        assert candidate_ips("does-not-exist.invalid") == []


class TestIsBlockedHostname:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "kubernetes.default.svc",
            "metadata.google.internal",
            "foo.local",
            "svc.cluster.local",
            "anything.internal",
            "",
        ],
    )
    def test_blocked(self, host: str) -> None:
        assert is_blocked_hostname(host) is True

    @pytest.mark.parametrize("host", ["api.example.com", "example.org"])
    def test_allowed(self, host: str) -> None:
        assert is_blocked_hostname(host) is False


class TestValidateEndpointLegacyParity:
    """With policy=None, validate_endpoint must behave exactly like the legacy
    validate_peer_endpoint the gateway re-exports."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.1:8080",
            "http://169.254.169.254/",
            "http://100.100.100.200/",  # newly added Alibaba
            "http://192.0.0.192/",  # newly added Oracle
            "http://2852039166/",  # decimal IMDS
            "http://0x7f000001/",
            "http://[::ffff:169.254.169.254]/",
            "http://localhost:8080",
            "http://svc.internal:8080",
            "not-a-url",
        ],
    )
    def test_rejects_internal(self, url: str) -> None:
        assert validate_endpoint(url) is False

    @pytest.mark.parametrize("url", ["http://203.0.113.1:8080", "https://api.example.com"])
    def test_allows_public(self, url: str) -> None:
        assert validate_endpoint(url) is True

    def test_resolve_then_classify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(host: str, *_a: object, **_k: object) -> list:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.11.12.13", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        assert validate_endpoint("http://rebind.attacker.example/") is False


class TestValidateEndpointPolicyBranch:
    """A supplied policy may only ever tighten (True -> False), never loosen."""

    def test_require_https_rejects_http(self) -> None:
        policy = EgressPolicy(require_https=True)
        assert validate_endpoint("http://api.example.com", policy) is False
        assert validate_endpoint("https://api.example.com", policy) is True

    def test_allowlist_rejects_unlisted(self) -> None:
        policy = EgressPolicy(allowed_hosts=["api.example.com", "*.trusted.com"])
        assert validate_endpoint("https://api.example.com", policy) is True
        assert validate_endpoint("https://svc.trusted.com", policy) is True
        assert validate_endpoint("https://evil.example", policy) is False

    def test_policy_never_loosens(self) -> None:
        # An internal target stays rejected even with a permissive policy.
        policy = EgressPolicy(require_https=False, allowed_hosts=[])
        assert validate_endpoint("http://169.254.169.254/", policy) is False

    def test_disabled_policy_is_ignored(self) -> None:
        policy = EgressPolicy(enabled=False, require_https=True, default_action=EgressAction.DROP)
        # http allowed because policy disabled -> parity with policy=None.
        assert validate_endpoint("http://api.example.com", policy) is True

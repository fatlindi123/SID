import asyncio
import ipaddress

import pytest

from app.core import url_security
from app.core.url_security import UnsafeTargetUrl, assert_public_http_url, parse_http_url


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "http://localhost",
        "https://service.local",
        "https://user:pass@example.com",
        "https://example.com:0",
    ],
)
def test_rejects_unsafe_or_invalid_target_syntax(value: str) -> None:
    with pytest.raises(UnsafeTargetUrl):
        parse_http_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1",
        "http://10.0.0.4",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
    ],
)
def test_rejects_private_ip_literals(value: str) -> None:
    with pytest.raises(UnsafeTargetUrl):
        asyncio.run(assert_public_http_url(value))


def test_rejects_dns_names_that_resolve_to_private_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_security,
        "resolve_host_addresses",
        lambda host, port: {ipaddress.ip_address("192.168.1.12")},
    )

    with pytest.raises(UnsafeTargetUrl, match="private or reserved"):
        asyncio.run(assert_public_http_url("https://public-looking.example"))


def test_rejects_mixed_public_and_private_dns_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_security,
        "resolve_host_addresses",
        lambda host, port: {
            ipaddress.ip_address("93.184.216.34"),
            ipaddress.ip_address("10.4.3.2"),
        },
    )

    with pytest.raises(UnsafeTargetUrl, match="private or reserved"):
        asyncio.run(assert_public_http_url("https://mixed.example"))


def test_accepts_public_dns_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_security,
        "resolve_host_addresses",
        lambda host, port: {ipaddress.ip_address("93.184.216.34")},
    )

    assert asyncio.run(assert_public_http_url("https://docs.example")) == "https://docs.example"


def test_normalizes_url_fragments_and_default_ports() -> None:
    assert url_security.normalized_http_url("HTTPS://EXAMPLE.COM:443/guide#part") == "https://example.com/guide"

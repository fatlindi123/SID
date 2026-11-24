"""Public HTTP URL validation used before and during browser navigation."""

import asyncio
import ipaddress
import re
import socket
from urllib.parse import SplitResult, urlsplit


class UnsafeTargetUrl(ValueError):
    """Raised when a target or browser request could reach a non-public host."""


def parse_http_url(value: str) -> tuple[SplitResult, str, int]:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise UnsafeTargetUrl("Enter a valid HTTP or HTTPS URL.")
    if value != value.strip() or any(ord(char) < 32 for char in value) or "\\" in value:
        raise UnsafeTargetUrl("The URL contains invalid characters.")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeTargetUrl("The URL is malformed.") from exc

    if scheme not in {"http", "https"}:
        raise UnsafeTargetUrl("Only HTTP and HTTPS URLs are allowed.")
    if not parsed.netloc or not host:
        raise UnsafeTargetUrl("The URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeTargetUrl("URLs containing credentials are not allowed.")

    normalized_host = host.rstrip(".").lower()
    if not normalized_host:
        raise UnsafeTargetUrl("The URL must include a hostname.")
    try:
        normalized_host = normalized_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeTargetUrl("The hostname is invalid.") from exc
    if (
        normalized_host == "localhost"
        or normalized_host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
    ):
        raise UnsafeTargetUrl("Local and internal hostnames are not allowed.")
    if "%" in normalized_host:
        raise UnsafeTargetUrl("Scoped IP addresses are not allowed.")
    try:
        ip_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        ip_address = None
    if ip_address is not None:
        normalized_host = ip_address.compressed
    else:
        labels = normalized_host.split(".")
        if (
            len(normalized_host) > 253
            or any(
                not label
                or len(label) > 63
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                for label in labels
            )
        ):
            raise UnsafeTargetUrl("The hostname is invalid.")

    effective_port = port or (443 if scheme == "https" else 80)
    if port == 0:
        raise UnsafeTargetUrl("The URL port is invalid.")
    return parsed, normalized_host, effective_port


def resolve_host_addresses(host: str, port: int) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeTargetUrl("The hostname could not be resolved.") from exc
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for answer in answers:
        raw_address = answer[4][0].split("%", 1)[0]
        try:
            addresses.add(ipaddress.ip_address(raw_address))
        except ValueError as exc:
            raise UnsafeTargetUrl("The hostname resolved to an invalid address.") from exc
    if not addresses:
        raise UnsafeTargetUrl("The hostname did not resolve to an address.")
    if any(not address.is_global for address in addresses):
        raise UnsafeTargetUrl("The URL resolves to a private or reserved network address.")
    return addresses


async def assert_public_http_url(value: str) -> str:
    """Validate syntax and ensure every DNS answer is a globally routable address."""
    _, host, port = parse_http_url(value)
    await resolve_public_addresses(host, port)
    return value


async def resolve_public_addresses(
    host: str, port: int
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses = await asyncio.to_thread(resolve_host_addresses, host, port)
    if any(not address.is_global for address in addresses):
        raise UnsafeTargetUrl("The URL resolves to a private or reserved network address.")
    return addresses


def normalized_http_url(value: str) -> str:
    parsed, host, port = parse_http_url(value)
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    host_for_url = f"[{host}]" if ":" in host else host
    netloc = host_for_url if port is None or port == default_port else f"{host_for_url}:{port}"
    path = parsed.path or "/"
    return parsed._replace(scheme=scheme, netloc=netloc, path=path, fragment="").geturl()


def same_origin(left: str, right: str) -> bool:
    try:
        left_parts, left_host, left_port = parse_http_url(left)
        right_parts, right_host, right_port = parse_http_url(right)
    except UnsafeTargetUrl:
        return False
    return (
        left_parts.scheme.lower() == right_parts.scheme.lower()
        and left_host == right_host
        and left_port == right_port
    )

"""Loopback-only proxy that pins each upstream connection to a validated IP."""

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from app.core.url_security import (
    UnsafeTargetUrl,
    parse_http_url,
    resolve_public_addresses,
)

logger = logging.getLogger("software_intelligence.egress")
Address = ipaddress.IPv4Address | ipaddress.IPv6Address
AddressResolver = Callable[[str, int], Awaitable[set[Address]]]
MAX_HEADER_BYTES = 64 * 1024
PROXY_TIMEOUT_SECONDS = 30


class SafeEgressProxy:
    def __init__(self, address_resolver: AddressResolver = resolve_public_addresses) -> None:
        self.address_resolver = address_resolver
        self._server: asyncio.Server | None = None
        self.server_url: str | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        address = self._server.sockets[0].getsockname()
        self.server_url = f"http://127.0.0.1:{address[1]}"

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.server_url = None

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            request_line = await asyncio.wait_for(client_reader.readline(), PROXY_TIMEOUT_SECONDS)
            if len(request_line) > 8192:
                raise UnsafeTargetUrl("Proxy request line is too long.")
            parts = request_line.decode("latin-1").strip().split(" ", 2)
            if len(parts) != 3:
                raise UnsafeTargetUrl("Malformed proxy request.")
            method, target, version = parts
            if method == "CONNECT":
                await self._read_headers(client_reader)
                host, port = self._parse_authority(target, 443)
                upstream_reader, upstream_writer = await self._open_upstream(host, port, "https")
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
                await self._tunnel(client_reader, client_writer, upstream_reader, upstream_writer)
                return

            if method not in {"GET", "HEAD"}:
                raise UnsafeTargetUrl("Only GET and HEAD requests are allowed.")
            parsed = urlsplit(target)
            if parsed.scheme.lower() != "http" or not parsed.hostname:
                raise UnsafeTargetUrl("The proxy only accepts absolute HTTP request URLs.")
            _, host, port = parse_http_url(target)
            headers = await self._read_headers(client_reader)
            host_header = next(
                (value for name, value in headers if name.lower() == "host"), None
            )
            if host_header and host_header.lower() != parsed.netloc.lower():
                raise UnsafeTargetUrl("The HTTP Host header does not match the request URL.")
            upstream_reader, upstream_writer = await self._open_upstream(host, port, "http")
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            upstream_writer.write(f"{method} {path} {version}\r\n".encode("latin-1"))
            filtered = [
                (name, value)
                for name, value in headers
                if name.lower() not in {"connection", "proxy-connection", "proxy-authorization"}
            ]
            if not host_header:
                filtered.append(("Host", parsed.netloc))
            for name, value in filtered:
                upstream_writer.write(f"{name}: {value}\r\n".encode("latin-1"))
            upstream_writer.write(b"Connection: close\r\n\r\n")
            await upstream_writer.drain()
            await self._relay_response(upstream_reader, client_writer)
        except (UnsafeTargetUrl, ValueError, asyncio.TimeoutError) as exc:
            logger.info("upstream_request_blocked", extra={"reason": str(exc)[:160]})
            await self._send_error(client_writer, 403, "Forbidden")
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            logger.debug("upstream_connection_failed", extra={"reason": type(exc).__name__})
            await self._send_error(client_writer, 502, "Bad Gateway")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("safe_proxy_request_failed")
            await self._send_error(client_writer, 502, "Bad Gateway")
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except OSError:
                    pass
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except OSError:
                pass

    async def _open_upstream(
        self, host: str, port: int, scheme: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        authority_host = f"[{host}]" if ":" in host else host
        validation_url = f"{scheme}://{authority_host}:{port}/"
        _, normalized_host, validated_port = parse_http_url(validation_url)
        if validated_port != port:
            raise UnsafeTargetUrl("The upstream port is invalid.")
        addresses = await self.address_resolver(normalized_host, port)
        if not addresses:
            raise UnsafeTargetUrl("The hostname did not resolve to an address.")
        last_error: OSError | None = None
        for address in addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(
                        str(address),
                        port,
                        family=socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                    ),
                    PROXY_TIMEOUT_SECONDS,
                )
            except OSError as exc:
                last_error = exc
        raise ConnectionError("Could not connect to any validated address.") from last_error

    @staticmethod
    def _parse_authority(authority: str, default_port: int) -> tuple[str, int]:
        if authority.startswith("["):
            closing = authority.find("]")
            if closing < 0:
                raise UnsafeTargetUrl("Malformed IPv6 CONNECT authority.")
            host = authority[1:closing]
            suffix = authority[closing + 1 :]
            if suffix and not suffix.startswith(":"):
                raise UnsafeTargetUrl("Malformed CONNECT authority.")
            port = int(suffix[1:]) if suffix else default_port
            return host, port
        if ":" in authority:
            host, raw_port = authority.rsplit(":", 1)
            return host, int(raw_port)
        return authority, default_port

    @staticmethod
    async def _read_headers(
        reader: asyncio.StreamReader,
    ) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        consumed = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), PROXY_TIMEOUT_SECONDS)
            consumed += len(line)
            if consumed > MAX_HEADER_BYTES:
                raise UnsafeTargetUrl("Proxy request headers are too large.")
            if line in {b"\r\n", b"\n", b""}:
                return headers
            decoded = line.decode("latin-1").strip()
            if ":" not in decoded:
                raise UnsafeTargetUrl("Malformed HTTP request header.")
            name, value = decoded.split(":", 1)
            headers.append((name.strip(), value.strip()))

    @staticmethod
    async def _relay_response(
        upstream_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        async with asyncio.timeout(PROXY_TIMEOUT_SECONDS):
            while data := await upstream_reader.read(64 * 1024):
                client_writer.write(data)
                await client_writer.drain()

    @staticmethod
    async def _tunnel(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def relay(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            while data := await reader.read(64 * 1024):
                writer.write(data)
                await writer.drain()

        directions = {
            asyncio.create_task(relay(client_reader, upstream_writer)),
            asyncio.create_task(relay(upstream_reader, client_writer)),
        }
        done, pending = await asyncio.wait(
            directions, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)

    @staticmethod
    async def _send_error(writer: asyncio.StreamWriter, status: int, message: str) -> None:
        body = f"{status} {message}\n".encode("ascii")
        try:
            headers = (
                f"HTTP/1.1 {status} {message}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "Content-Type: text/plain\r\n\r\n"
            ).encode("ascii")
            writer.write(headers + body)
            await writer.drain()
        except (ConnectionError, OSError):
            return

import asyncio

from app.services.safe_egress_proxy import SafeEgressProxy


def test_proxy_rejects_loopback_connect_requests() -> None:
    async def run() -> bytes:
        proxy = SafeEgressProxy()
        await proxy.start()
        assert proxy.server_url is not None
        port = int(proxy.server_url.rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"CONNECT 127.0.0.1:80 HTTP/1.1\r\n"
            b"Host: 127.0.0.1:80\r\n"
            b"\r\n"
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=2)
        writer.close()
        await writer.wait_closed()
        await proxy.close()
        return response

    assert asyncio.run(run()).startswith(b"HTTP/1.1 403")

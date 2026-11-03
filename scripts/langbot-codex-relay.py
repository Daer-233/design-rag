"""Loopback-published TCP relay to the authenticated official Codex Daemon Hub."""
import asyncio


async def relay(reader, writer):
    upstream = None
    tasks = []
    try:
        remote, upstream = await asyncio.open_connection('langbot_plugin_runtime', 8768)

        async def copy(source, target):
            while data := await source.read(65536):
                target.write(data)
                await target.drain()

        tasks = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(remote, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (ConnectionError, OSError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for stream in (writer, upstream):
            if stream:
                stream.close()
                try:
                    await stream.wait_closed()
                except (ConnectionError, OSError):
                    pass


async def main():
    server = await asyncio.start_server(relay, '0.0.0.0', 8768)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())

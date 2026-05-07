import aiohttp
import asyncio


async def fetch_status_invalid():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.github.com") as response:
            print(response.status_code)


if __name__ == "__main__":
    asyncio.run(fetch_status_invalid())

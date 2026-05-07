import aiohttp
import asyncio


async def fetch_text_invalid():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.github.com") as response:
            content = response.text()
            print(content)


if __name__ == "__main__":
    asyncio.run(fetch_text_invalid())

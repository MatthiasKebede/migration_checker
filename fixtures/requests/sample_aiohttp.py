# migration_checker/fixtures/requests/sample_aiohttp.py

import aiohttp
import asyncio

async def fetch_data_aiohttp():
    """A simple function using the aiohttp library."""
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.github.com") as response:
            print(response.status)
            
            # This would be a common mistake when migrating from requests
            # aiohttp uses await .text() as a method.
            # Our tool should flag access to a `.text` attribute as suspicious.
            # content = response.text

            # Correct usage in aiohttp
            content = await response.text()
            print(len(content))

if __name__ == "__main__":
    asyncio.run(fetch_data_aiohttp())


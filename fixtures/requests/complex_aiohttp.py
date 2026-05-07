import aiohttp as ah
import asyncio


async def sync_people(base_url, token):
    headers = {"Authorization": f"Bearer {token}"}

    async with ah.ClientSession(headers=headers) as session:
        async with session.get(f"{base_url}/people", params={"page": "1"}) as people_response:
            people_response.raise_for_status()
            people = await people_response.json()

        async with session.post(f"{base_url}/audit", json={"count": len(people)}) as audit_response:
            audit_response.raise_for_status()
            audit_text = await audit_response.text()

        async with session.delete(f"{base_url}/cache") as delete_response:
            delete_response.raise_for_status()
            delete_status = delete_response.status

    return {
        "people": people,
        "audit_text": audit_text,
        "delete_status": delete_status,
    }


if __name__ == "__main__":
    asyncio.run(sync_people("https://api.example.com", "demo-token"))

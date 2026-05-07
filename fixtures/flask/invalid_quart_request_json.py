from quart import request


async def read_payload_invalid():
    payload = request.get_json()
    return payload

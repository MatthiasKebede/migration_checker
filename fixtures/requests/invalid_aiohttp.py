import aiohttp


def fetch_data_aiohttp_invalid():
    response = aiohttp.ClientSession.get("https://api.github.com")
    content = response.text
    print(content)


if __name__ == "__main__":
    fetch_data_aiohttp_invalid()

import httpx


def fetch_data_httpx_invalid():
    response = httpx.get("https://api.github.com")
    payload = response.json
    print(payload)


if __name__ == "__main__":
    fetch_data_httpx_invalid()

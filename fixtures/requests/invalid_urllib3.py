import requests
import urllib3 as http_client


def fetch_data_urllib3_invalid():
    manager = http_client.PoolManager()
    manager.request("GET", "https://api.github.com")
    response = requests.get("https://api.github.com")
    print(response.text)


if __name__ == "__main__":
    fetch_data_urllib3_invalid()

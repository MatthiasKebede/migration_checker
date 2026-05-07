import urllib3 as http_client


def fetch_data_urllib3():
    manager = http_client.PoolManager()
    response = manager.request("GET", "https://api.github.com", fields={"page": "1"})
    print(response.status)


if __name__ == "__main__":
    fetch_data_urllib3()

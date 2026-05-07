# migration_checker/fixtures/requests/sample_httpx.py

import httpx

def fetch_data_httpx():
    """A simple function using the httpx library."""
    response = httpx.get("https://api.github.com")
    print(response.status_code)

    # This would be a common mistake when migrating from requests
    # httpx uses .json() as a method, not an attribute.
    # Our tool should flag access to a `.json` attribute as suspicious.
    # json_data = response.json 

    # Correct usage in httpx
    json_data = response.json()
    print(json_data)

if __name__ == "__main__":
    fetch_data_httpx()


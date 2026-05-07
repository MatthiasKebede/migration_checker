# migration_checker/fixtures/requests/sample_requests.py

import requests

def fetch_data():
    """A simple function using the requests library."""
    response = requests.get("https://api.github.com")
    print(response.status_code)
    
    # This attribute access is valid in requests
    content = response.text
    print(len(content))

    # This method call is valid in requests
    json_data = response.json()
    print(json_data)

if __name__ == "__main__":
    fetch_data()


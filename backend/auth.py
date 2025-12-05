# auth.py
import requests
import backend.config as config  # absolute import


def get_access_token():
    """Requests an access token from Amazon SP-API using the refresh token."""
    # Check that all necessary config variables exist
    if not all([config.CLIENT_ID, config.CLIENT_SECRET, config.REFRESH_TOKEN]):
        raise ValueError("Missing SP-API credentials in config/.env")

    url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": config.REFRESH_TOKEN,
        "client_id": config.CLIENT_ID,
        "client_secret": config.CLIENT_SECRET
    }

    response = requests.post(url, data=payload)
    response.raise_for_status()
    data = response.json()

    access_token = data.get("access_token")
    if not access_token:
        raise Exception(f"No access token returned. Full response: {data}")
    return access_token

if __name__ == "__main__":
    try:
        token = get_access_token()
        print("Access token obtained successfully!")
        print(token)
    except Exception as e:
        print("Failed to get access token:", e)

"""Paste into a Microsoft Fabric Python notebook with a Lakehouse attached.

Set these three non-secret configuration values; the secret stays in Key Vault.
"""
import json
import uuid
from pathlib import Path
import requests
import notebookutils

API_BASE_URL = "https://YOUR-CONTAINER-APP.azurecontainerapps.io"
KEY_VAULT_URL = "https://YOUR-VAULT.vault.azure.net/"
KEY_NAME = "scraper-api-key"

api_key = notebookutils.credentials.getSecret(KEY_VAULT_URL, KEY_NAME)
response = requests.post(
    f"{API_BASE_URL}/scrape",
    headers={"X-API-Key": api_key},
    json={"url": "https://example.com", "wait_seconds": 5},
    timeout=(15, 180),
)
response.raise_for_status()
result = response.json()
output = Path("/lakehouse/default/Files/scrapes")
output.mkdir(parents=True, exist_ok=True)
path = output / f"{uuid.uuid4()}.json"
path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
print(f"Saved {result['html_bytes']} HTML bytes to {path}")

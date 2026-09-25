"""Run locally after az login. Values never enter Terraform state or CLI args."""
import os
import secrets
import sys
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential
from azure.keyvault.secrets import SecretClient


def main():
    client = SecretClient(vault_url=f'https://{sys.argv[1]}.vault.azure.net',
                          credential=AzureCliCredential(), logging_enable=False)
    for name in ('mcp-api-key', 'scraper-api-key', 'openai-api-key'):
        try:
            existing = client.get_secret(name)
            if not existing.value or existing.properties.enabled is False:
                raise ValueError('Existing secret is empty or disabled')
        except ResourceNotFoundError:
            value = os.environ.get('DEPLOY_OPENAI_KEY') if name == 'openai-api-key' else secrets.token_urlsafe(48)
            if not value:
                raise ValueError('An OpenAI key is required to initialize the collection job')
            client.set_secret(name, value)
        print(f'{name}: available in Key Vault')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Secret provisioning failed. Check Key Vault RBAC, operator IP and credentials; secret values were not logged.', file=sys.stderr)
        sys.exit(1)
    finally:
        os.environ.pop('DEPLOY_OPENAI_KEY', None)

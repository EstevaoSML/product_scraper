"""Guard network requirements that isolated unit mocks cannot exercise."""
from pathlib import Path
import yaml


def check_api_can_resolve_public_hosts_while_chrome_is_isolated():
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'compose.yaml').read_text())
    networks = config['networks']
    services = config['services']
    assert networks['browser']['internal'] is True
    assert services['chrome']['networks'] == ['browser']
    assert any(not networks[n].get('internal', False) for n in services['api']['networks'])
    assert any(not networks[n].get('internal', False) for n in services['egress']['networks'])
    assert 'ports' not in services['chrome']
    assert 'ports' not in services['egress']
    assert services['chrome']['build']['dockerfile'] == 'Dockerfile.browser'
    assert services['chrome']['read_only'] is True
    assert services['chrome']['secrets'] == ['api_key']
    assert services['api']['environment']['BROWSER_URL'] == 'http://chrome:8001'
    assert any('/tmp:' in mount and 'exec' in mount for mount in services['chrome']['tmpfs'])

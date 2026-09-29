"""Release safety checks without Azure credentials or paid calls."""
import copy
import json
import pytest
from webapp.deploy.portfolio.github_release import controls_from_state, review_plan


@pytest.mark.parametrize('smoke_fails', [False, True])
def check_release_order_and_failed_smoke_leaves_schedule_off(tmp_path, monkeypatch, smoke_fails):
    from webapp.deploy.portfolio import github_release as release
    scratch = tmp_path / 'scratch'
    infra = tmp_path / 'infra'
    infra.mkdir()
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'INFRA', infra)
    monkeypatch.setattr(release, 'SCRATCH', scratch)
    monkeypatch.setenv('PORTFOLIO_CONFIG_JSON', json.dumps({'subscription_id': 'fixture', 'name': 'fixture'}))
    monkeypatch.setenv('ARM_SUBSCRIPTION_ID', 'fixture')
    events = []

    def fake_run(*args, capture=False):
        args = list(map(str, args))
        if args[0] == 'az':
            return '{"exists":true}'
        if any(arg.endswith('package.py') for arg in args):
            (scratch / 'package.json').write_text(json.dumps({'sha256': 'a' * 64}))
        if 'upload' in args:
            events.append('upload')
        if 'smoke' in args:
            events.append('smoke')
            if smoke_fails:
                raise RuntimeError('smoke failed')

    def fake_tf(*args, capture=False):
        if args[0] == 'show':
            return json.dumps(fixture_state())
        if args[0] == 'output':
            return '{}'

    monkeypatch.setattr(release, 'run', fake_run)
    monkeypatch.setattr(release, 'tf', fake_tf)
    monkeypatch.setattr(release, 'apply', lambda controls, settings: events.append(('apply', controls['enable_monthly_schedule'])))
    if smoke_fails:
        with pytest.raises(RuntimeError):
            release.main()
        assert events == ['upload', ('apply', False), 'smoke']
    else:
        release.main()
        assert events == ['upload', ('apply', False), 'smoke', ('apply', True)]


def fixture_state():
    values = {
        "azurerm_resource_group.portfolio": {"id": "/subscriptions/fixture/resourceGroups/rg-fixture-portfolio"},
        "azurerm_role_assignment.operator_data": {"principal_id": "human"},
        "azurerm_role_assignment.operator_secrets": {"principal_id": "human"},
        "azurerm_consumption_budget_resource_group.alert": {"time_period": [{"start_date": "2026-09-01T00:00:00Z"}]},
        "azurerm_container_app_job.monthly[0]": {"schedule_trigger_config": [{"cron_expression": "0 9 1 * *"}]},
    }
    return {"values": {"root_module": {"resources": [{"address": k, "values": v} for k, v in values.items()]}}}


def check_release_preserves_human_budget_and_schedule():
    result = controls_from_state(fixture_state(), {"subscription_id": "fixture", "name": "fixture"})
    assert result["operator_object_id"] == "human"
    assert result["budget_start_date"] == "2026-09-01T00:00:00Z"
    assert result["enable_monthly_schedule"] is True
    assert result["register_resource_providers"] is False


@pytest.mark.parametrize('digest', ['b' * 64, 'invalid'])
def check_local_recovery_uses_current_remote_package(tmp_path, monkeypatch, digest):
    from webapp.deploy.portfolio import github_release as release
    (tmp_path / 'portfolio-deployment.local.json').write_text(json.dumps({'subscription_id': 'fixture', 'name': 'fixture'}))
    state = fixture_state()
    state['values']['outputs'] = {'package_sha256': {'value': digest}}
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'INFRA', tmp_path)
    monkeypatch.setattr(release, 'tf', lambda *args, **kwargs: json.dumps(state))
    target = tmp_path / 'portfolio.auto.tfvars.json'
    if digest == 'invalid':
        with pytest.raises(ValueError):
            release.sync_local_controls()
        assert not target.exists()
    else:
        release.sync_local_controls()
        values = json.loads(target.read_text())
        assert values['package_sha256'] == digest
        assert values['enable_monthly_schedule'] is True
        assert set(values) == {'deploy_job', 'package_sha256', 'enable_monthly_schedule', 'budget_start_date'}


@pytest.mark.parametrize("state,settings", [
    ({}, {"subscription_id": "fixture", "name": "fixture"}),
    (fixture_state(), {"subscription_id": "another", "name": "fixture"}),
    (fixture_state(), {"subscription_id": "fixture", "name": "another"}),
])
def check_release_rejects_empty_or_wrong_state(state, settings):
    with pytest.raises(ValueError):
        controls_from_state(state, settings)


def check_release_rejects_different_operator_assignments():
    state = copy.deepcopy(fixture_state())
    state["values"]["root_module"]["resources"][2]["values"]["principal_id"] = "another"
    with pytest.raises(ValueError):
        controls_from_state(state, {"subscription_id": "fixture", "name": "fixture"})


@pytest.mark.parametrize("kind,actions", [
    ("azurerm_storage_account", ["delete", "create"]),
    ("azurerm_container_app_job", ["delete"]),
    ("azurerm_role_assignment", ["create"]),
    ("azurerm_role_assignment", ["update"]),
])
def check_release_rejects_destruction_and_access_changes(kind, actions):
    with pytest.raises(ValueError):
        review_plan({"resource_changes": [{"address": "fixture", "type": kind, "change": {"actions": actions}}]})


def check_release_allows_job_update_and_unchanged_access():
    review_plan({"resource_changes": [
        {"address": "job", "type": "azurerm_container_app_job", "change": {"actions": ["update"]}},
        {"address": "role", "type": "azurerm_role_assignment", "change": {"actions": ["no-op"]}},
    ]})

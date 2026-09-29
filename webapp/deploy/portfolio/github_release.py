"""GitHub OIDC release of an EXISTING portfolio; no model calls on the runner."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "webapp/deploy/portfolio/terraform"
SCRATCH = ROOT / ".ci-runtime/portfolio"
OPS = ROOT / "webapp/deploy/portfolio/operations.py"


def run(*args, capture=False):
    result = subprocess.run(list(map(str, args)), check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def tf(*args, capture=False):
    return run("terraform", "-chdir=" + str(INFRA), *args, capture=capture)


def controls_from_state(state, settings):
    """Keep the original operator, budget epoch and schedule on ephemeral runners."""
    resources = {r["address"]: r["values"] for r in state.get("values", {}).get("root_module", {}).get("resources", [])}
    required = ["azurerm_resource_group.portfolio", "azurerm_role_assignment.operator_data",
                "azurerm_role_assignment.operator_secrets", "azurerm_consumption_budget_resource_group.alert",
                "azurerm_container_app_job.monthly[0]"]
    if any(key not in resources for key in required):
        raise ValueError("Existing deployed portfolio state is required; migrate it before GitHub deployment.")
    group = resources[required[0]]
    expected = "/subscriptions/" + settings["subscription_id"] + "/resourceGroups/rg-" + settings["name"] + "-portfolio"
    if group["id"].lower() != expected.lower():
        raise ValueError("Configuration does not match the migrated state.")
    operator = resources[required[1]]["principal_id"]
    if operator != resources[required[2]]["principal_id"]:
        raise ValueError("Operator assignments differ; resolve locally before CI.")
    job = resources[required[4]]
    return dict(deploy_job=True, enable_monthly_schedule=bool(job.get("schedule_trigger_config")),
                budget_start_date=resources[required[3]]["time_period"][0]["start_date"],
                operator_object_id=operator, register_resource_providers=False)


def review_plan(plan):
    """CI cannot delete/replace resources or change access. Such changes stay local."""
    for resource in plan.get("resource_changes", []):
        if resource.get("mode") == "data":
            continue
        actions = resource["change"]["actions"]
        if "delete" in actions or (
            resource["type"] == "azurerm_role_assignment" and actions != ["no-op"]
        ):
            raise ValueError("Release requires a local infrastructure/access review: " + resource["address"])


def sync_local_controls():
    """Refresh ignored rollout values before a local operation after a CI release."""
    settings = json.loads((ROOT / 'portfolio-deployment.local.json').read_text(encoding='utf-8-sig'))
    state = json.loads(tf('show', '-json', capture=True))
    controls = controls_from_state(state, settings)
    values = {key: controls[key] for key in ('deploy_job', 'enable_monthly_schedule', 'budget_start_date')}
    digest = state['values']['outputs']['package_sha256']['value']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Remote state has no valid deployed package digest.')
    values['package_sha256'] = digest
    (INFRA / 'portfolio.auto.tfvars.json').write_text(json.dumps(values, indent=2), encoding='utf-8')
    print('Local rollout controls refreshed from the shared Terraform state.')


def apply(controls, settings):
    variables = SCRATCH / "release.tfvars.json"
    variables.write_text(json.dumps(controls), encoding="utf-8")
    path = SCRATCH / "release.tfplan"
    tf("plan", "-input=false", "-lock-timeout=5m", "-var-file=" + str(settings),
       "-var-file=" + str(variables), "-out=" + str(path))
    review_plan(json.loads(tf("show", "-json", str(path), capture=True)))
    tf("apply", "-input=false", "-lock-timeout=5m", str(path))


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    settings_path = ROOT / "portfolio-deployment.local.json"
    settings_path.write_text(os.environ["PORTFOLIO_CONFIG_JSON"], encoding="utf-8")
    # Reuse the same file validator as the local deployment.
    run("pwsh", "-NoProfile", "-File", ROOT / "webapp/deploy/portfolio/deploy.ps1", "-Action", "CheckSettings")
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    if settings["subscription_id"].lower() != os.environ["ARM_SUBSCRIPTION_ID"].lower():
        raise ValueError("Azure login subscription differs from the configuration file.")
    account = "st" + settings["name"]
    # Refuse a new/empty backend: an incorrect key must not create a duplicate stack.
    exists = json.loads(run("az", "storage", "blob", "exists", "--auth-mode", "login",
                            "--account-name", account, "--container-name", "github-tfstate",
                            "--name", "portfolio.tfstate", "--only-show-errors", "-o", "json", capture=True))
    if exists.get("exists") is not True:
        raise ValueError("Migrate portfolio.tfstate to github-tfstate before running this workflow.")
    (INFRA / "backend.generated.tf").write_text('terraform {\n  backend "azurerm" {}\n}\n', encoding="utf-8")
    tf("init", "-input=false", "-backend-config=storage_account_name=" + account,
       "-backend-config=container_name=github-tfstate", "-backend-config=key=portfolio.tfstate",
       "-backend-config=use_azuread_auth=true", "-backend-config=use_oidc=true")
    tf("validate")
    controls = controls_from_state(json.loads(tf("show", "-json", capture=True)), settings)
    outputs = SCRATCH / "outputs.json"
    outputs.write_text(tf("output", "-json", capture=True), encoding="utf-8")
    run(sys.executable, ROOT / "webapp/deploy/portfolio/package.py", "--output", SCRATCH)
    run(sys.executable, OPS, "upload", "--outputs", outputs, "--package", SCRATCH / "package.json")
    controls["package_sha256"] = json.loads((SCRATCH / "package.json").read_text())["sha256"]
    restore_schedule = controls["enable_monthly_schedule"]
    controls["enable_monthly_schedule"] = False
    apply(controls, settings_path)
    outputs.write_text(tf("output", "-json", capture=True), encoding="utf-8")
    # All runtime validation and publication execute in Azure, with no paid research.
    run(sys.executable, OPS, "start", "--outputs", outputs, "--mode", "smoke")
    if restore_schedule:
        controls["enable_monthly_schedule"] = True
        apply(controls, settings_path)
    print("Azure smoke and publication succeeded; original schedule restored.")


if __name__ == "__main__":
    if sys.argv[1:] == ['--sync-local']:
        sync_local_controls()
    elif sys.argv[1:]:
        raise SystemExit('Supported local option: --sync-local')
    else:
        main()


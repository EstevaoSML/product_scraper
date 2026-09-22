"""Build the isolated agent image and smoke-check it without network or secrets."""
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(*args, timeout=60):
    return subprocess.run(['docker', *args], cwd=ROOT, check=True, timeout=timeout,
                          capture_output=True)


def main():
    tag = 'retail-agent-ci-' + uuid.uuid4().hex[:12]
    result = {'passed': False, 'stage': 'docker_engine_preflight', 'checks': []}
    built = False
    try:
        run('info', '--format', '{{.ServerVersion}}', timeout=30)
        result['stage'] = 'build'
        run('build', '-f', 'Dockerfile.agent', '-t', tag, '.', timeout=900)
        built = True
        result['stage'] = 'offline_smoke'
        options = ['run', '--rm', '--name', tag, '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                   '--pids-limit', '128', '--memory', '256m']
        output = run(*options, tag, '--help').stdout
        if b'--max-cost-usd' not in output:
            raise RuntimeError('Missing budget option')
        run(*options, '--entrypoint', 'python', tag, '-c',
            'import mcp, httpx, httpx2; from azure.identity.aio import ManagedIdentityCredential; '
            'from azure.storage.blob.aio import BlobServiceClient; '
            'from app.research_contracts import DecisionEnvelope; '
            'from app.research_openai import ModelBudget; '
            'assert ModelBudget("0.05").calls == 0; '
            'assert DecisionEnvelope.model_json_schema()["additionalProperties"] is False')
        result.update(passed=True, stage='complete', checks=['image_build', 'help', 'imports_and_contract'])
    except (OSError, subprocess.SubprocessError, RuntimeError):
        # No raw docker output (could include environment-specific build details).
        result['error'] = 'Agent container validation failed; check Docker access/build configuration'
    finally:
        if built:
            for args in [('rm', '-f', tag), ('image', 'rm', tag)]:
                try:
                    run(*args, timeout=30)
                except (OSError, subprocess.SubprocessError):
                    pass
        (ROOT/'reports').mkdir(exist_ok=True)
        (ROOT/'reports'/'agent-container-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

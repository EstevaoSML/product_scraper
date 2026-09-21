# Testing reminders for every change

- Add meaningful unit tests for new behavior and regression tests for bug fixes.
- Changes to URL handling, DNS, redirects, proxying, credentials, request limits, or browser lifecycle require abuse-case/security tests.
- Run `python -m pytest -q --cov=app --cov-branch --cov-fail-under=80` after installing requirements-dev.txt.
- Changes to Docker, Compose, dependencies, Chrome options or browser integration also require `python scripts/container_ci.py`. Report a blocked Docker run honestly; mocked tests do not count as browser validation.
- Keep tests in `checks/check_*.py` with `check_*` functions; the existing gitignore excludes names containing `test`.
- Before adding an LLM or agent, implement the evaluation plan in `docs/CI.md`: extraction accuracy, schema validation, grounding, prompt injection, tool permissions, secret protection, budgets and termination. No AI runtime currently exists, so do not claim AI behavior is tested today.
- Never put real credentials, user HTML, customer data, or provider API keys in fixtures or PR artifacts. Live model evaluation belongs in a trusted manual job with explicit budgets, not untrusted pull requests.
- Preserve the production URL restrictions; do not add a private-address bypass to make container checks pass.

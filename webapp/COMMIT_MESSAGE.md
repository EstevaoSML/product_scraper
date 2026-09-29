# Commit sugerido

```text
feat: add low-budget Azure portfolio deployment without ACR

Add static Azure Storage hosting, private ADLS Gen2, Key Vault and one
monthly Container Apps Consumption job using a public Microsoft base.
Track ten products across four retailers with durable USD 2 monthly
reservations, no repeated pair attempts and no new image generation.
Add a no-model Azure smoke gate, deployment commands and regression checks.
```

Lembrete: revise as mudanças e faça o commit. Nenhum commit foi criado automaticamente.

Validação: 392 testes Python, 92,43% de cobertura do pacote `app` e quatro novos planos Terraform simulados aprovados. Docker bloqueado pela ausência do engine Linux; smoke real no Azure pendente. Nenhum recurso Azure ou chamada paga foi criado nesta implementação.

Inclua `webapp/portfolio`, `webapp/deploy/portfolio` (com `.terraform.lock.hcl` e `verification/budget.tftest.hcl`), `checks/check_portfolio.py` e as demais alterações. Não inclua `.ci-runtime`, pacotes ZIP, configuração real, estado Terraform ou credenciais.

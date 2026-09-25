# Commit sugerido

```text
feat(webapp): add Azure deployment and retailer price history averages

Add the Flask catalog with five products and evidence-backed price history.
Compare the latest retailer average with the earliest recorded daily average.
Add Terraform, private ADLS Gen2, Key Vault references and managed identities.
Package web, scraper and collection jobs with deployment and validation docs.
```

Lembrete: revise as mudanças e faça o commit. Esta entrega não criou um commit automaticamente.

Na raiz do repositório, os novos arquivos estão em `webapp/` e nos dois arquivos `checks/check_web_*.py`. Já havia mudanças no agente antes desta tarefa; revise-as separadamente ou inclua-as conscientemente no commit.

Os arquivos de configuração real, estado Terraform, secrets e caches são ignorados pelo Git. Relatórios brutos em `webapp/data/reports` foram copiados para a pasta de trabalho, mas seguem a regra de ignore do repositório para `reports`; banco do catálogo e imagens finais acompanham o WebApp.

# Validação — 27/09/2026

**Atualização — infraestrutura econômica sem ACR:** 392 testes Python e 4 novos planos Terraform simulados aprovados. Consulte [deploy/portfolio/VALIDATION.md](deploy/portfolio/VALIDATION.md) para os resultados e limitações atuais. O relatório abaixo preserva a validação anterior da stack completa.

## Resultado

- Suíte do repositório: **366 testes aprovados**, incluindo **24 testes do WebApp/deploy**.
- Cobertura de `app` (scraper/agente), com branches: **92,42%**. Esse percentual não é a cobertura do pacote `webapp`.
- Terraform 1.14.7 e AzureRM 5.6.0: `validate` aprovado para bootstrap e stack principal.
- **4 cenários Terraform com provider simulado aprovados**: infraestrutura privada, workloads, cron opcional e seed anterior ao site público.
- Sintaxe do JavaScript e parsing do PowerShell verificados.
- Interface conferida em navegador: cinco imagens, primeira data e média, detalhe por varejista, ausência dos antigos rótulos anuais.
- Consulta aos metadados PyPI das cinco dependências diretas do site/deploy não reportou vulnerabilidades nessas versões. Isso não substitui auditoria das dependências transitivas ou da imagem final.

A suíte cobre o cálculo por varejista/data, preservação de preço antigo com aviso, validação de oferta/variante/evidência, busca, cache Azure, resposta HTTP 503 em falha do storage, bloqueio de caminhos arbitrários de imagem e publicação protegida por lease. Um teste de integração simulada confirma que o worker chama `python -m app.research_job`, mantém chaves fora dos argumentos, preserva o histórico e publica relatórios e catálogo.

## Regressões do deploy em 27/09/2026

Quatro testes executam PowerShell com ferramentas externas simuladas: validação sem configuração/login Azure, interrupção quando Terraform falha, sucesso com `status=ready` e recusa de sucesso quando o site permanece em outro status. Nenhuma chamada Azure é feita nesses testes. O engine Docker Linux foi novamente consultado e continua indisponível.

## Ambiente de testes

O ambiente Windows bloqueou o diretório temporário padrão do pytest. A execução completa utilizou uma pasta isolada em `.ci-runtime` e ACL herdada apenas nessa pasta temporária. Nenhuma restrição do aplicativo, URL, proxy ou credencial foi relaxada. Azure SDKs ausentes foram instalados no ambiente de validação; nenhuma chamada ao Azure foi feita pelos testes.

Há um aviso de depreciação preexistente do Starlette/AnyIO. Os demais arquivos modificados no scraper antes desta tarefa foram preservados.

## Pendências de homologação

- `python scripts/container_ci.py` foi tentado e bloqueado: engine Docker Linux indisponível.
- As imagens novas do WebApp/worker ainda não foram construídas/executadas em Docker. O script faz o build remoto no ACR durante o deploy; nenhum build remoto foi iniciado nesta entrega.
- Não houve `terraform apply`, criação de recursos, execução Azure por identidade gerenciada ou pesquisa paga adicional.
- É necessário executar o deploy real com a assinatura/permissões, conferir seed, `/readyz` e uma coleta controlada antes de declarar homologação completa de produção.

Os testes simulados não comprovam conectividade, quota regional, disponibilidade das imagens base ou comportamento do navegador no Azure.

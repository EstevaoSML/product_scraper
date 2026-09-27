# Implantação Azure

Este pacote provisiona o site Flask, o scraper MCP e seu navegador, os jobs de seed/coleta e o armazenamento. Use um nome novo para esta stack: o Terraform antigo em `../../deploy/terraform` continua separado. Não aplique os dois estados sobre os mesmos recursos.

## Pré-requisitos

1. PowerShell 7 (`pwsh`), Python 3.12+, Azure CLI e Terraform 1.9–1.x instalados.
2. Assinatura Azure com permissão de criar recursos e atribuir funções RBAC, por exemplo Owner no escopo de implantação. Faça `az login`; autenticação corporativa/MFA permanece no Azure CLI.
3. Região com Container Apps e quota disponível. O padrão é `brazilsouth`.
4. Chave OpenAI válida para o agente. Ela é armazenada exclusivamente no Key Vault; o site não a recebe.

O build usa `az acr build` no Azure e não exige Docker local. A primeira execução cria recursos pagos: Container Apps, navegador sempre ativo, ACR, armazenamento, Private Endpoints e logs. O limite de US$ 0,05 do agente não limita a fatura da infraestrutura.

## Validação local sem credenciais

Com PowerShell 7 e Terraform instalados, execute na raiz do repositório:

```powershell
pwsh -File webapp/deploy/deploy.ps1
```

Não exige `config.json`, Azure CLI, login, Python ou chave OpenAI. Valida o bootstrap e a stack e executa cenários Terraform com provider simulado. O primeiro `init` pode baixar providers públicos; não provisiona recursos Azure. O script interrompe em falha de validação.

## Executar

Na raiz de `web_scraping`:

```powershell
Copy-Item webapp/deploy/config.example.json webapp/deploy/config.json
az login
# Consulte o ID da assinatura e o ID de objeto da identidade que fez login:
az account show --query id -o tsv
az ad signed-in-user show --query id -o tsv
```

Preencha `config.json`:

- `subscription_id`: assinatura destino.
- `name`: nome globalmente único, 6–16 letras minúsculas/números, iniciando por letra. Define nomes de ACR, armazenamento e Key Vault.
- `secret_operator_object_id`: ID **de objeto** Entra do usuário/principal autenticado, não o client ID de um aplicativo.
- `operator_ipv4`: IP público IPv4 da estação. Somente esse endereço acessa o backend do Terraform; o Key Vault o permite temporariamente durante o cadastro de segredos.
- `suggestion_email`: e-mail público para sugestões, opcional.
- `collection_cron`: vazio mantém coleta manual. Exemplo `0 12 * * *` executa diariamente às 12h UTC e autoriza chamadas recorrentes.

```powershell
# Validação local sem provisionar recursos:
pwsh -File webapp/deploy/deploy.ps1

# Criação/atualização efetiva da stack:
pwsh -File webapp/deploy/deploy.ps1 -Apply
```

Na primeira implantação, informe a chave OpenAI no prompt oculto. Em atualizações, pressione Enter se ela já estiver no Key Vault. As chaves `mcp-api-key` e `scraper-api-key` são geradas automaticamente e preservadas em novas execuções. O script não coloca seus valores em argumentos, arquivos temporários, tfvars ou estado Terraform.

O script faz, em ordem:

1. Registro dos resource providers e bootstrap do armazenamento do estado.
2. Infraestrutura base: rede, identidades, Key Vault, ACR, logs e ADLS.
3. Cadastro de segredos por SDK autenticado no Azure CLI, com acesso público limitado ao IP informado; fecha o acesso público em `finally`.
4. Builds Linux no ACR com tags próprias da execução.
5. Scraper interno e jobs; execução do seed com os cinco produtos existentes.
6. Site público HTTPS com `/readyz` validando acesso ao catálogo; somente confirma sucesso após receber explicitamente `status=ready`. Timeout, erro HTTP ou outro status interrompem a validação final.

Terraform provisiona a infraestrutura; o script coordena builds, segredos e carga de dados, etapas que não são resolvidas apenas entregando uma credencial a `terraform apply`. Nenhuma cópia manual de imagens/relatórios é necessária para a primeira publicação.

## Recursos e isolamento

| Componente | Acesso |
| --- | --- |
| Site Container App | HTTPS público, Gunicorn, 1–3 réplicas, identidade apenas leitora do catálogo |
| MCP e navegador/egress | Ingress interno ao ambiente, uma réplica de cada serviço |
| Job de coleta | Identidade escritora do catálogo, leitura de dois segredos específicos do Key Vault |
| Key Vault | RBAC, purge protection, endpoint privado; valores ausentes do estado Terraform |
| ADLS Gen2 | HNS habilitado, filesystem privado `catalog`, endpoints privados `blob` e `dfs`, chaves compartilhadas desabilitadas |
| Terraform state | Conta separada, autenticação Entra, firewall por IP, versionamento e retenção |

O site é stateless e não precisa de uma chave de sessão. Não usa conexão de armazenamento com segredo/SAS. As imagens privadas são entregues pelo Flask via identidade gerenciada, apenas para IDs de produtos do catálogo; relatórios brutos não possuem rota pública.

O `bootstrap` mantém seu próprio estado local para evitar a dependência circular de criar o backend antes do Terraform usá-lo. Guarde `webapp/deploy/bootstrap/terraform.tfstate` com segurança e fora do Git. O estado principal fica no Azure. Para trocar de estação, transfira o estado bootstrap de forma segura ou importe os recursos existentes, sem recriá-los. Quando seu IP mudar, atualize `operator_ipv4` e execute novamente.

## Dados e recuperação

No filesystem ADLS `catalog`:

```text
catalog/latest.json           # catálogo publicado, commit protegido por lease
catalog/snapshots/<id>.json   # cópias completas anteriores à publicação
images/<produto>/<sha>.png    # imagens imutáveis por conteúdo
reports/seed/...              # evidências originais do piloto
reports/<lote>/<produto>/...  # relatórios, diagnósticos, scrapes e imagens posteriores
```

Cada worker usa SQLite apenas temporariamente para reaproveitar o importador, reconstituído do JSON privado. O Data Lake é a fonte persistente de produção. Nunca se monta SQLite compartilhado entre réplicas.

O lease de 60 segundos é renovado a cada 15 segundos. Se for perdido, o processo de pesquisa é interrompido e a publicação é recusada. Após uma queda, o lease expira; o catálogo previamente publicado permanece. O seed é idempotente e não substitui dados mais novos.

Para recuperar um catálogo, um operador na rede privada, com Storage Blob Data Contributor, pode selecionar um snapshot válido e publicá-lo em `catalog/latest.json` durante uma janela sem coleta, adquirindo o mesmo lease. Os blobs de imagens são imutáveis, preservando as referências dos snapshots. Não há limpeza automática de snapshots nesta versão. A conta usa LRS: não oferece recuperação de desastre entre regiões.

## Coletar e verificar

```powershell
az containerapp job start --name NOME-collect --resource-group rg-NOME
az containerapp job execution list --name NOME-collect --resource-group rg-NOME -o table
```

Cada execução processa os cinco produtos sequencialmente, até US$ 0,05 de pesquisa por produto (US$ 0,25 no lote), sem imagens novas por padrão. Os cinco arquivos existentes são preservados. Para habilitar novas imagens, altere explicitamente `--image-max-cost-usd` no job, até `0.05`; isso adiciona orçamento estimado de imagem. O coletor chama o mesmo módulo `app.research_job`, usando `MCP_API_KEY` e `OPENAI_API_KEY` injetados do Key Vault; não há arquivo de chave no contêiner.

Logs de runtime vão para Log Analytics. O web responde `/healthz` para vida do processo e `/readyz` para catálogo. Falhas de armazenamento devolvem HTTP 503 com mensagem genérica. O job não tem retry automático que possa repetir chamadas pagas; consulte o status antes de iniciar outro lote.

## Atualização e limitações verificáveis

Execute novamente `deploy.ps1 -Apply` para construir novas tags e atualizar as revisões. O seed detecta o catálogo existente e não o reimporta. Preserve os nomes/estado para evitar criar outra stack. Não use `terraform destroy` para atualizar; Data Lake e backend possuem `prevent_destroy`.

`vendor/scraper` é um snapshot dos fontes locais para os builds. Ele não inclui `.env`, `secrets`, outputs ou ambiente virtual do repositório. Alterações futuras no agente raiz precisam ser refletidas deliberadamente nesse snapshot antes do deploy.

O catálogo e Terraform foram testados localmente; a criação dos recursos, o build remoto e o acesso efetivo por identidade gerenciada ainda dependem da primeira execução em sua assinatura. O engine Docker Linux local estava indisponível. Consulte `../VALIDATION.md` antes de considerar a implantação homologada.

Referências: [segredos por Key Vault no Container Apps](https://learn.microsoft.com/azure/container-apps/manage-secrets), [endpoints privados Blob e DFS](https://learn.microsoft.com/azure/storage/common/storage-private-endpoints).

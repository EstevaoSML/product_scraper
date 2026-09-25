# Preço Claro

Aplicativo Flask de histórico de preços, integrado ao repositório `web_scraping`. A pasta `webapp` é a versão principal do aplicativo. O pacote `app` na raiz continua sendo o scraper/agente; não foi substituído.

## Executar localmente

Na raiz do repositório, com Python 3.12:

```powershell
python -m pip install -r webapp/requirements.txt
$env:PORT = '5001'
python -m webapp.app
```

Abra http://127.0.0.1:5001/. Localmente, usa `webapp/data/catalog.sqlite3`. O servidor de desenvolvimento escuta somente em loopback. No Azure, a imagem usa Gunicorn, duas instâncias de worker e armazenamento privado no ADLS Gen2.

## Como os preços são calculados

- **Média atual:** média aritmética da leitura mais recente de cada varejista, com preço válido em BRL e estoque confirmado. Uma loja entra apenas uma vez. Leitura antiga continua incluída e é sinalizada no detalhe após 48 horas.
- **Primeiro registro:** primeira data UTC com uma média válida. Nessa data, usamos a última leitura de cada varejista observado naquele dia. Não completamos lacunas com preços de outras datas.
- **Variação:** compara essas duas médias. A data, a quantidade de varejistas e suas ofertas ficam disponíveis no detalhe. As datas das últimas leituras podem diferir entre lojas.
- O cálculo usa centavos e arredondamento decimal. Oferta sem preço, fora de estoque ou em outra moeda não vira zero nem recupera uma oferta antiga para compor a média atual.
- A comparação exige a mesma variante. O importador rejeita troca de variante/anúncio sem mapeamento explícito. Condições Pix, parcelamento e frete não são padronizadas automaticamente.

O piloto contém cinco produtos KaBuM!, cinco preços observados e cinco ilustrações geradas. Quatro preços vieram do agente; o headset foi confirmado diretamente no MCP. A origem é apresentada no detalhe. Custo histórico contabilizado: **US$ 0,09801850**; consulte `COLLECTION.md`. Esta preparação de deploy não fez novas chamadas pagas.

## Implantação Azure

Veja **[deploy/README.md](deploy/README.md)**. O script provisiona a infraestrutura com Terraform, constrói quatro imagens no ACR, cadastra segredos no Key Vault, carrega os dados existentes no Data Lake e libera o site HTTPS.

```powershell
Copy-Item webapp/deploy/config.example.json webapp/deploy/config.json
# Preencha identificadores Azure, IP público e, opcionalmente, e-mail.
az login
pwsh -File webapp/deploy/deploy.ps1 -Apply
```

Não coloque credenciais em `config.json`, tfvars, Git ou comandos. A chave OpenAI é solicitada de forma oculta na primeira implantação; chaves internas são geradas pelo script. Identidades gerenciadas acessam Key Vault e ADLS. A implantação requer assinatura/permissões e capacidade regional; não foi executada nesta entrega.

## Novas coletas

Localmente, o MCP e as dependências do agente devem estar disponíveis:

```powershell
python -m webapp.collect --agent-root . --image-max-cost-usd 0.05 --batch novo-lote
```

`OPENAI_API_KEY` deve estar no ambiente do coletor; a chave MCP vem de `secrets/api_key.txt`. O coletor chama `python -m app.research_job`, com limite de pesquisa de US$ 0,05 por produto. O parâmetro de imagem é um orçamento estimado separado; use `0` para reutilizar as imagens existentes. `--ids ps5-digital` restringe a coleta. O mesmo lote ignora tentativas já concluídas.

No Azure, execute o job `NOME-collect` conforme o guia. O job é **manual por padrão**, sem novas imagens e sem repetição automática em falha. Um lease renovável serializa as coletas e protege a publicação do catálogo. Relatórios/imagens são salvos antes da atualização do catálogo; uma falha não apaga preços anteriores. O seed nunca sobrescreve um catálogo já inicializado.

## Estrutura

- `app.py`, `templates/`, `static/`: site, busca, histórico e formulário mailto.
- `retail_catalog.py`: validação de evidências e cálculo compartilhado entre SQLite e Azure.
- `cloud_catalog.py`: leitura privada do catálogo/imagens via identidade gerenciada.
- `cloud_job.py`: seed, coleta serial, histórico e publicação no ADLS.
- `deploy/`: bootstrap do estado, Terraform, script de implantação e guia.
- `vendor/scraper/`: snapshot de código e inputs de build do scraper/agente, sem credenciais. Mantém o pacote de implantação reproduzível; atualize deliberadamente ao alterar o agente da raiz.
- `../checks/check_web_catalog.py`, `../checks/check_web_cloud.py`: regressões do aplicativo.

Defina `SUGGESTION_EMAIL` localmente ou `suggestion_email` no deploy para habilitar sugestões. O formulário prepara um e-mail; o usuário confirma o envio no próprio cliente. Não há SMTP, credenciais de e-mail ou armazenamento de mensagens no site.

## Validação

Na raiz, com `requirements-dev.txt` e `webapp/requirements.txt` instalados:

```powershell
python -m pytest -q --cov=app --cov-branch --cov-fail-under=80
terraform -chdir=webapp/deploy/terraform init -backend=false
terraform -chdir=webapp/deploy/terraform validate
terraform -chdir=webapp/deploy/terraform test -test-directory=verification
python scripts/container_ci.py
```

Consulte `VALIDATION.md` para resultados e limitações. `preview.html` é uma exportação estática: mantenha `static` junto dele; novos dados aparecem no Flask, não automaticamente na exportação.

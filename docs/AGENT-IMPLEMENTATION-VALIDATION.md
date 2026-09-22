# Entrega: agente de pesquisa de varejo com GPT-5 mini

Data: 2026-09-22.
Repositório: `C:\Users\estev\OneDrive\Área de Trabalho\work\web_scraping`.

## Implementado

- Um agente Python, reutilizando `app.research.research`, com contratos Pydantic
  estritos, autorização local de ferramentas e evidências do mesmo snapshot/produto/oferta.
- Adaptador GPT-5 mini pela OpenAI Responses API com Structured Outputs. Chaves
  separadas do navegador, sem ferramentas externas habilitadas no modelo.
- Limites de decisões, operações, links, tempo, tokens, custo e tentativas;
  reserva antes de cada chamada paga e fechamento em `finally`.
- Executável local/Azure, imagem Docker própria, lease renovável no Blob Storage,
  armazenamento privado de relatórios e métricas sanitizadas.
- Terraform opcional para Container Apps Job manual, identidade, RBAC, Storage,
  endpoint privado e DNS. Reutiliza ACR, Key Vault e Log Analytics existentes.
- Fixtures sintéticas, testes de segurança/cancelamento, avaliações determinísticas
  e workflow manual separado para avaliações reais do modelo.
- Documentação de uso em `docs/RETAIL-AGENT.md`, com atualizações nos guias existentes.

## Validação realizada

| Verificação | Resultado |
|---|---|
| Suíte Python completa | 293 testes aprovados |
| Cobertura de linhas e branches | 93,33%; mínimo exigido 80% |
| Corpus sintético pelo fluxo real com decisões simuladas | 11 cenários aprovados |
| Terraform validate | Aprovado, sem avisos |
| Terraform test | 6 testes aprovados |
| Auditoria das dependências resolvidas do agente | Nenhuma vulnerabilidade conhecida encontrada |
| CLI do agente | `--help` validado sem credenciais |
| `git diff --check` | Sem erros de whitespace |
| `scripts/container_ci.py` | Bloqueado no pré-check do Docker |
| `scripts/agent_container_ci.py` | Bloqueado no pré-check do Docker |

O engine Linux do Docker Desktop não está disponível nesta sessão. Uma tentativa
anterior retornou acesso negado ao pipe; a última retornou pipe inexistente.
Foi tentada a inicialização em segundo plano, sem disponibilizar o engine.
Os runners geraram seus relatórios de falha e não criaram stacks de testes.

A instalação direta das dependências encontrou problemas de permissões em
pastas temporárias do Python/Windows e de build do undetected-chromedriver.
A suíte foi executada em Python 3.12.14 com dependências isoladas no workspace,
reutilizando a instalação existente do undetected-chromedriver 3.5.5. Um ajuste
somente no launcher de QA manteve a herança das ACLs nas pastas temporárias de QA;
nenhuma proteção de URL/SSRF ou teste da aplicação foi desativado.

A auditoria usou a lista transitiva integral resolvida por `pip --dry-run --report`
e `pip-audit --disable-pip --no-deps --strict`, evitando o problema local de criação
de venv do auditor. O CI mantém a auditoria normal de `requirements-agent.txt`.

## Limites e próximos passos

Nenhum recurso Azure foi criado, nenhum Terraform apply foi executado e nenhuma
chamada paga à OpenAI foi feita. Os testes determinísticos não demonstram precisão
semântica ou resistência geral do GPT-5 mini a prompt injection. A avaliação manual
com modelo real continua pendente, com teto de custo obrigatório.

A extração é deliberadamente conservadora: dados financeiros exigem uma associação
explícita com um Offer estruturado. Páginas sem essa associação podem retornar
nome/variante e campos financeiros nulos. JSON-LD continua sendo dado não confiável.

O contrato público mudou de `fields` para `product` e `evidence`, conforme o formato
solicitado. Adaptadores customizados antigos precisam migrar para o novo contrato.

Para terminar a validação de containers, com Docker Desktop/engine Linux pronto,
execute no repositório:

```powershell
python scripts/container_ci.py
python scripts/agent_container_ci.py
```

Antes de implantar: revisar o plano Terraform real, preparar a imagem do agente e
armazenar `openai-api-key` no Key Vault. A identidade do navegador não recebe acesso
a essa chave. Não envie chaves pelo chat. O guia explica a implantação e a execução.

A alteração preexistente de `AGENTS.md` foi preservada. Nenhum commit foi criado.

## Commit sugerido

Lembrete: crie um commit após revisar as alterações e concluir a validação Docker.

Mensagem:

`feat: add bounded GPT-5 mini retail research agent on Azure`

Descrição:

- Reuse the provider-neutral MCP loop with strict decisions, grounded offer evidence,
  external budgets and cancellation-safe session cleanup.
- Add the OpenAI adapter and isolated Azure Container Apps Job with private Blob
  reports, renewable lease, scoped identity and Key Vault references.
- Add deterministic security/evaluation coverage, optional manual model evaluation,
  agent image CI and operational documentation.
- Validation: 293 Python checks, 93.33% coverage, 11 synthetic scenarios and six
  Terraform checks passed; Docker integration remains pending on engine availability.

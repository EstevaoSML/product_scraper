> Navigation update: the service now also exposes open_page, inspect_page, search_site, follow_link and close_session. See [Agent navigation](AGENT-NAVIGATION.md) for the new contracts; existing scrape_html examples below remain valid. Azure now uses [Container Apps](../deploy/terraform/README.md), not Functions.

# Usar o scraper como ferramenta MCP

O projeto expõe `scrape_html` em `http://127.0.0.1:8000/mcp`, na mesma porta da API REST. Usa o SDK oficial Python `mcp==2.2.0` e Streamable HTTP. Não é necessário adicionar outro container.

## Atualização do protocolo

A especificação publicada em **2026-07-28** introduziu um núcleo sem sessões, chamadas independentes com metadados por requisição e cabeçalhos de roteamento como `Mcp-Method` e `Mcp-Name`. O SDK implementa esses detalhes; não reimplementamos o protocolo manualmente. Também foi testado o caminho legado `2025-11-25`, com handshake e transporte HTTP sem sessões persistentes.

Fontes oficiais consultadas em 20/09/2026: [anúncio da especificação](https://blog.modelcontextprotocol.io/posts/2026-07-28/), [integração ASGI do SDK](https://py.sdk.modelcontextprotocol.io/run/asgi/) e [segurança e implantação](https://py.sdk.modelcontextprotocol.io/run/deploy/).

## Iniciar e testar

Na pasta do projeto, com Docker Desktop iniciado:

```powershell
docker compose build api chrome
docker compose up -d --no-build --wait --wait-timeout 180
```

Para usar o cliente de exemplo, instale as dependências no seu ambiente Python 3.12+:

```powershell
python -m pip install -r requirements-dev.txt
python scripts/mcp_client.py 'https://www.kabum.com.br/produto/989702/console-sony-playstation-5-ssd-825gb-controle-sem-fio-dualsense-2-jogos-digitais-edicao-digital'
```

O cliente lê `secrets/api_key.txt`, descobre a ferramenta, chama `scrape_html` e salva um JSON diferente em `outputs/scrape_mcp_<timestamp-UTC>_<id>.json`. Não imprime a chave. Não sobrescreve resultados anteriores. Os erros da ferramenta aparecem com código e request ID e são registrados pelo servidor. Uma falha local de conexão do cliente Python não gera um arquivo de log no servidor.

Para outra instalação, use `--endpoint https://SEU-SERVIDOR/mcp --key-file CAMINHO_DA_CHAVE`. Não envie sua chave local para servidores de terceiros.

## Configurar o Agent

| Configuração | Valor |
|---|---|
| Transporte | Streamable HTTP |
| Endpoint local | `http://127.0.0.1:8000/mcp` |
| Autenticação | Cabeçalho `X-API-Key` com a chave do servidor |
| Ferramenta | `scrape_html` |
| Timeout do cliente | Pelo menos 180 segundos |

Use a configuração de headers/segredos do cliente MCP do seu agente. A chave não é um argumento da ferramenta e não deve entrar no prompt. Este projeto usa chave compartilhada para integração privada; não implementa login OAuth, descoberta de autorização ou isolamento de usuários. Clientes que aceitam apenas OAuth não funcionarão sem uma camada de autorização adicional.

Exemplo dos argumentos que o agente envia:

```json
{
  "url": "https://www.kabum.com.br/produto/989702/console-sony-playstation-5-ssd-825gb-controle-sem-fio-dualsense-2-jogos-digitais-edicao-digital",
  "wait_seconds": 5,
  "timeout_seconds": 30
}
```

`wait_css` é opcional. O schema rejeita campos adicionais, como scripts, cookies e proxy. O agente pode descobrir websites usando sua ferramenta de busca e passar a URL ao scraper. `scrape_html` não realiza busca na web nem extrai preços por conta própria.

O resultado inclui HTML e metadados em `structuredContent`, além de uma cópia JSON em `content` para clientes que leem apenas texto. Inclui `request_id`, correlacionado com `X-Request-ID` e os logs. Como o HTML aparece nas duas representações, o limite de 3,5 MB do resultado MCP pode ser atingido antes do limite da API REST. O conteúdo não é truncado silenciosamente.

Falhas de scraping retornam `isError=true` com código, mensagem segura, status HTTP equivalente e request ID. Autenticação e falhas de transporte usam status HTTP. Respeite `retry_after_seconds` em erros de navegador ocupado e limite as tentativas no agente. REST e MCP compartilham o mesmo navegador, semáforo e intervalo mínimo entre execuções.

## Segurança e Azure

`URL_POLICY=public` permite destinos HTTPS públicos; endereços privados, localhost e metadados da nuvem permanecem bloqueados. Os logs existentes registram erros de ferramenta em `/mcp`, sem HTML, chave ou URL de destino.

`MCP_ALLOWED_HOSTS` identifica **o endereço do seu servidor MCP**, não os sites que serão raspados. O padrão permite acesso local. Para servir por um domínio próprio/Azure, configure o hostname exato, por exemplo:

```dotenv
MCP_ALLOWED_HOSTS=meu-scraper.example.com,meu-scraper.example.com:443
```

O template Azure oferece os parâmetros `mcpAllowedHosts` e `mcpAllowedOrigins`. Configure `mcpAllowedHosts` com o FQDN da implantação e use HTTPS. Não use curingas de domínio. Recrie o container após mudar variáveis. O domínio pode ser obtido na implantação existente e usado numa atualização do template.

Clientes de servidor normalmente não enviam `Origin`; o padrão rejeita qualquer Origin presente. Se usar um cliente no navegador, configure `MCP_ALLOWED_ORIGINS` com sua origem exata e implemente CORS apropriado no ingress. A origem autorizada não substitui autenticação. Não habilitamos CORS globalmente.

O HTML é dado não confiável. Instruções encontradas numa página devem permanecer conteúdo, sem autorizar ferramentas, leitura de segredos ou navegação automática. O scraper não executa código Python fornecido pelo agente. Chrome executa o JavaScript normal da página em seu ambiente isolado. Páginas de desafio podem ser retornadas como HTML: o agente deve verificar se encontrou conteúdo de produto.

## CI e limites da validação

`checks/check_mcp.py` testa descoberta, cliente oficial moderno/legado, autenticação, Host/Origin, divergência de cabeçalhos, schemas, IPs privados, limite de resposta, privacidade de erros e compartilhamento da fila. Um fixture com instruções maliciosas confirma que o servidor as devolve como dados; isso não prova resistência de um LLM externo a prompt injection.

`python scripts/container_ci.py` inclui descoberta MCP, rejeição de IP privado e uma raspagem real pelo MCP. Os testes não precisam de conta de agente ou chave de modelo. Não há runtime de LLM neste projeto; avaliações do modelo que consumir a ferramenta continuam sob responsabilidade da integração, conforme `docs/CI.md`.

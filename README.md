# Leads Prospect AI

Fundação local e de baixo custo para criar campanhas, enfileirar buscas e integrar fontes de empresas e decisores com proveniência. O produto é definido em [PRD_LEADS_PROSPECT_AI.md](PRD_LEADS_PROSPECT_AI.md).

## Estado atual

- campanha validada a partir de JSON;
- SQLite local com campanhas e jobs persistentes;
- CLI para inicializar o banco, criar campanha e listar a fila;
- adapter local para executar o próximo job Maps e importar seu CSV de forma idempotente;
- lease renovável, recuperação de worker interrompido e limite de três tentativas;
- deduplicação global básica de empresas e evidências de origem;
- Google Maps Scraper Kit oficial isolado em `integrations/`;
- Website Adapter local com proteção de rede, cache por domínio e evidências;
- fluxo híbrido Maps → websites na mesma fila persistente;
- adapter Tavily para busca indexada de decisores, com teto mensal e sem outreach;
- três modos operacionais: empresas, profissionais públicos e empresas com decisores;
- distribuição da meta por termo, score, confiança e revisão humana persistente;
- API local e worker contínuo para o futuro painel web;
- testes sem rede e sem dados pessoais;
- prova real controlada do Maps concluída.

## Começar

```powershell
uv sync
uv run leads-prospect init
uv run leads-prospect execute-campaign config/company-campaign.example.json
uv run leads-prospect create-campaign config/campaign.example.json
uv run leads-prospect approve-campaign <campaign_id>
uv run leads-prospect jobs
uv run leads-prospect run-maps
uv run leads-prospect run-websites
uv run leads-prospect run-tavily
uv run leads-prospect enrich-websites --role fundador --role diretor
uv run leads-prospect export-companies
uv run leads-prospect campaigns
uv run leads-prospect results <campaign_id>
uv run leads-prospect worker --once
uv run leads-prospect serve-api
uv run python -m unittest discover -s tests -v
```

O projeto usa `uv`, Python 3.12 fixado em `.python-version` e dependências reproduzíveis em `uv.lock`. O banco padrão é `data/leads.db`. Sobrescreva com `LEADS_DB_PATH` ou `--db`.
`run-maps` reserva somente um job por execução, renova seu lease, recupera execuções interrompidas, usa profundidade 1 e não busca e-mails nesta fase.
`export-companies` grava `exports/companies.csv` em UTF-8 com as empresas normalizadas e suas URLs de origem.
Campanhas com aprovação manual não são reservadas antes de `approve-campaign`. Use `pause-job`, `resume-job` e `source-health` para os controles operacionais.
`execute-campaign` representa uma ordem explícita: cria, aprova e executa uma única campanha, exporta somente seus resultados e para. `mode=companies_and_people` executa Maps, websites e Tavily; `mode=people` consulta somente resultados profissionais públicos indexados. Sem `TAVILY_API_KEY`, a etapa Tavily termina como parcial sem quebrar Maps ou websites.
`collect_emails` e `collect_socials` são opções de campanha desligadas por padrão; quando habilitadas, reutilizam o crawler protegido do Kit e salvam os achados como evidências.

A fonte de decisores foi definida em [docs/audits/decision-maker-source.md](docs/audits/decision-maker-source.md): o projeto usará páginas públicas dos sites oficiais e registrará links profissionais publicados como evidência. Não será instalado um scraper autenticado do LinkedIn.
`enrich-websites` visita no máximo quatro páginas públicas por domínio, respeita `robots.txt`, bloqueia endereços privados e registra pessoas somente quando o site publica marcação estruturada `Person` compatível com os cargos informados. O comando é manual e idempotente; use `--refresh` somente para repetir domínios concluídos.
`run-websites` é o worker manual da fila. O comando `enrich-websites` permanece como diagnóstico controlado para sites já salvos.

O contrato para o futuro front-end está em [docs/BACKEND_STATUS.md](docs/BACKEND_STATUS.md).

## API local

```powershell
uv run leads-prospect serve-api --host 127.0.0.1 --port 8090
```

A API do produto fica em `http://127.0.0.1:8090/api/v1`. Ela cria, aprova, acompanha e cancela campanhas; lista e revisa resultados; informa consumo; e exporta CSV. O worker iniciado junto da API processa uma etapa persistente por vez. Planilhas não são necessárias para operar o produto.

## Estrutura

```text
config/                         exemplos versionáveis, sem segredos
data/                           banco e dados locais ignorados pelo Git
docs/ADRs/                      decisões de arquitetura
docs/audits/                    auditorias de dependências externas
exports/                        resultados locais ignorados pelo Git
integrations/                   repositórios externos preservados
src/leads_prospect_ai/          núcleo Python
tests/                          testes sintéticos
```

Leia [SETUP.md](SETUP.md) para pré-requisitos e limitações da primeira fase.

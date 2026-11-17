# Instalação — Google Maps Scraper Kit

Opções novas de cobertura, profundidade/grade, aderência/filtros, DNS/MX e JSON: [guia operacional](docs/COBERTURA_EXTRACAO.md). O motor Gosom/Docker é mantido; não atualizar o submódulo para o Kit Python nesta instalação.

## Primeira instalação

Suporte validado: Windows, Docker Desktop, uv e Python 3.12. Porta da aplicação: `127.0.0.1:8090`. Motor original: `127.0.0.1:8081`; Nginx interno `8080`. Apache `8080` e PostgreSQL `17/5432` permanecem fora deste projeto.

```powershell
git submodule update --init --recursive
uv sync
Copy-Item integrations/google-maps-scraper-kit/.env.example integrations/google-maps-scraper-kit/.env
(Get-Content integrations/google-maps-scraper-kit/.env) -replace '^SCRAPER_PORT=8080$', 'SCRAPER_PORT=8081' | Set-Content integrations/google-maps-scraper-kit/.env
uv run google-maps-scraper-kit init
```

A distribuição técnica é `google-maps-scraper-kit` 0.2.0; imports usam `google_maps_scraper_kit`. `uv run gmaps` é o inicializador manual curto e `uv run google-maps-scraper-kit` expõe a CLI administrativa. O banco é criado/migrado sem apagar o histórico existente.

## Motor corrigido

Kit oficial preservado em integrations/google-maps-scraper-kit. Na raiz do produto:

```powershell
docker compose -f integrations/google-maps-scraper-kit/docker-compose.yml -f infrastructure/engine/compose.yaml build google-maps-scraper
docker compose -f integrations/google-maps-scraper-kit/docker-compose.yml -f infrastructure/engine/compose.yaml up -d
```

Usar ambos os arquivos; o Compose do Kit sozinho volta à imagem original sem patch. Não executar dois motores sobre o mesmo volume. Recriar com fila vazia. Não usar down -v ou limpeza destrutiva. Fonte/licenças/rollback em [auditoria do motor](docs/audits/engine-close-fix.md).

## Como executar o projeto

### Opção 1 — Inicialização manual

Inicie `gmaps-scraper` e `gmaps-scraper-ui` pelos botões **Start** do Docker Desktop ou execute:

```powershell
docker compose --env-file integrations/google-maps-scraper-kit/.env -f integrations/google-maps-scraper-kit/docker-compose.yml -f infrastructure/engine/compose.yaml up -d
uv run gmaps
```

Acesse `http://127.0.0.1:8090/`. `Ctrl+C` encerra API, interface e worker Python. O motor Docker é independente; pare-o pelos botões **Stop** ou, sem remover volume, com:

```powershell
docker compose --env-file integrations/google-maps-scraper-kit/.env -f integrations/google-maps-scraper-kit/docker-compose.yml -f infrastructure/engine/compose.yaml stop
```

### Opção 2 — Inicialização automatizada

Dê dois cliques em `ABRIR-APLICACAO.cmd` ou execute:

```powershell
.\scripts\start-local.ps1
```

O script inicia o Docker Desktop quando necessário e localizável, sobe o motor, aguarda sua API, inicia o mesmo backend Python em primeiro plano e abre `http://127.0.0.1:8090/`. `Ctrl+C` encerra o Python. Containers e Docker Desktop permanecem ativos para evitar interromper serviços que já estavam em uso.

Depois do `Ctrl+C`, encerre explicitamente os containers deste projeto com:

```powershell
.\scripts\stop-local.ps1
```

Esse comando recusa a operação enquanto a aplicação Python ainda estiver ativa, usa `docker compose stop` e preserva volumes, SQLite e exports.

Opções: `-NoBrowser` não abre o navegador; `-Preview` inicia somente a interface/histórico, sem worker e sem Docker. Se a API já estiver ativa, o script não cria uma segunda instância e informa que ela deve ser encerrada pelo terminal proprietário.

Nenhum modo instala serviço, agenda scraping, remove volumes ou apaga SQLite/exports. Não use `down -v`.

### Limitações conhecidas

- O fluxo completo foi validado no Windows com Docker Engine disponível, containers inicialmente parados e containers já ativos.
- A abertura automática partindo do Docker Desktop totalmente encerrado não foi executada durante esta manutenção para não alterar o estado global do computador. O script procura o executável a partir do Docker CLI e dos caminhos padrão, aguarda até 120 segundos e, se não o localizar, pede inicialização manual.
- macOS e Linux não foram validados para o script PowerShell; nesses sistemas, use o modo manual com Docker já disponível.

## Reprodutibilidade e dados locais

`uv sync --locked` valida as versões de Python. O submódulo fixa o Kit externo e o Compose fixa as imagens. `.env`, SQLite, exports, logs e resultados reais são ignorados pelo Git. Uma clonagem totalmente limpa ainda deve repetir os passos de **Primeira instalação**; não copie o `.env` ou o banco desta máquina para o GitHub.

## Sites e Playwright

Playwright Python está no uv.lock. No Windows, o backend usa o Edge instalado em headless, sem abrir janela ou usar o perfil pessoal. Em outra plataforma, instalar Chromium com `uv run playwright install chromium`; opcionalmente selecionar canal por variável de ambiente GOOGLE_MAPS_SCRAPER_KIT_BROWSER_CHANNEL. O motor Maps já possui seu navegador dentro da imagem e não precisa dessa instalação Python.

HTTP primeiro; fallback JS local quando há script e faltam contatos. Até quatro tentativas de página, 1 MB, robots/timeout e rede pública controlada. Renderização limitada a 20 s/20 requests, somente mesmo domínio. Não aceitar cookie/login nem contornar bloqueios. Sites que exigem CDN/API externa ou login podem permanecer parciais.

Firecrawl MCP está conectado no Codex. Não foi instalado Firecrawl autônomo ou self-hosted; o backend não depende dessa conta, API ou créditos. Não configurar Tavily/Apify. Chaves antigas locais permanecem ignoradas pelo Git e não são carregadas pelo novo produto.

## CLI de extração

```powershell
uv run google-maps-scraper-kit create-campaign config/campaign.example.json
uv run google-maps-scraper-kit approve-campaign ID
uv run google-maps-scraper-kit campaigns
uv run google-maps-scraper-kit results ID
uv run google-maps-scraper-kit export-companies
```

API com worker é o fluxo preferido. Não iniciar workers/execute-campaign adicionais em paralelo. O comando manual enrich-websites visita sites já salvos e deve ser usado conscientemente, pois faz acesso externo.

Histórico antigo permanece consultável e não pode ser retomado. Campos pessoais e scoring antigos não são exportados. CSV contém todas as empresas auditadas, com selected/selection_reason; filtrar selected=1 para usar a seleção.

Cada lista fechada mantém sua versão no SQLite, independente das próximas coletas. CSVs baixados pela API ficam em data/exports/ e podem ser regenerados sem nova busca. Preserve data/google-maps-scraper-kit.db e seus backups ao reorganizar a instalação; o banco fica fora dos containers. Detalhes/testes: docs/PERSISTENCIA_LISTAS.md.

## Testes

```powershell
uv run python -m unittest discover -s tests -v
.venv\Scripts\python.exe integrations/google-maps-scraper-kit/scripts/test_scrape.py
uv run python tests/browser_smoke.py
```

Prova real opt-in: `uv run python tests/live_web.py --execute`. Para consultar uma campanha v2 já executada sem nova coleta, usar --campaign-id ID. Não repetir criação em caso de dúvida; conferir histórico.

Documentação atual: [PRD](PRD_GOOGLE_MAPS_SCRAPER_KIT.md), [backend](docs/BACKEND_STATUS.md), [validação](docs/VALIDACAO_WEB.md).

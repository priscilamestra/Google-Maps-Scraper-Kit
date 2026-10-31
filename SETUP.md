# Configuração local

## Disponível nesta máquina

- Python 3.13;
- Git;
- Docker Desktop 4.97.0 com backend WSL 2;
- Google Maps Scraper Kit clonado em `integrations/google-maps-scraper-kit`.

## Portas locais

| Serviço | Host | Porta | Observação |
|---|---|---:|---|
| PostgreSQL 17 | configurado pelo serviço local | 5432 | não é alterado pelo projeto |
| Apache EnterpriseDB | configuração local do EnterpriseDB | 8080 | preservado para coexistir com outros sistemas |
| Google Maps Scraper | `127.0.0.1` | 8081 | definida em `integrations/google-maps-scraper-kit/.env` |

O Compose publica somente a porta externa do scraper em `127.0.0.1:8081`. A porta interna do Nginx e do motor continua `8080`; rede, volume e comunicação entre containers permanecem inalterados. O arquivo `.env` é local e ignorado pelo Git.

Confirme o ambiente com:

```powershell
docker --version
docker ps
Invoke-WebRequest http://127.0.0.1:8081/api/v1/jobs
```

Não exponha a porta do scraper além de `127.0.0.1`.

## Aplicação principal

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
uv run leads-prospect serve-api --host 127.0.0.1 --port 8090
```

`run-maps` reserva e executa somente o próximo job Maps em estado `queued`, importa o CSV no SQLite sem duplicar resultados do mesmo job e termina em `completed` ou `failed`. Nesta fase, usa profundidade 1 e não busca e-mails.

Campanhas com `manual_approval_required=true` permanecem fora da execução até `approve-campaign`. Jobs ainda não iniciados podem ser controlados com `pause-job <job_id>` e `resume-job <job_id>`. `source-health` mostra falhas consecutivas e abertura do circuit breaker.

`execute-campaign` é o caminho sob demanda: o próprio comando explícito cria, aprova, executa, normaliza e exporta uma única campanha. No modo híbrido, um job `website` é criado depois do Maps e recebe somente os IDs daquela campanha. Para a coleta adicional do Kit, habilite conscientemente `collect_emails` e/ou `collect_socials`; ambos ficam desligados por padrão.

`enrich-websites` processa os sites já salvos no banco, sem agendamento. Por domínio, lê `robots.txt` e no máximo quatro páginas entre início, contato, sobre e equipe. URLs privadas, portas não HTTP(S), respostas acima de 1 MB e páginas que excedem 10 segundos são recusadas. A primeira versão reconhece decisores publicados como JSON-LD `Person`; páginas puramente visuais ou dependentes de JavaScript ficam para o fallback Playwright sob demanda.

`serve-api` inicia a API do produto e um worker local em segundo plano. Mantenha a ligação em `127.0.0.1`; a API não possui autenticação porque foi projetada somente para uso local. O banco SQLite é a fonte principal da aplicação. CSV é exportação opcional, não armazenamento operacional.

## Testes

Núcleo do projeto:

```powershell
uv run python -m unittest discover -s tests -v
```

Kit externo, offline:

```powershell
python integrations/google-maps-scraper-kit/scripts/test_scrape.py
```

## Prova mínima do Maps

Execute dentro de `integrations/google-maps-scraper-kit`:

```powershell
docker compose up -d
docker compose ps
python scripts/scrape.py "cafes em Campinas SP" --city "Campinas, SP" --depth 1 --no-email --limit 5
```

Comece pequeno, um job por vez. Registre duração, linhas brutas, únicas, falhas e sinais de bloqueio antes de aumentar o volume.

# Arquitetura atual

```text
frontend futuro
      ↓ JSON/HTTP
API local :8090 ───── revisão, resultados e exportação
      ↓
SQLite ─────────────── campanhas, jobs, empresas, pessoas e evidências
      ↓
worker persistente
      ├─ Google Maps Kit :8081
      ├─ websites públicos
      └─ Tavily pública e limitada
      ↓
normalização → qualificação → revisão humana
```

O pacote principal é dono das regras e dos dados. O Google Maps Scraper Kit permanece preservado em `integrations/` e é acessado por wrapper. API e scraper aceitam somente conexões locais.

## Cobertura

| Requisito | Estado |
|---|---|
| Modos empresas, pessoas e híbrido | concluído |
| Meta distribuída por termo | concluído |
| SQLite e fila persistente | concluído |
| Lease, retry, backoff e circuit breaker | concluído |
| Maps oficial em Docker | concluído em `127.0.0.1:8081` |
| Sites, e-mails e redes publicados | concluído, com falhas externas marcadas como parciais |
| Tavily limitada e sem cobrança automática | concluído |
| Confiança de decisores | `verified`, `likely` ou `candidate` |
| Score e campos obrigatórios | concluído |
| Revisão `pending/approved/rejected` | concluído |
| API local para o painel | concluído em `127.0.0.1:8090` |
| CSV por campanha | concluído |
| Planilha/Sheets | opcional, não necessário para o produto |
| Frontend | pendente em trabalho separado |
| Outreach | fora do escopo |

## Limitações assumidas

- páginas dependentes de JavaScript podem exigir futuro fallback Playwright;
- nomes de funcionários e porte só são usados quando uma fonte pública os comprova;
- perfis `candidate` nunca são apresentados como confirmados;
- cancelar impede as próximas etapas, mas não interrompe no meio uma requisição externa já iniciada;
- SQLite atende ao uso local de uma usuária; PostgreSQL só será necessário com concorrência ou implantação remota.

Dados reais ficam em `data/` e `exports/`, fora do Git. Credenciais ficam somente no `.env` local.

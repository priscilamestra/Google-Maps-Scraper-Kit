# Arquitetura — Google Maps Scraper Kit

```text
Painel existente / chat Codex / CLI
          ↓ HTTP/JSON
API local :8090
          ↓
SQLite: campanhas v2, jobs, empresas, contatos, evidências
          ↓
worker serial → Maps Kit :8081 → Gosom closefix1
          ↓
Website HTTP protegido → fallback Playwright local
          ↓
normalização → filtros explícitos → revisão → CSV único
```

Python é dono das regras e do estado. Não há Tavily, Apify, Supabase, CRM, scoring comercial ou descoberta de pessoas no runtime. Skill/MCP do Codex são ferramentas do agente, não componentes executados automaticamente na aplicação.

| Componente | Responsabilidade |
|---|---|
| models/db | empresas apenas; campanhas v2, histórico legado preservado sem retomada |
| maps/normalization | wrapper do Kit, bruto e identidade observada, coordenadas/fontes |
| website/contacts | HTTP público, até 4 tentativas de página, contatos institucionais, cache |
| rendering | JS em processo local limitado a 20 s; fetches controlados pelo Python |
| qualification | nome interno legado; agora somente filtros declarados e raio, sem score |
| worker/service | Maps → sites quando solicitado; nunca enfileirar fonte comercial |
| export | uma empresa por linha, redes/fontes/evidence_json; colunas pessoais antigas vazias |
| api/static | interface existente; nome/opções ajustados, sem redesenho |

Portas: aplicação 127.0.0.1:8090; Kit 127.0.0.1:8081 → Nginx interno 8080. Apache 8080 e PostgreSQL 17/5432 preservados. Docker usa o mesmo volume de resultados.

Backend Playwright é uma dependência uv real e usa Edge no Windows. O navegador do Maps continua na imagem Go; são execuções distintas. Firecrawl MCP foi verificado no Codex, sem self-hosting ou credencial hospedada instalada na aplicação.

Compatibilidade: pacote `google_maps_scraper_kit`, distribuição `google-maps-scraper-kit` e tabelas legadas permanecem para preservar instalação/histórico. Nome público e diretório: Google Maps Scraper Kit. Contexto normativo: [PRD vigente](../PRD_GOOGLE_MAPS_SCRAPER_KIT.md). Validações atuais: [estado do backend](BACKEND_STATUS.md).

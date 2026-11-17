# Backend — Google Maps Scraper Kit 0.2

Contrato vigente de extração institucional. Escopo completo: [PRD](../PRD_GOOGLE_MAPS_SCRAPER_KIT.md). Evidências operacionais: [validação web](VALIDACAO_WEB.md).

## Execução

`uv run gmaps` inicia API e worker em primeiro plano e encerra com `Ctrl+C`, sem controlar o Docker. `ABRIR-APLICACAO.cmd` chama `scripts/start-local.ps1`: no Windows, ele inicia a infraestrutura necessária, mas não desliga containers implicitamente. Após o `Ctrl+C`, `scripts/stop-local.ps1` encerra os containers com volumes preservados. `--no-worker` mantém prévia. Não há agendamento nem execução ao abrir a página.

Campanhas novas aceitam somente mode=companies e recebem schema_version=2 no servidor. E-mails/redes são opt-in. Maps coleta empresas; uma etapa website faz enriquecimento institucional se solicitado. Trabalhadores e CLI não carregam Tavily ou Apify.

Ampliação regional: depth, grid_size, deep_scan e max_queries; filtros min_rating/min_reviews/no_website/exclude_campaign_ids; verify_emails com estados DNS por lista. Profundidade padrão nova 5; campanhas antigas conservam configuração. Contrato completo em [COBERTURA_EXTRACAO.md](COBERTURA_EXTRACAO.md). Jobs retornam query/progress, e a campanha retorna contagens finished/total.

Novos pedidos usam match_business_terms=true; category_terms permite alternativas explícitas. Filtro literal de nome/categoria evita selecionar sugestões de outros segmentos. Links sociais diretos do Maps e redirecionamentos publicados preservados; parser/cache v3, sem mudar listas fechadas. Testes atuais: 53 aprovados, além do smoke test desktop/mobile. Prova final: 5 empresas aderentes, 3 com redes, 0 e-mails recuperados nesse recorte; partial por sites inacessíveis. Não confundir as 54 empresas brutas do diagnóstico anterior com 54 agências.

GET /api/v1/campaigns/{id}/export?format=json baixa a lista persistida como JSON; `format=xlsx` gera Excel real e `format=csv` mantém o CSV UTF-8 com BOM. Os três formatos aceitam `company_id` repetido para exportar exatamente o conjunto filtrado/selecionado pela interface. Metadados institucionais adicionais do Maps ficam em maps_details (sem nomes/perfis de autores de reviews), e a exportação inclui maps_details_json, published_emails, email_host e email_checks_json. Evidências originais não são apagadas por falha DNS.

## Rotas preservadas

```text
GET    /api/v1/health
GET    /api/v1/runtime
GET    /api/v1/geocode?q=cidade
GET    /api/v1/campaigns
POST   /api/v1/campaigns
GET    /api/v1/campaigns/{id}
DELETE /api/v1/campaigns/{id}
POST   /api/v1/campaigns/{id}/approve
POST   /api/v1/campaigns/{id}/cancel
POST   /api/v1/jobs/{id}/pause
POST   /api/v1/jobs/{id}/resume
GET    /api/v1/campaigns/{id}/results
PATCH  /api/v1/campaigns/{id}/results/{company_id}
GET    /api/v1/campaigns/{id}/export?format=csv|xlsx|json&company_id={company_id}
GET    /api/v1/usage
GET    /api/v1/sources/health
```

Não há endpoint de revisão/pesquisa de pessoas. Jobs são etapas de execução. Histórico de consumo antigo é diagnóstico, sem chamadas a essas fontes. Host e Origin locais são obrigatórios.

## Dados

Resultados: nome/categoria/endereço, telefone primário e phones, website, emails, Instagram/Facebook/LinkedIn institucional/TikTok/YouTube/Twitter/X/WhatsApp, coordenadas/place_id/maps_url, fontes e evidências, data, revisão, seleção e motivos de exclusão. Não há decision_makers ou score/confiança comercial na resposta.

CSV tem uma linha por empresa e evidence_json. Colunas antigas decision_maker_* / fit_score / confidence ficam vazias por compatibilidade. Campos ausentes continuam vazios/null, sem adivinhar país, e-mail ou perfil.

Selecionar até a meta usa nome/ID estáveis, sem ranking comercial. Termos negativos, campos obrigatórios e raio são filtros explícitos. Rejeitar selecionado permite selecionar o próximo elegível.

## Histórico e estados

Campanhas anteriores têm read_only=true: não são retomadas pelo worker nem podem ser aprovadas. Configuração/jobs existentes permanecem; tabelas pessoais antigas são preservadas, sem exportação/revisão de pessoas. Cada lista terminal mantém sua visão empresarial em campaign_companies.result_json e campaigns.results_frozen_at; o catálogo global pode mudar em coletas posteriores sem reescrever a lista fechada. Revisão/seleção pertencem à própria lista e permanecem editáveis quando permitido. Migração de listas antigas preserva o estado disponível, sem inventar versões já sobrescritas no passado.

CSV: GET /campaigns/{id}/export regenera a lista salva e grava atomicamente exports/campaign-{id}.csv ao lado do SQLite (data/exports na instalação atual). Não depende do diretório de trabalho, de Docker ou da existência prévia do CSV. Arquivo ausente é recriado sem consultar a fonte. Metadados e mapa vêm da mesma lista salva. Relatório: PERSISTENCIA_LISTAS.md.

Exclusão: `DELETE /api/v1/campaigns/{id}` remove definitivamente a campanha, seus jobs, resultados brutos, vínculos, verificações de e-mail e arquivos exportados. Empresas/evidências compartilhadas com outra campanha são preservadas; catálogos órfãos são removidos. Respostas: `204` em sucesso, `404` para ID inexistente e `409` se houver job em execução. O frontend deve pedir confirmação explícita antes de enviar `DELETE`; para uma campanha em execução, deve cancelar e aguardar o job parar antes de excluir.

Exportação filtrada: repita `company_id` na query para cada resultado visível/escolhido. Sem `company_id`, exporta a lista inteira. Há limite de 5.000 IDs por requisição. CSV e XLSX retornam `X-Result-Count`; JSON retorna `{campaign, results}`. A ação Copiar continua local no navegador e usa somente os resultados visíveis.

Fila, lease, heartbeat, retry limitado e circuit breaker preservados. Website inacessível não descarta o restante: etapa/campanha pode terminar partial com erro registrado. Cancelamento impede próximas etapas; pausa atua em jobs não ativos.

## Rede e navegador

HTTP: robots, IP público fixado na conexão, redirecionamentos validados, 1 MB, timeout de rede 10 s, até 4 tentativas de página. Cache inclui URL e opções. LinkedIn /in/ individual é recusado como rede institucional.

Playwright: processo filho local, timeout global 20 s com encerramento de seus filhos; somente GET do mesmo domínio, fetcher protegido, até 20 requests interceptados, Service Workers/WebSockets/downloads/mídia bloqueados. CDN externa e sites dependentes de login podem permanecer incompletos. Nenhum login ou bypass de bloqueio.

Leaflet/OSM, geocodificação manual Nominatim e seus limites/atribuição permanecem. Raio filtra após coleta e não garante cobertura completa. O limite do Kit limita sua saída, não rigidamente as páginas visitadas internamente.

## Codex e Firecrawl

A skill do projeto chama estas mesmas rotas; não cria crawler ou banco paralelo. Firecrawl MCP existe na sessão Codex e foi verificado; não é dependência autônoma da aplicação. Não herdar tokens nem supor gratuidade por haver créditos na conta. Uso hospedado/self-hosted no backend permanece não instalado e opcional.

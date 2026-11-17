# Validação web — Google Maps Scraper Kit 0.2

## Usar

1. No modo manual, deixe Docker Desktop e os containers do motor ativos e execute `uv run gmaps`. No modo automatizado, dê dois cliques em `ABRIR-APLICACAO.cmd`.
2. Confirme no terminal as URLs `8081` (motor) e `8090` (aplicação personalizada).
3. Abra http://127.0.0.1:8090/ e selecione a campanha mais recente no histórico.
4. Para nova extração, informe segmento/cidade, Localizar, bairro opcional, raio, meta e limite; escolha e-mails/redes.
5. Clique Iniciar busca uma vez. Acompanhe Maps e website; confira dados/fontes, revisão e CSV.

A página existente foi preservada. Nome, opções pessoais e rótulos de score foram ajustados; não houve redesenho Stitch.

## Prova real da refatoração — 02/11/2026

Campanha `9dc3f64a-cb91-4566-974d-7764c3661460`, criada e aprovada pelos controles reais no Edge: agência de marketing em Nova Friburgo, meta 5, limite de saída 10, raio 10 km, e-mails/redes solicitados.

- Maps concluído na primeira tentativa: 10 empresas únicas; 5 selecionadas, com coordenadas dentro do raio.
- Enriquecimento institucional: 8 sites avaliados; 5 completos, 10 páginas lidas, 3 sites inacessíveis.
- Falhas: dois sites sem páginas permitidas/acessíveis conforme robots; um domínio não resolveu no DNS. Não foi feito bypass.
- Estado final **partial**, porque a etapa website não pôde cobrir os três sites. Dados válidos preservados, meta de empresas atingida.
- Entre as cinco selecionadas: 5 telefones, 5 sites, 1 com e-mail e 3 com redes disponíveis no banco. Contatos anteriores podem ser reutilizados; isso não significa que todos foram descobertos pela primeira vez nesta execução.
- CSV: 10 linhas empresariais auditadas, uma por company_id; selected indica as 5 escolhidas. Nenhum dado de decisor ou score comercial exportado.
- Mapa, popup, revisão/restauração, exportação e reload passaram sem erro JavaScript.
- Nenhum novo crédito Tavily/Apify usado. Histórico de consumo preservado.

Capturas e JSON reais estão em data/live-validation/, ignorados pelo Git. Não versionar contatos pessoais, chaves ou CSVs reais.

## Testes e critérios

A suíte atual cobre: escopo empresarial, rejeição de pessoas/cargos, histórico sem retomada, fila/lease/retry/circuito, migração, raio/bairros/idiomas, e-mails/texto/JSON-LD, telefones, LinkedIn institucional, cache por opção, limites de tentativas, DNS fixado e CSV único sem pessoas.

Teste de Playwright executa JavaScript real e bloqueia requests privados; outro força script infinito e comprova timeout/encerramento do processo próprio. Isso comprova o mecanismo, não compatibilidade universal com sites reais.

Browser smoke: painel/mapa, desktop/celular, idiomas, bairro vazio, filtros, revisão, pausa/retomada/cancelamento e modo de prévia. Kit oficial e correção Go conservam suas provas anteriores; não apresentar testes removidos de pessoas como cobertura do novo escopo.

Critério de aceite: campanha empresarial → Maps → sites conforme opt-in → contatos com evidência → CSV consistente. Parcial por robots/DNS deve aparecer claramente, sem inventar contatos. Aprovação estética final pertence à próxima etapa.

## Limites conhecidos

Sites bloqueados, sem DNS, dependentes de login/CDN externa ou sem contato publicado podem ficar incompletos. Firecrawl é ferramenta MCP do Codex; não há adapter autônomo pago/self-hosted no produto. Grade automática, profundidade configurável e subdivisão agora estão implementadas; [contrato e nova prova regional](COBERTURA_EXTRACAO.md). O raio não garante varredura integral nem volume diário.

Campanhas antigas têm indicação de histórico de leitura. Não usar seus modos pessoais para novas execuções. PRD vigente: [Google Maps Scraper Kit](../PRD_GOOGLE_MAPS_SCRAPER_KIT.md).

## Persistência de listas

Histórico operacional em SQLite preservado e ampliado com versão por lista fechada, sem redesenho. Testes de duas listas independentes, mapa e downloads: `uv run python tests/history_browser.py`. Resultados/CSV reais permaneceram idênticos após recriar o motor e reiniciar a API. CSV ausente é regenerado pelo download. Migração, limites e provas: [PERSISTENCIA_LISTAS.md](PERSISTENCIA_LISTAS.md).

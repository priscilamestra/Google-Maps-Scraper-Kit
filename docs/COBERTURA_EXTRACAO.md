# Cobertura regional e extração institucional

Atualização de 2026-11-02. Mantidos o Kit instalado, seu commit, Gosom v1.18.1 closefix1, Docker, volume, redes e portas. Não houve migração para o motor Python 2.x do Kit.

## Usar na aplicação

1. Abra http://127.0.0.1:8090/. Informe os termos e a cidade; confirme o centro com Localizar ou no mapa.
2. Ajuste o raio e a quantidade desejada. E-mails e redes estão ligados no formulário novo; podem ser desligados conscientemente. Pedidos antigos conservam suas opções.
3. Em Cobertura da busca, use divisão automática ou uma grade explícita. Profundidade padrão 5. Aprofundamento de regiões saturadas é opcional, ligado no formulário novo.
4. Em filtros, escolha nota/avaliações, ausência de site, campos obrigatórios, termos negativos, listas anteriores a excluir e verificação DNS/MX, se desejado.
5. Iniciar busca salva e aprova somente esse pedido. Acompanhe as etapas e a região pesquisada; o contador do motor é bruto e não deve ser confundido com empresas únicas selecionadas.
6. Reabra a lista no histórico, consulte mapa e contatos e baixe CSV ou JSON. Reabrir/exportar não executa a busca novamente.

Para outra cidade, crie outra lista com seu centro confirmado. A fila pode manter pedidos de várias cidades e processa-os serialmente; cada lista preserva seus parâmetros e resultados. Não iniciar um segundo worker ou motor.

## Contrato de campanha

Exemplo completo: [regional-campaign.example.json](../config/regional-campaign.example.json). API, CLI e chat usam o mesmo contrato.

| Campo | Significado |
|---|---|
| depth | 1..40; padrão 5 nas novas campanhas. O significado exato é o do Gosom instalado, não uma promessa de 20 resultados por passo do motor novo |
| grid_size | 0 automático; 1 busca simples; 2..10 N×N regiões |
| deep_scan | Subdivide regiões com pelo menos 100 resultados brutos em quatro; no máximo dois níveis extras |
| max_queries | 1..100, contando termos, bairros, consulta inicial e subdivisões. Rejeita plano inicial que não cabe |
| search_area | Centro confirmado e raio 1..10000 m; a geometria não inventa bairros reais |
| collection_limit | Orçamento de candidatos únicos importados; não orçamento de páginas visitadas pelo motor |
| target_count | Quantidade desejada selecionada após filtros; disponibilidade não garantida |
| min_rating / min_reviews | Mínimos declarados. Campo ausente não é nota zero presumida nem satisfaz o filtro |
| match_business_terms / category_terms | Novos pedidos exigem aderência textual ao tema. Alternativas explícitas ampliam o vocabulário aceito; não é classificação semântica por IA |
| no_website | Somente empresas sem site conhecido; incompatível com exigir site |
| exclude_campaign_ids | Até 100 listas existentes cujas empresas devem ser ignoradas; opcional |
| verify_emails | Requer collect_emails; consulta DNS/MX público sem chave, sem assinatura ou créditos |
| language | Código de duas letras, padrão pt; o país textual continua sendo localização, não parâmetro de ranking do motor |

Automático: até limite 20 usa consulta simples; acima disso usa grade 2×2 (limite até 100) ou 3×3 (acima de 100), além da consulta inicial. Cada termo/bairro gera consultas próprias, sempre seriais. Grade 10×10 ocupa as 100 consultas e dispensa a inicial adicional; mais termos precisam caber no mesmo orçamento ou o pedido é rejeitado.

A grade cobre geometricamente o círculo com regiões sobrepostas. Deduplicação entre regiões evita contar a mesma empresa duas vezes. Resultados fora do raio/sem coordenadas verificáveis não consomem orçamento regional e continuam no CSV bruto local da consulta. Filtros prévios de nota/avaliações/site/telefone/termos negativos/listas anteriores também não desperdiçam esse orçamento. E-mails/redes dependem da etapa posterior dos sites; podem reduzir a seleção final abaixo da meta.

Ao atingir collection_limit, as regiões ainda não solicitadas ao motor são encerradas com explicação, sem novas consultas. Se uma região continuar saturada no limite de aprofundamento/consultas, a etapa termina parcial. Cancelar impede próximas consultas e preserva dados; a operação externa corrente pode terminar antes de parar. Não há busca agendada ou evasão de bloqueios.

## Como o motor é reutilizado

scripts/scrape-area.py importa o cliente oficial do Kit instalado e reutiliza run_job, dedupe e clean_website. Envia latitude/longitude, raio e zoom de cada região à API Gosom existente. Nenhuma edição no submódulo ou substituição do scraper. Referência: Kit commit 4746e8a80baea7ae501d0f4754444b26bdd45085, scripts/scrape.py; licença MIT preservada.

O motor gera seu CSV bruto completo; ele é gravado atomicamente em data/maps-jobs e importado com o orçamento regional. Não truncar cada região antes de contabilizar saturação ou eliminar sobreposições. Consultas antigas sem opções novas continuam compatíveis com a chamada anterior ao script oficial.

Progresso: um arquivo local por consulta registra ID nativo, estado e linhas brutas. O controlador persiste esse progresso no SQLite enquanto renova o lease. GET /campaigns/{id} expõe jobs[].query, jobs[].progress e progress.finished/total. Não persistir proxy ou credencial nesses campos.

Proxy não é necessário. O wrapper regional aceita opcionalmente a variável de processo SCRAPER_PROXIES como lista JSON de URLs compatíveis com o Gosom, caso a pessoa já possua infraestrutura própria. Não carregar ou exibir credenciais na campanha, não usar para contornar CAPTCHA/bloqueios e não contratar serviço automaticamente. Sem configuração, não usa proxy.

## E-mails, sites e DNS

HTTP/Playwright e proteções existentes preservados. Extração inclui mailto, texto visível, JSON-LD institucional, [at]/[dot]/[arroba]/[ponto] e data-cfemail já recebido no HTML. Até quatro tentativas de página; Contato/Sobre/Equipe no mesmo domínio, robots respeitado. Não navegar em perfis sociais nem inferir contatos pessoais.

O Google pode misturar sugestões de outros segmentos. O filtro compara palavras de nome/categoria com os temas, ignorando conectivos e descritores genéricos quando existe termo mais específico. Sem correspondência, não seleciona como empresa do nicho. Não inventa equivalência semântica: termos alternativos devem ser explícitos em category_terms. Isso pode excluir empresas cujo nome/categoria não evidenciam a atividade; revisar ou ampliar as alternativas, sem contar resultados incertos como válidos. Campanhas antigas conservam seus parâmetros e dados.

Verificação DNS consulta https://dns.google/resolve, sem autenticação. MX válido recebe categoria de provedor quando reconhecida. Sem MX explícito, A/AAAA podem representar MX implícito; não eliminar esse domínio por ausência de MX. NXDOMAIN, null MX ou ausência confirmada de MX/A/AAAA ficam inválidos. Timeout/SERVFAIL/erro ficam unknown e mantêm o e-mail publicado; a etapa registra parcial. Não enviar e-mail nem tentar SMTP para testar caixas.

email_checks é vinculado à lista e email; o domínio é consultado uma vez por execução e checks definitivos são reaproveitados numa retomada da mesma lista. published_emails e evidence preservam o que o site publicou, inclusive quando o endereço é retirado de emails por DNS inválido. Nada disso altera listas já fechadas.

Campos adicionais institucionais disponíveis no bruto do Gosom são expostos em maps_details: horários, popularidade, imagens, categorias, descrição, histograma, preço, endereço completo e status. Não incluir nomes/perfis de autores de reviews ou dados de proprietário pessoal. A disponibilidade de cada campo depende do motor e da fonte.

## Exportação e persistência

- CSV: GET /api/v1/campaigns/{id}/export, mantém o contrato anterior e acrescenta published_emails/email_host/email_checks_json/maps_details_json.
- JSON: GET /api/v1/campaigns/{id}/export?format=json, download com metadados da lista, jobs e resultados persistidos.
- SQLite permanece fonte durável; snapshots finalizados incluem os novos campos e verificações. CSV é regenerável e continua com escrita atômica ao lado do SQLite.
- Nenhum dado de decisor, score comercial, Tavily, Apify ou Supabase foi reintegrado.

## Validação

50 testes automatizados aprovados; navegador desktop/celular e histórico independente aprovados. Cobertura: grade, meridiano, raio, sobreposição, orçamento, saturação/retomada, aderência/filtros, DNS válido/inválido/incerto/MX implícito, cache, evidências, redes redirecionadas/diretas do Maps e JSON. Wrapper/progresso testados com cliente simulado. Skill validada pelo quick_validate oficial em UTF-8.

Prova real opt-in: uv run python tests/live_coverage.py --run; relatório em data/live-validation/coverage-report.json, ignorado pelo Git. Esse comando executa cinco consultas regionais de marketing em Nova Friburgo, raio 3 km, profundidade 5, meta 60 e limite 120, depois sites e DNS/MX. Verifica listas antigas, coordenadas, duplicatas, CSV/JSON e integridade SQLite. O resultado real, inclusive eventual quantidade abaixo da meta ou falhas de sites, deve ser informado separadamente.

Não foi feito benchmark equivalente ao motor Python do Kit. Grade, profundidade e mais campos não garantem a existência de 60 empresas do nicho dentro de um raio, 100% de contatos nem volume diário fixo. Nota/avaliações são filtros operacionais; intent_score/ICP e pesquisa de pessoas continuam no Lead Intelligence AI.

### Provas reais desta implementação

1. Diagnóstico regional 4b8c9dbd-6c68-4329-90a5-437d3e568c64: cinco consultas Maps reais concluídas; 54 empresas brutas dentro de 3 km, 591,3 s incluindo sites. Conferência posterior: somente 8 aderentes a marketing/publicidade/publicitária e 46 de outros segmentos. Não comprova 54 agências. Nos diversos segmentos houve 8 empresas com e-mail e 9 verificações DNS; nenhuma das 8 aderentes tinha e-mail recuperado. Essa descoberta motivou o filtro de aderência. coverage-report.json registra a distinção.
2. Prova com aderência 56fe4abc-95a7-48a1-98df-73a9c9a4af21: meta/limite 5, profundidade 3, grade 2×2; cinco empresas, duas com redes, 82,8 s. Regiões restantes dispensadas ao atingir orçamento; 13 históricos idênticos.
3. Prova final 01c24448-06ab-44be-8339-1d7c9a068af8: cinco empresas aderentes, três com redes, nenhum e-mail recuperado nesse recorte; 101,2 s. partial por sites inacessíveis, sem falha Maps; meta empresarial atingida. CSV/JSON, raio, duplicatas, integridade e 14 listas anteriores verificados. Relatório coverage-final-report.json.

A auditoria do HTML acessível dos sites aderentes não encontrou e-mails nas páginas examinadas. Também havia domínio 404, perfil social impedido por robots e empresas sem website. Isso não comprova inexistência de contato em outra fonte; não inventar e-mails nem contar contatos de outros segmentos. Extração de e-mail passou em testes sintéticos e ocorreu no diagnóstico real em outros segmentos.

Redes publicadas no website do Maps e destinos Google/Facebook são decodificados sem visitar perfis. Parser agora usa cache institucional v3 para que scans v2 não impeçam nova extração; snapshots fechados continuam imutáveis. Motor/imagem e submódulo intactos; nenhum crédito de API comercial utilizado.

Verificação final após reiniciar a API: as 15 listas preservaram os mesmos resultados e o CSV final manteve o mesmo hash. Integridade SQLite/chaves estrangeiras válidas; API/worker e Maps disponíveis. Portas 8090/8081 do projeto, Apache 8080 e PostgreSQL 5432 continuaram acessíveis. Relatório local coverage-readiness.json. Na prova final, três sites ficaram completed e dois failed; estado parcial não é falha do motor nem confirmação de ausência de e-mail nos sites inacessíveis.

# Google Maps Scraper Kit — PRD
Versão 2.3 · 03/11/2026 · produto local, backend e interface validados.

Este documento substitui o escopo comercial anterior. A versão original está preservada em [arquivo histórico](docs/archive/PRD_LEGACY_V1.1.md). Não autoriza criar o segundo produto nem reintegrar suas funções.

## 1. Problema e posicionamento

Encontrar empresas locais e reunir dados publicados no Maps e em seus websites exige trabalho repetitivo. O Google Maps Scraper Kit recebe segmento, localidade e quantidade desejada; coleta empresas, extrai contatos institucionais rastreáveis e entrega uma base CSV. Dados ausentes continuam ausentes.

O produto não avalia intenção de compra, capacidade comercial, perfil pessoal ou adequação ao ICP. A meta é uma quantidade desejada, nunca garantia de disponibilidade.

## 2. Objetivos

- Descobrir empresas por termo, nicho/categoria e localidade usando o motor Maps instalado.
- Preservar telefone, site, endereço, categoria, coordenadas, avaliações e origem.
- Visitar sites mediante opção explícita para obter e-mails, telefones adicionais e canais institucionais publicados.
- Deduplicar empresas/contatos e exportar uma linha por empresa.
- Funcionar sob demanda no computador, preservando histórico operacional e permitindo revisão dos dados.
- Evitar novas assinaturas, cobrança por requisição ou créditos externos como requisito.

## 3. Fronteira entre produtos

| Google Maps Scraper Kit — agora | Lead Intelligence AI — projeto futuro |
|---|---|
| Empresas, sites, contatos e redes institucionais | Decisores, CEOs, fundadores e perfis individuais |
| Filtros declarados de categoria, endereço, raio e presença de contato | Scoring comercial, ICP, qualificação avançada |
| Listas persistentes, revisão operacional, mapa e CSV em SQLite | Histórico de ICP, qualificação, inteligência/prospecção e Supabase |
| CSV com evidências | Mensagens personalizadas e integrações comerciais |
| Python/HTTP/Playwright local, motor Maps | Tavily, Apify e outros enriquecedores a avaliar separadamente |

Não fazem parte deste produto: scraping autenticado, login em redes sociais, evasão de CAPTCHA/bloqueios, outreach, APIs comerciais para descoberta de pessoas, Supabase ou comunicação direta com o segundo projeto.

## 4. Arquitetura existente e pretendida

```text
Painel existente / chat Codex / CLI
                ↓
API HTTP local :8090
                ↓
SQLite: campanhas → fila persistente → resultados/evidências
                ↓
Maps Adapter → Kit oficial :8081 → Gosom closefix1 em Docker
                ↓
normalização + deduplicação
                ↓
Website Adapter HTTP → fallback Playwright quando necessário
                ↓
filtros explícitos → seleção determinística → revisão → CSV
```

Essa é a arquitetura implementada na refatoração; não foi trocada por SaaS, Redis/Celery ou banco remoto. SQLite é o armazenamento local. PostgreSQL 17 e Apache existentes não participam do pipeline e permanecem preservados.

O nome público e o diretório local são Google Maps Scraper Kit. A distribuição Python `google-maps-scraper-kit`, o pacote `google_maps_scraper_kit` e os aliases CLI permanecem por compatibilidade com instalações e histórico. Esses identificadores técnicos não definem a marca pública.

## 5. Responsabilidades e disponibilidade

| Componente | Papel e estado |
|---|---|
| Python | API, fila, filtros, HTTP protegido, normalização, evidências, CSV; implementado |
| Playwright do motor Go | Navega no Maps; instalado na imagem Docker corrigida |
| Playwright Python | Fallback local de sites JS, dependência instalada com uv; testes executam JS real |
| Skill Codex | Interpreta pedidos e usa a mesma API; funciona durante sessão do agente, não é worker de produção |
| Firecrawl MCP | Disponível/autenticado no Codex e consultado nesta revisão; auxilia pesquisa/extração assistida, não roda automaticamente no backend |
| Firecrawl hospedado/self-hosted | Nenhum adapter ou servidor instalado no produto; opcional e não requisito financeiro |

A API do produto não herda tokens MCP ou a assinatura do GPT. Não declarar integração autônoma Firecrawl apenas por haver uma skill instalada. O contrato operacional está no [estado do backend](docs/BACKEND_STATUS.md).

## 6. Entrada e planejamento

Exemplo de campanha:

```json
{
  "name": "Agências — Nova Friburgo",
  "mode": "companies",
  "target_count": 5,
  "collection_limit": 10,
  "business_terms": ["agência de marketing"],
  "location": {"city": "Nova Friburgo", "state": "RJ"},
  "neighborhoods": [],
  "language": "pt",
  "collect_emails": true,
  "collect_socials": true,
  "manual_approval_required": true,
  "search_area": {"latitude": -22.3061661, "longitude": -42.4934183, "radius_m": 10000}
}
```

Centro precisa ser escolhido no mapa ou confirmado por geocodificação manual; as coordenadas do exemplo são a referência já obtida para essa cidade, não um centro universal. Idioma: código de duas letras, padrão pt. Até 20 bairros explícitos; cidade geral permanece no mesmo histórico. A busca também divide automaticamente o raio em regiões geométricas, sem inventar nomes de bairros.

`target_count` limita a seleção após filtros. Na busca regional, `collection_limit` limita empresas únicas importadas dentro do raio, antes dos filtros de contato que dependem dos sites; sobreposições e resultados fora do raio não consomem esse orçamento. Brutos completos ficam em data/maps-jobs. Consultas simples sem grade conservam a distribuição do limite por termo/bairro. Nenhum desses limites representa teto rígido de páginas visitadas pelo motor. Coleta adicional tem teto 5.000. Raio de 1 a 10.000 m é aplicado por Haversine; não comprova cobertura integral do círculo.

Opções de cobertura: depth 1..40 (padrão 5), grid_size 0 automático/1 somente inicial/2..10 grade explícita, deep_scan opcional e max_queries 1..100. A grade automática usa busca simples até limite 20, depois 2×2 ou 3×3. Aprofundamento divide regiões com pelo menos 100 resultados brutos em quatro, até dois níveis adicionais. Consultas iniciais/termos/bairros/subdivisões contam no orçamento; plano inicial maior é rejeitado, esgotamento durante aprofundamento fica parcial. O painel oferece aprofundamento ligado nos pedidos novos; API mantém opt-in explícito.

Filtros adicionais: min_rating 0..5, min_reviews inteiro, no_website e exclude_campaign_ids opcionais. Ausência de nota/avaliações não satisfaz um mínimo declarado. Exclusão de empresas de outras listas é opcional, preservando a independência do histórico. verify_emails, com collect_emails, verifica DNS/MX sem chave; domínio comprovadamente inválido sai de emails, mas permanece em published_emails/evidências. Falha de DNS mantém o contato unknown e a etapa parcial. MX não comprova existência da caixa. Controles e contrato em [COBERTURA_EXTRACAO.md](docs/COBERTURA_EXTRACAO.md).

Novos pedidos usam match_business_terms=true para evitar selecionar sugestões de outros segmentos trazidas pelo Google. Filtro literal de nome/categoria, sem score comercial. category_terms aceita alternativas explícitas; vazio usa os termos pesquisados. Falta de evidência textual não é confirmação de atividade; manter diagnóstico e não prometer classificação semântica perfeita. Parâmetros e dados de listas antigas não são alterados.

## 7. Fluxo de execução

1. Validar campanha exclusivamente empresarial; rejeitar modo de pessoas, cargos e filtro de decisor.
2. Persistir pedido e consultas; executar só após aprovação explícita.
3. Executar um job Maps por vez; importar bruto e deduplicar por identidade observada.
4. Preservar fonte, coordenadas e campos do Maps; sem inventar porte ou contatos.
5. Se e-mails/redes forem solicitados, enfileirar etapa institucional dos sites da campanha.
6. HTTP primeiro: robots.txt, página inicial e páginas relevantes (Contato/Sobre/Equipe), até quatro tentativas de página por domínio/URL de entrada.
7. Se faltam contatos e há indício de JavaScript, executar fallback local limitado.
8. Consolidar e-mails, telefones e links públicos, cada um com origem/data/método.
9. Aplicar somente filtros declarados e raio. Selecionar até a meta em ordem determinística por nome/ID; sem pontuação comercial.
10. Revisar no aplicativo e exportar Excel, CSV ou JSON; a ação Copiar usa somente os resultados visíveis.

Não há cron ou busca recorrente. Fechar a aba não cancela o worker. Cancelar impede próximas etapas; operação externa atual pode concluir antes de parar.

## 8. Extração institucional e segurança

- E-mails: texto visível, mailto, JSON-LD institucional, marcações [at]/[dot] e data-cfemail publicado; normalização e remoção de placeholders conhecidos. Decodificar texto recebido não autoriza contornar bloqueios.
- Telefones adicionais: tel, campo telephone institucional e texto explicitamente rotulado como telefone/WhatsApp. Remover formatação sem adivinhar país/DDD.
- Redes: links publicados e sameAs de Organization/LocalBusiness e tipos reconhecidos.
- LinkedIn institucional: /company/ ou /school/. /in/ individual é excluído.
- Não navegar nos perfis sociais para identificar pessoas; registrar somente o link publicado.
- Não assumir que qualquer perfil está ativo ou teve sua propriedade verificada externamente.
- URL/e-mail normalizados, contatos duplicados consolidados, HTML externo tratado como dados.
- HTTP público apenas; portas 80/443, sem credenciais embutidas. DNS público validado e conexão fixada no IP aprovado; redirecionamentos também validados.
- Respostas até 1 MB, timeout de rede de 10 s por operação, robots.txt respeitado; 401/403 em robots impedem coleta, 404 permite ausência de regra.
- Playwright em processo separado: limite global de 20 s por renderização, até 20 requests interceptados, somente GET do mesmo domínio via fetcher protegido; sem Service Workers, WebSockets, downloads ou imagens/mídias/fontes. Processo próprio e seus filhos são encerrados no timeout.
- Bibliotecas/CDNs ou APIs de outro domínio e sites que exigem autenticação podem não renderizar. Não contornar a restrição com serviço pago.

Cache considera URL de entrada e opções de extração. Resultados completos do mesmo site são reaproveitados sem revisita; falhas parciais são registradas, sem refazer automaticamente todos os sites bem-sucedidos.

## 9. Modelo de dados e migração

Dados ativos: campanhas schema_version 2, jobs maps/website, empresas, contatos/evidências e scans de site. Evidência contém entidade, campo, valor observado, URL, provedor/método e data.

Tabelas legadas de pessoas/consumo e colunas antigas de score são preservadas para não destruir histórico. Não há módulo Tavily, criação de pessoas, worker pessoal ou endpoint de revisão de pessoas. Campanhas anteriores à separação são consultáveis como histórico e não podem ser aprovadas/retomadas como novas extrações. Uma busca nova usa o novo contrato.

Backup antes da migração: `data/backups/leads-before-extractor-scope-20261102-181444.db`. Não remover banco, volumes ou credenciais antigas por limpeza. Nenhum push, reset ou exclusão de histórico Git é necessário.

### 9.1. Listas e histórico persistente

Cada extração é uma lista identificada por campaign_id, com nome, data de criação, termos/nicho, cidade/localização, parâmetros completos, jobs/status, empresas, contatos/enriquecimento, coordenadas e CSV. O histórico operacional é parte essencial do Google Maps Scraper Kit e não deve ser removido na separação de produtos.

- SQLite permanece a fonte principal, fora dos containers do motor, em data/google-maps-scraper-kit.db no Windows. Não introduzir Supabase para esse histórico.
- campaign_companies mantém o vínculo exclusivo por lista+empresa. Uma empresa pode aparecer em várias listas sem duplicar o catálogo global.
- Ao terminar como completed/partial/failed/cancelled, a visão empresarial de cada lista é gravada em result_json; results_frozen_at indica a preservação concluída. Uma coleta posterior não altera nome, contatos, evidências ou pontos da lista fechada.
- Revisão, seleção e exclusões operacionais permanecem separadas da observação salva e podem mudar conscientemente; CSV reflete a revisão persistida da própria lista.
- Durante execução/interrupção, resultados importados e jobs continuam persistidos; lease/retentativas permitem retomada sem duplicar vínculos. A versão estável é fechada quando a extração chega ao estado terminal.
- Abrir uma lista no histórico usa somente registros salvos para tabela/mapa; não cria consulta, visita de website ou scraping.
- Listas anteriores são preservadas e recebem uma versão dos dados disponíveis no momento da migração. Valores globais já sobrescritos antes dessa implementação não podem ser reconstruídos com precisão; não simular recuperação histórica.
- Lead Intelligence AI terá histórico próprio de ICP, qualificação, enriquecimento comercial e prospecção no Supabase. Não compartilhar banco ou implementar integração direta nesta etapa.

## 10. Exportação e exclusão de listas

Uma linha por company_id. CSV UTF-8 com BOM e cabeçalho estável. Campos: empresa/categoria/endereço, cidade/estado, telefone primário e lista phones, website, e-mails, Instagram/Facebook/LinkedIn institucional, TikTok/YouTube/Twitter/X/WhatsApp, coordenadas/place_id/Maps, revisão/filtros/seleção, URLs de origem, evidence_json, data e schema_version.

Colunas antigas decision_maker_* / fit_score / confidence continuam presentes **vazias**, exclusivamente por compatibilidade de cabeçalho. Nenhum dado pessoal ou score comercial é exportado. Textos que poderiam ser fórmulas de planilha recebem apóstrofo de proteção; os valores observados originais ficam em evidence_json.

O endpoint `/api/v1/campaigns/{id}/export` entrega `csv`, `xlsx` ou `json`. Os três formatos aceitam `company_id` repetido para exportar exatamente o conjunto filtrado ou selecionado na interface; sem IDs, exportam a lista inteira. O CSV é regenerável a partir da lista salva e fica vinculado pelo campaign_id em `data/exports/campaign-{id}.csv`. Escrita temporária + fsync + substituição atômica preservam o arquivo anterior se a gravação falhar. Se o arquivo for removido, o download o regenera sem consultar fontes externas. Revisão inalterada produz conteúdo consistente após reiniciar a aplicação ou recriar o motor.

`DELETE /api/v1/campaigns/{id}` exclui definitivamente uma lista após confirmação explícita na interface. Jobs em execução retornam conflito até serem cancelados e finalizados. A exclusão remove somente vínculos, resultados e arquivos exclusivos da campanha; empresas e evidências compartilhadas com outras listas permanecem preservadas.

Não há integração direta com Supabase, Sheets ou o segundo projeto. O CSV é a fronteira entre produtos, mas não substitui o histórico persistente do Google Maps Scraper Kit.

## 11. Confiabilidade e aceite

- Fila persistente, lease, heartbeat e recuperação; retry limitado/backoff/circuito preservados para falhas de execução.
- Falha de uma página mantém dados válidos de outras. Domínio incompleto termina como parcial e registra causa; não inventar que houve coleta completa.
- Limite de páginas considera tentativas, inclusive falhas; sem loops sobre páginas irrelevantes.
- Testes devem cobrir rejeição de escopo comercial, histórico preservado, HTTP/JS, fontes, LinkedIn institucional, duplicatas, cache/opções, limites/timeouts e CSV único sem pessoas.
- Validar duas listas em datas diferentes para a mesma empresa, preservando dados/pontos próprios; reabrir após reiniciar a API e recriar containers; regenerar CSV apagado; testar falha de escrita, recuperação de lease e integridade dos vínculos.
- Prova real deve registrar campanha, duração, volume, contatos, jobs, falhas parciais e consumo externo. Testes sintéticos não provam cobertura comercial ou volume diário.

Resultados da execução desta versão ficam no [estado do backend](docs/BACKEND_STATUS.md) e [guia de validação](docs/VALIDACAO_WEB.md). Um status parcial por site bloqueado não é sucesso integral: precisa aparecer no relatório.

Estado validado em 03/11/2026: 53 testes automatizados aprovados e smoke test desktop/mobile aprovado, incluindo mapa real, histórico, exclusão e exportações. Isso valida o comportamento exercitado; não garante cobertura comercial integral de toda cidade nem disponibilidade contínua de fontes externas.

## 12. Estado atual e limites da entrega

Backend, histórico SQLite, API, fila, interface web, mapa, revisão, exclusão e exportações estão implementados. A interface foi refinada e validada em desktop e mobile. O uso permanece local e sob demanda; Docker Desktop e o motor Maps precisam estar ativos para novas coletas, mas listas e arquivos persistidos permanecem no computador.

A migração para o motor Python do Kit 2.x foi expressamente adiada. Renderização de mais tipos de site e Firecrawl autônomo dependem de necessidade demonstrada e condições de custo aprovadas. Identificação de decisores e inteligência comercial pertencem exclusivamente ao segundo produto.

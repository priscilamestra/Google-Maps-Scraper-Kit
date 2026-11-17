# Comparação: implementação local e repositório de referência Google Maps Scraper Kit

Data: 2026-11-02. Comparação documental e de código; sem nova extração, migração de motor ou mudança de interface.

**Registro do diagnóstico anterior à implementação.** Profundidade, grade/subdivisão, filtros, aderência ao tema, DNS/MX, JSON e progresso foram implementados depois desta comparação. Estado e provas atuais: [COBERTURA_EXTRACAO.md](../COBERTURA_EXTRACAO.md). A matriz abaixo preserva a situação encontrada, sem reescrever a origem das decisões. Motor Python continua adiado; não afirmar paridade de desempenho entre motores.

## Conclusão

A implementação local do Google Maps Scraper Kit atende ao fluxo básico validado de empresas do Maps → contatos institucionais dos sites → lista persistente → mapa/revisão/CSV, sem API paga obrigatória. Ainda não tem paridade funcional comprovada com o README atual do repositório de referência. A validação do MVP não demonstrou cobertura de cidade inteira, volume diário ou velocidade equivalente ao repositório de referência.

O ponto mais relevante no código atual é a profundidade fixa em 1. Pedir uma meta maior ou aumentar collection_limit não faz, por si só, o motor aprofundar a busca. Também não há grade geográfica automática na API do produto. Não se pode prometer 60 empresas disponíveis e qualificadas só porque o usuário pediu 60.

## Referências e versões

- Kit consultado: [README no commit 15dd80b](https://github.com/Mahanaicoach/google-maps-scraper-kit/blob/15dd80b584dfeab550271ba383b540af8f44d1d2/README.md).
- [Créditos e mudança de motor](https://github.com/Mahanaicoach/google-maps-scraper-kit/blob/15dd80b584dfeab550271ba383b540af8f44d1d2/CREDITS.md).
- [Release v2.2.0](https://github.com/Mahanaicoach/google-maps-scraper-kit/releases/tag/v2.2.0), publicada em 2026-11-01. O benchmark descrito no README foi realizado pelo autor em macOS, com o motor 2.1.0; a release 2.2.0 informa que Windows, Linux e Docker não tiveram teste real nessa release.
- Kit instalado neste projeto: submódulo integrations/google-maps-scraper-kit no commit 4746e8a80baea7ae501d0f4754444b26bdd45085.
- Motor instalado: Gosom v1.18.1 com correção local de encerramento documentada em [engine-close-fix.md](engine-close-fix.md).

A premissa de “mesmo motor” corresponde à versão antiga instalada. O Kit 2.x usa motor próprio Python + Chrome/CDP, sem código Gosom, e oferece execução nativa sem Docker. Compatibilidade de rotas e nomes de colunas anunciada pelo autor não equivale a compatibilidade já testada com nosso adaptador.

## Matriz funcional

“Disponível no Kit” descreve documentação/código remoto; não significa teste local independente. Uma opção existente no script do submódulo não conta como recurso integrado à nossa aplicação.

| Recurso | Repositório de referência | Implementação local | Avaliação |
|---|---|---|---|
| Buscar empresas por termo e cidade | Sim | Sim, com campanhas e fila | Fluxo básico validado localmente |
| Nome, endereço, telefone, site, categoria, coordenadas e link Maps | Sim | Sim, conforme disponibilizados pela fonte | Núcleo atendido; campos ausentes permanecem vazios |
| E-mails e redes institucionais dos sites | Por padrão, com opção de desligar | Opt-in; HTTP e fallback Playwright locais | Atendido com comportamento diferente; não garante contato em todo site |
| Vários termos na mesma lista | Sim | Sim, jobs por termo com união/deduplicação | Atendido |
| Profundidade configurável | --depth | Adaptador fixa --depth 1 | Lacuna importante de cobertura |
| Grade geográfica automática | --grid N, deduplicação por place_id | Cidade e bairros explícitos; sem grade automática | Lacuna importante de cobertura |
| Aprofundar áreas saturadas | --deep-scan, CLI; ainda sem opção na UI do Kit nessa release | Ausente | Lacuna para buscas extensas, distinta da grade simples |
| Centro, raio e pontos no mapa | Busca geográfica e UI do Kit | Centro/coordenadas, raio e marcadores reais com Leaflet/OSM | Atendido por outra implementação; raio é filtro após coleta, não prova cobertura integral |
| Filtros de telefone/e-mail/site | Sim | Campos obrigatórios, inclusive redes; termos negativos e raio | Atendido parcialmente; não são os mesmos filtros |
| Nota mínima, quantidade mínima de avaliações e empresas sem site | Sim | Dados podem ser coletados, mas sem esses filtros no contrato da campanha | Ausente na API do produto |
| Ignorar empresas de listas anteriores | --exclude CSV/JSON | Deduplicação dentro da lista; listas independentes podem repetir empresas | Recurso adicional ausente; repetir em listas diferentes pode ser desejado |
| Verificação de domínio de e-mail por MX | --verify-emails, DNS sem chave | Limpeza sintática/placeholder; sem MX | Ausente; mesmo MX não comprova que a caixa recebe mensagens |
| Navegador para sites dependentes de JavaScript | --browser-sites opcional | Playwright local com limites, robots e isolamento de rede | Disponível; cobertura e desempenho não comparados em mesmas condições |
| Múltiplas cidades em uma execução | Sim, CLI | Uma cidade por campanha, com bairros opcionais | Ausente na campanha única; possível criar listas separadas |
| Proxies configuráveis | Sim, opcionais | Não expostos no contrato/API do produto | Ausente; não é dependência obrigatória nem recomendação de assinatura |
| Idioma e país de busca | Idioma e país configuráveis | pt/en/es; país da localização não vira automaticamente parâmetro de ranking | Parcial |
| Campos detalhados de reviews, horários e fotos | Opções específicas, incluindo --reviews/--full | Preserva material bruto do motor; resultado/CSV do produto expõe subconjunto institucional | Sem equivalência do contrato de saída completo |
| Exportar CSV | Sim | Sim, regenerado da lista salva, com evidências | Atendido |
| Exportar arquivo JSON | Sim | Resultados JSON pela API, sem exportação dedicada de arquivo JSON | Parcial |
| Persistência de execuções | Jobs/resultados persistentes no motor | SQLite com listas independentes, estados, revisão e snapshots de listas finalizadas | Ambos persistem; nossos vínculos e versões de listas foram testados |
| Recuperação de interrupções | Motor anuncia pausa/retomada | Lease, heartbeat, tentativas limitadas, estados parciais e correção local de encerramento | Disponível; retomada exata do mesmo ponto não deve ser presumida |
| Progresso detalhado dentro da busca | Motor/UI com informações de progresso | Estados dos jobs/etapas | Parcial |
| API local e uso por agente | Sim, comandos/skills próprios | API compartilhada por painel, CLI e skill Codex | Atendido com integração própria |
| Tecnologias, pixels e intent_score | --tech | Fora do escopo comercial aprovado | Não incorporar automaticamente; scoring pertence ao Lead Intelligence AI |
| Executar sem Docker | Sim, motor Python atual | Motor atual depende do Docker | Diferença operacional importante no Windows |
| Sem assinatura/API paga obrigatória | Sim | Sim, Maps + HTTP/Playwright locais | Atendido; Firecrawl MCP não é dependência autônoma do backend |

A interface não foi redesenhada nesta comparação. O uso de OSM/Leaflet com coordenadas coletadas do Maps não significa usar a API cartográfica do Google nem reproduzir seu mapa visual.

## Evidência local disponível

Código relevante:
- src/google_maps_scraper_kit/maps.py: _command fixa profundidade, chama o Kit com --no-email e recebe --full/--limit. O enriquecimento ocorre depois no backend.
- src/google_maps_scraper_kit/models.py: contrato de campanha, termos, cidade/bairros, meta, limite e filtros.
- src/google_maps_scraper_kit/website.py, rendering.py e contacts.py: coleta institucional HTTP/Playwright, normalização e evidências.
- src/google_maps_scraper_kit/db.py: fila, histórico e snapshots por lista finalizada.
- src/google_maps_scraper_kit/export.py: CSV e escrita atômica.
- docs/BACKEND_STATUS.md e docs/PERSISTENCIA_LISTAS.md: contratos operacionais.

Relatórios locais existentes, relidos nesta comparação:
- data/live-validation/extractor-refactor-report.json: teste real com 8 sites, 5 completos, 3 inacessíveis e 10 páginas; 34 testes do produto e 60 do Kit naquele momento.
- data/live-validation/list-history-report.json: suíte posteriormente ampliada para 39 testes; histórico no navegador aprovado; reinício real da API e recriação real do container preservaram resultados e CSV; 12 listas preservadas, integridade e chaves estrangeiras válidas.

Esses testes são evidência do que foi validado anteriormente, não uma nova execução nesta auditoria. Não houve benchmark comparável no mesmo hardware, consulta, profundidade e opções entre os dois motores. Os números de velocidade e volume do README são resultados do autor, não previsões para este computador. Não há base para prometer extração ilimitada, quantidade diária fixa ou 100% de e-mails.

## Próximas decisões para aproximar a cobertura

1. Prioridade: definir profundidade configurável e busca por grade com orçamento explícito de consultas, deduplicação, cancelamento e respeito aos bloqueios. Manter a distinção entre meta solicitada, coletado e qualificado; esgotamento legítimo termina com quantidade real.
2. Avaliar o motor 2.2.0 isoladamente antes de investir no adaptador antigo. Testar no Windows, comparar consultas idênticas e validar rotas, colunas, interrupção, dados persistidos e enriquecimento. Só então decidir manter Gosom ou migrar. A instalação atual não foi alterada.
3. Completar filtros de nota/avaliações/ausência de site, exclusão opcional de listas anteriores e MX, se a meta for equivalência desses recursos.
4. Tratar multicidade, JSON, reviews detalhados, idiomas e proxies como diferenças adicionais explícitas; não confundir com requisitos já cumpridos.
5. Manter qualificação comercial/ICP no Lead Intelligence AI. Não adicionar intent_score aqui só para copiar o README.

Não executar atualização automática do submódulo/Compose: o Kit novo altera motor e volumes. Nosso overlay e a correção Gosom exigem revisão antes de qualquer migração, preservando SQLite, arquivos, redes, portas e serviços existentes.

# Mapa da interface: pesquisa e contrato proposto

Verificado e implementado em 01/11/2026. Decisão da usuária: nenhuma API paga, assinatura ou faturamento Google. Leaflet + OpenStreetMap no painel local, sem chave Google. Detalhes operacionais e testes em `docs/BACKEND_STATUS.md`.

## Dependência visual e licença

Leaflet 1.9.4, sem modificações, carregado do pacote oficial via `https://unpkg.com/leaflet@1.9.4/dist/`. Fonte/tag: https://github.com/Leaflet/Leaflet/tree/v1.9.4 . Licença BSD-2-Clause, © Volodymyr Agafonkin e CloudMade: https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE . O aplicativo não redistribui cópia modificada. Atribuição Leaflet e © OpenStreetMap contributors visíveis no mapa. Configuração Nominatim deliberada para este aplicativo local, somente consultas manuais de centro: cache persistente, requisições serializadas, máximo uma por 1,1 s, sem autocomplete, sem varredura em lote e sem dados confidenciais. Serviço configurável por `GOOGLE_MAPS_SCRAPER_KIT_GEOCODER_URL`; política: https://operations.osmfoundation.org/policies/nominatim/ .

## Evidência do motor instalado

Verificados diretamente os templates oficiais de `gosom/google-maps-scraper` na tag `v1.18.1`:

- `web/static/templates/job_view.html` carrega Leaflet 1.9.4, cria `L.map('job-map')`, usa tiles OpenStreetMap com atribuição e adiciona `L.marker([place.latitude, place.longitude])` para cada empresa.
- `web/static/templates/index.html` contém entrada numérica `Radius (BETA)`. Isso comprova a existência do parâmetro na interface original, não a implementação de um círculo arrastável nem de recorte geográfico estrito.
- O README do Kit instalado, seção de limites, confirma que a interface é servida pelo motor e carrega mapas OpenStreetMap. Os templates não estão no checkout do Kit porque são distribuídos na imagem oficial do motor.

Código-fonte verificado:

- https://github.com/gosom/google-maps-scraper/blob/v1.18.1/web/static/templates/job_view.html
- https://github.com/gosom/google-maps-scraper/blob/v1.18.1/web/static/templates/index.html

Assim, empresas descobertas no Google Maps podem aparecer em suas coordenadas reais sobre mapa OpenStreetMap. Não é necessário usar mapa-base do Google para representar os resultados do Google. O código exato do painel personalizado do vídeo ainda não foi obtido; não afirmar que sua implementação é idêntica.

Para o painel local, usar Leaflet para centro/raio e marcadores, com filtro de distância no backend descrito abaixo. Leaflet suporta círculos em metros e mudança de raio por `setRadius`; o seletor de raio sincroniza esse valor com a campanha. Os tiles públicos OSM não exigem chave/faturamento para uso compatível com sua política: manter atribuição visível, cache/Referer normais do navegador, sem download em massa ou prefetch. Serviço é best-effort, sem garantia de disponibilidade ilimitada.

- https://leafletjs.com/reference.html#circle
- https://operations.osmfoundation.org/policies/tiles/

## Serviços distintos

- A Maps Embed API oferece iframe gratuito com uso ilimitado, chave e configuração Google Cloud. Seus modos são place, view, directions, streetview e search. Não é o contrato adequado para desenhar o círculo editável e gerenciar os marcadores da lista de leads da aplicação. Uma busca exibida no iframe não equivale aos resultados persistidos da campanha.
- A Maps JavaScript API permite controlar o mapa e suas sobreposições. O SKU Dynamic Maps oferece atualmente 10.000 eventos mensais gratuitos; depois, a primeira faixa custa US$ 7 por 1.000 eventos. Requer faturamento habilitado. Serviços Places e geocoding têm cobrança própria e não fazem parte desta proposta.
- Maps Demo Key serve apenas para testes/protótipos, sem faturamento; não resolve o uso operacional contínuo do produto.
- A imagem de referência não comprova qual biblioteca/provedor foi usado. O checkout instalado do Kit não contém o painel customizado daquela imagem.

Fontes oficiais:

- https://developers.google.com/maps/documentation/embed/embedding-map
- https://developers.google.com/maps/documentation/embed/quickstart
- https://developers.google.com/maps/documentation/javascript/get-api-key
- https://developers.google.com/maps/documentation/javascript/usage-and-billing
- https://developers.google.com/maps/billing-and-pricing/pricing

## Dados reais e raio

O wrapper em `src/google_maps_scraper_kit/maps.py` chama o Kit com `--full` e preserva as colunas originais em `maps_results.raw_json`, incluindo latitude/longitude quando fornecidas pela fonte. A normalização/API expõe essas coordenadas como campos próprios. Não inventar posições nem substituir a coordenada da empresa pelo centro da cidade.

O CLI instalado aceita centro latitude/longitude e grade. O próprio Kit registra em `scripts/scrape.py`, no processamento da grade, que radius não recorta resultados no modo normal. O wrapper usa centro explícito quando há `search_area`, mantendo `--city` para campanhas antigas. O raio solicitado (até 10 km) é aplicado na qualificação após cada importação pelo cálculo Haversine, sem modificar o motor. A coleta do Kit ainda usa limite por termo antes desse filtro: meta e cobertura completa não são garantidas.

Contrato implementado com Leaflet/OpenStreetMap, mantendo API/SQLite/fila compartilhados:

1. Campanha recebe centro explícito (latitude/longitude válidas e rastreáveis) e raio em metros, além dos filtros existentes. O painel envia o centro selecionado; a conversa precisa de centro comprovado quando houver raio.
2. Persistir centro/raio na configuração da campanha e repassar o centro ao adaptador, sem alterar terceiros. Não substituir coleta pela Places API.
3. Validar e normalizar coordenadas reais, `maps_url` e `place_id`, preservando dados brutos e bancos existentes. Disponibilizar no resultado da API e na exportação.
4. Calcular distância geodésica no backend e aplicar o limite real após a coleta. Preservar linhas fora da região com motivo de exclusão. Sem coordenadas, manter o registro como localização não verificável, sem marcador fictício e sem afirmar que atende ao raio.
5. O painel Leaflet/OpenStreetMap desenha círculo e marcadores usando esses mesmos dados; mover centro/raio altera a solicitação, não apenas o desenho. Separar resultados coletados, qualificados e selecionados.
6. Testes sintéticos: coordenadas inválidas/ausentes, distância zero, fronteira do círculo, resultado externo, persistência de configurações e compatibilidade do banco antigo. Teste real posterior depende do Docker/hipervisor.

Não criar dependência Google cobrada nem habilitar faturamento. Validados 30 testes de backend e um teste de navegador com mapa externo real, empresas sintéticas, interação de raio, persistência e revisão. Nova coleta real via Docker continua pendente. Nenhum código do Kit/motor foi alterado.

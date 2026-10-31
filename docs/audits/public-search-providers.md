# Auditoria de provedores de busca pública

**Data:** 31/10/2026
**Status:** Tavily validada em benchmark interno e operacional como adapter gratuito limitado; demais propostas permanecem retiradas.
**Objetivo:** localizar URLs públicas/indexadas de empresas e profissionais sem acessar conta LinkedIn e sem raspar páginas de resultados de buscadores.

## Critérios

- API oficial com resposta estruturada;
- nenhuma sessão ou cookie do LinkedIn;
- nenhuma burla de CAPTCHA ou bloqueio;
- custo inicial coberto por franquia gratuita;
- permissão explícita para persistir URL, título e trecho como evidência;
- chave somente em `.env`, nunca no Git.

## Resultado

| Provedor | Decisão | Motivo |
|---|---|---|
| Google Custom Search JSON API | NO-GO | indisponível para novos clientes e descontinuação anunciada para 01/01/2027 |
| Bing Search API | NO-GO | produto aposentado; não é uma base sustentável para nova integração |
| scraping de páginas Google/Bing/DuckDuckGo | NO-GO | instável e contrário ao requisito de usar uma fonte autorizada |
| Brave Search API | NO-GO para este projeto | cadastro e modelo de cobrança/créditos externos não atendem à restrição de custo confirmada pelo usuário |
| Tavily Search API | GO condicionado | plano gratuito recorrente de 1.000 créditos/mês, uso oficial em pesquisa de contas e ICP; exige teto de consumo, verificação humana e proíbe disparo de spam |

## Tavily Search API

Documentação oficial verificada em 31/10/2026:

- preços: <https://www.tavily.com/pricing>
- busca e consumo: <https://docs.tavily.com/documentation/api-reference/endpoint/search>
- termos: <https://www.tavily.com/terms>
- política de uso: <https://www.tavily.com/acceptable-use-policy>
- uso oficial em Sales/GTM: <https://www.tavily.com/blog/how-tavily-uses-tavily-in-sales-finding-and-reaching-customers>

Condições aplicáveis ao projeto:

- plano gratuito com 1.000 créditos renovados mensalmente e sem cartão obrigatório;
- busca `basic` custa 1 crédito e retorna no máximo 20 resultados;
- uso restrito à pesquisa interna, enriquecimento, ICP e preparação para revisão humana;
- não enviar mensagens, automatizar cadência ou facilitar spam pela Tavily;
- persistir somente URL, título, trecho, consulta, data e evidência necessários ao uso interno;
- verificar pessoa, cargo e vínculo antes de aprovar um lead;
- interromper ao esgotar a cota; nunca contratar plano ou habilitar cobrança automaticamente.

A Tavily publica seu próprio uso em pesquisa de contas, ICP, identificação de pessoas relevantes e preparação de abordagem. A política proíbe comunicações comerciais não solicitadas e spam, não a pesquisa interna com revisão humana. Como o MVP não envia mensagens, o caso é `GO condicionado`. O adapter foi validado em execução real em 31/10/2026; o ambiente registrou 25 créditos acumulados e manteve revisão humana dos vínculos.

## Brave Search API

Documentação oficial:

- produto e uso: <https://brave.com/search/api/>
- preços: <https://api-dashboard.search.brave.com/documentation/pricing>
- início rápido: <https://api-dashboard.search.brave.com/app/documentation/web-search/get-started>

Condições públicas verificadas:

- endpoint `https://api.search.brave.com/res/v1/web/search`;
- autenticação pelo header `X-Subscription-Token`;
- plano Search a US$ 5 por 1.000 requisições;
- US$ 5 em créditos aplicados mensalmente;
- capacidade publicada de 50 requisições por segundo, embora o projeto permaneça em concorrência 1;
- resultados com URL, título e trecho;
- a Brave informa que armazenar resultados exige um plano que inclua `storage rights`.

O crédito publicado cobriria até 1.000 consultas mensais no preço atual, mas isso não é garantia de gratuidade permanente. O sistema deverá medir consultas e interromper antes de consumo pago não aprovado.

## Proposta de ativação retirada

O roteiro abaixo fazia parte da proposta retirada e não deve ser executado:

1. criar ou acessar uma conta Brave Search API;
2. verificar no painel/termos se o plano selecionado permite armazenar URLs, títulos e snippets;
3. confirmar se o cadastro exige meio de pagamento e se existe cobrança automática além do crédito;
4. criar a chave e salvá-la localmente como `BRAVE_SEARCH_API_KEY`, sem enviá-la no chat ou versioná-la;
5. executar uma prova de no máximo 20 consultas e registrar cobertura/custo.

Não é necessária ação do usuário na Brave. A chave Tavily permanece somente no `.env`, e sites oficiais continuam sendo a primeira fonte. Tavily complementa apenas os campos e URLs que eles não publicarem.

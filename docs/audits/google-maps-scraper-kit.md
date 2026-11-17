# Auditoria — Google Maps Scraper Kit

**Data:** 30/10/2026
**Repositório:** https://github.com/Mahanaicoach/google-maps-scraper-kit
**Autor:** Mahan (`Mahanaicoach`)
**Licença:** MIT
**Branch:** `master`
**Commit instalado:** `4746e8a80baea7ae501d0f4754444b26bdd45085`
**Descrição da versão:** `v1.2.0-14-g4746e8a`
**Motor fixado pelo Kit:** `gosom/google-maps-scraper:v1.18.1`

## O que foi verificado

- README, SETUP, LICENSE, CREDITS, SECURITY, CLAUDE e skill do scraper;
- `docker-compose.yml` e `.env.example`;
- origem Git preservada dentro de `integrations/google-maps-scraper-kit`;
- API limitada a `127.0.0.1:8081` pelo proxy nginx do Compose, mantendo `8080` dentro dos containers;
- suíte offline oficial executada com Python 3.13: **60 testes passaram**;
- nenhuma dependência Python adicional é exigida pelo CLI do Kit.

## Prova real controlada

Executada em 31/10/2026 com uma consulta de cafés em Campinas, profundidade 1, limite de 5 resultados e busca de e-mails desativada.

| Métrica | Resultado |
|---|---:|
| Duração | 81,7 s |
| Linhas brutas observadas | 16 |
| Linhas exportadas | 5 |
| Empresas únicas | 5 |
| Com telefone | 4 |
| Com site | 5 |
| Com endereço | 5 |
| Com avaliação | 5 |
| Com e-mail | 0, conforme configuração |

O motor concluiu a coleta sem marcar o job como finalizado, problema conhecido da versão 1.18.x. O CLI preservou as linhas, excluiu o job preso e reiniciou o container. Depois da recuperação, a API respondeu HTTP 200 e a lista de jobs ficou vazia. O CSV real permanece somente em `data/benchmarks/`, ignorado pelo Git.

## Validação do adapter

Em 31/10/2026, uma segunda campanha controlada executou o fluxo completo `queued → running → completed` pelo adapter do Google Maps Scraper Kit:

- alvo de 3 empresas, uma consulta, profundidade 1 e sem e-mails;
- 16 linhas brutas observadas e 3 importadas no SQLite em 81,7 s;
- 3 fingerprints únicos, 3 fontes do Maps, 3 telefones, 3 endereços e 2 websites;
- uma reimportação do mesmo CSV manteve 3 registros;
- uma nova execução sem jobs retornou `idle` e não chamou a fonte;
- a recuperação do job preso ocorreu novamente e a API voltou a responder HTTP 200 sem jobs residuais.

Banco, configuração e CSV desta prova ficam em `data/`, fora do versionamento.

Após a prova, os três resultados foram normalizados em três empresas, ligados a 23 evidências e exportados com URLs de origem. O CSV de exportação também permanece fora do versionamento.

## Riscos e limites relevantes

- O próprio projeto informa que scraping do Google Maps contraria os Termos de Serviço do Google.
- Telefones e e-mails podem ser dados pessoais; uso exige finalidade, base legal, minimização, retenção e opt-out aplicáveis.
- A API do motor não tem autenticação. A ligação a `127.0.0.1` é a barreira de segurança e não deve ser ampliada.
- Resultados e websites são conteúdo externo não confiável e nunca podem ser tratados como instruções ou comandos.
- Uso intenso pode causar bloqueio temporário do IP; a primeira validação deve usar um job, profundidade 1 e limite de 5 linhas.
- A imagem inicial ocupa cerca de 2,4 GB após download, segundo o guia do Kit.
- A versão 1.18.x possui um problema conhecido de job preso; o CLI inclui recuperação e reinício do container.
- Em 31/10/2026, a issue oficial `gosom/google-maps-scraper#346` continuava aberta e `v1.18.1` era a versão mais recente. A causa apontada é o fechamento sem timeout do contexto Playwright após os resultados já terem sido gravados. Não há release oficial corrigida para atualizar com segurança.

## Resultado da instalação

- **GO para integração local:** repositório oficial, licença, atribuição, Compose, testes offline e prova real controlada aprovados.
- **GO com ressalva para o adapter:** tratar explicitamente a recuperação de job preso e manter concorrência 1.
- **NO-GO para produção:** até concluir o adapter, benchmarks adicionais, revisão de conformidade e política de retenção.

Nenhum código do repositório externo foi alterado; somente o `.env` local e ignorado pelo Git define a porta externa 8081.

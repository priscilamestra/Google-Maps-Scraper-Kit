# Auditoria da fonte de decisores

**Data:** 31/10/2026
**Decisão:** não instalar um scraper de LinkedIn; implementar descoberta pública no núcleo do projeto.

## Requisitos eliminatórios

- não usar login, cookie ou sessão de uma conta LinkedIn;
- não contornar CAPTCHA, bloqueio ou controle de acesso;
- não tornar uma assinatura paga obrigatória;
- preservar URL, data e valor observado como evidência;
- manter campos ausentes como ausentes.

## Evidência oficial

A documentação oficial de acesso às APIs do LinkedIn oferece, por autosserviço, login OpenID do próprio membro e publicação. Produtos de Sales, Marketing e Talent dependem de aprovação. Ela não oferece busca geral de pessoas ou leitura arbitrária de perfis como API pública:

- <https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access>

O PRD também exclui scraping autenticado, burla de acesso e dependência paga obrigatória. Portanto, a ausência de uma API pública adequada não autoriza substituir o requisito por automação de conta.

## Candidatos avaliados

| Opção | Resultado | Motivo |
|---|---|---|
| `joeyism/linkedin_scraper` | NO-GO | automação de páginas do LinkedIn; cobertura útil depende de sessão/login e fica sujeita ao authwall |
| `stickerdaniel/linkedin-mcp-server` | NO-GO | exige login/cookies e o próprio projeto alerta para restrição de conta e possível conflito com os termos |
| `pratik-dani/linkedin-scraper` | NO-GO | exige uma conta LinkedIn válida e declara que não é suportado pelo LinkedIn |
| APIs terceiras de scraping/enriquecimento | opcional futuro | removem a conta local, mas introduzem fornecedor, créditos e custo; não são requisito do MVP |
| site oficial da empresa | GO | fonte pública, ligada à empresa e capaz de publicar equipe, cargo e links profissionais com evidência direta |
| Tavily Search API | GO condicionado | busca pública indexada com cota gratuita recorrente; somente pesquisa interna, revisão humana e sem outreach automático |

Referências dos candidatos:

- <https://github.com/joeyism/linkedin_scraper>
- <https://github.com/stickerdaniel/linkedin-mcp-server>
- <https://github.com/pratik-dani/linkedin-scraper>

## Arquitetura aprovada

O projeto usará o `Website Adapter` como primeira fonte e Tavily como complemento limitado:

1. receber empresas e sites já encontrados pelo Maps;
2. visitar apenas páginas públicas do mesmo site, começando por contato, sobre e equipe;
3. extrair nomes, cargos e links profissionais publicados pela própria empresa;
4. aceitar uma pessoa somente quando o cargo corresponder aos papéis solicitados;
5. persistir pessoa, relação com a empresa e evidência da página onde o dado foi observado;
6. encerrar com resultado parcial quando o site não publicar um decisor.
7. consultar Tavily somente para empresas sem evidência suficiente, respeitando o teto mensal;
8. aceitar a URL indexada como candidata e validar nome, cargo e vínculo antes de aprovar a pessoa.

LinkedIn será um campo de evidência quando sua URL estiver publicada no site ou localizada pela busca indexada da Tavily. Nenhum adapter abrirá ou raspará o perfil do LinkedIn, e o sistema não enviará mensagens.

## Limite conhecido

Essa escolha privilegia segurança, custo e rastreabilidade, mas terá cobertura menor que uma base comercial. A Tavily opera com teto local de 1.000 créditos gratuitos por mês, parada obrigatória ao esgotar a cota e nenhuma contratação automática.

## Prova controlada de 31/10/2026

O núcleo HTTP e os testes com fixtures sintéticas foram implementados. Uma campanha explícita de `cafe em Campinas, SP, BR` produziu:

- 5 empresas únicas e 38 evidências do Maps;
- 4 empresas com website;
- 3 domínios processados, 4 páginas HTML e 28 novas evidências de website;
- 1 domínio recusado por certificado TLS inválido;
- 0 decisores publicados em JSON-LD ou Microdata compatível com os cargos testados.

O resultado valida o transporte, os limites, o cache e a ausência de invenção, mas não valida cobertura de decisores. A amostra de cafeterias não publicou equipe em formato estruturado; por isso, o modo híbrido ainda não deveria ser apresentado como funcional ao usuário final naquele ponto.

Uma segunda campanha controlada, `empresa de software em Campinas, SP, BR`, validou o fluxo enfileirado Maps → websites:

- 5 empresas e 39 evidências do Maps;
- 4 domínios processados, 10 páginas e 37 evidências de website;
- 1 domínio indisponível com HTTP 503;
- 0 decisores publicados em JSON-LD/Microdata para os cargos solicitados;
- estado final `partial`, preservando as 4 coletas bem-sucedidas;
- CSV corrigido e validado com somente as 5 empresas da campanha.

A página pública `quem somos` alcançada no teste continha nomes de clientes em depoimentos, não membros da empresa. Eles foram corretamente rejeitados como decisores.

O benchmark posterior de Nova Friburgo coletou 25 empresas em duas campanhas e encontrou 16 perfis candidatos relacionados a 8 empresas usando Tavily. O resultado comprovou a integração, mas também mostrou que marcas, franquias e nomes genéricos podem apontar para profissionais fora da unidade local. Por isso, o backend classifica pessoas como `verified`, `likely` ou `candidate` e mantém revisão humana obrigatória.

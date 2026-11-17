# Motor: encerramento bloqueado e correção local

Decisão em 02/11/2026: a usuária autorizou investigar e corrigir o erro do motor. O Kit permanece intacto. Uma imagem derivada identificada substitui apenas o binário do motor por uma compilação com correção revisada; Compose usa overlay, preservando rede, portas e volume.

## Evidências e fontes

- Log local da coleta `21be98dd-0195-4a4b-9b34-11ab47a754f1`: resultados escritos, `scrapemate exited`, status permaneceu `working`; o Kit precisou recuperar dados e reiniciar o motor.
- Relato oficial compatível: https://github.com/gosom/google-maps-scraper/issues/346 . É uma correspondência de sintoma/código; não foi capturado um dump da execução anterior.
- No código da biblioteca, `browser.Close` espera `BrowserContext.Close` e `Browser.Close` sem timeout; `jsFetch.Close` também espera `pw.Stop`. O retorno bloqueado impede atualizar o status final e liberar o próximo trabalho.
- Correção proposta, ainda não incorporada pelo autor: https://github.com/gosom/scrapemate/pull/31 , contribuição de cattydev, commit `436425d8f71ee3426ef789b24917e1b8487b8db7`.

## Proveniência e adaptação

- Motor oficial: `gosom/google-maps-scraper`, tag v1.18.1, commit `549e4b5e61c7103685ef8392f246ebdba783ed03`, MIT, Georgios Komninos.
- Biblioteca oficial: `gosom/scrapemate`, base `859d15f56ba5ed3851587edc305fab6ee956cc71`, MIT, Georgios Komninos.
- Patch rastreável em `infrastructure/engine/browser-close.patch`, extraído do diff da PR 31 pela referência oficial `refs/pull/31/head`.
- Timeout de 5 s para context/browser e 30 s para parar o driver; tenta fechar o navegador mesmo se o contexto travar. Reaproveita o helper de timeout já existente. Não muda coleta, filtros, login ou acesso às fontes.
- Imagem derivada: `google-maps-scraper-kit/maps-engine:v1.18.1-closefix1`; runtime original fixado pelo digest `sha256:e205c02913c5a69c16fc2094b8e5b194a2f655166d2c1126d50548d216e6b1b2`. Licenças incluídas.
- Clones de inspeção em `integrations/google-maps-scraper` e `integrations/scrapemate`, ignorados pelo Git do produto; fontes originais e licenças preservadas. O Dockerfile refaz a obtenção e verifica commits, sem depender de edições nesses clones.
- Overlay reduz concorrência do motor de 4 para 1, seguindo a regra local para fontes sensíveis. Não é a correção do travamento; os testes simulam explicitamente fechamento bloqueado.

## Build e instalação

Na raiz, com Docker Desktop aberto:

```powershell
docker compose -f integrations/google-maps-scraper-kit/docker-compose.yml -f infrastructure/engine/compose.yaml build google-maps-scraper
docker compose -f integrations/google-maps-scraper-kit/docker-compose.yml -f infrastructure/engine/compose.yaml up -d --no-deps google-maps-scraper
```

O build executa regressões que simulam contexto travado e testes HTTP do motor antes de gerar a imagem. Instalar somente com a fila vazia; nunca executar dois motores sobre o mesmo volume.

Rollback preservando dados: com fila vazia, executar `docker compose -f integrations/google-maps-scraper-kit/docker-compose.yml up -d --no-deps google-maps-scraper`. Não usar `down -v` ou limpeza de volumes.

## Validação

Build e instalação concluídos em 02/11/2026. O build roda os mesmos testes contra o código original e exige reproduzir as duas falhas: `browser.Close blocked on a hung context close` e `pageSlotPool.close blocked on a hung context close`. O símbolo de timeout adicionado apenas ao teste de baseline permite compilar o mesmo teste, sem alterar o comportamento de produção anterior. Contra a versão corrigida, ambos passam com `-race`. Testes de `./web/...` também passaram antes de compilar o binário.

Imagem instalada: `google-maps-scraper-kit/maps-engine:v1.18.1-closefix1`, manifesto local `sha256:e60ca71726c78725ce349a3567eb9e211541c85ee5b8a610c36938ccae03cea7`. O volume continua `google-maps-scraper-kit_gmaps_data`, montado em `/gmapsdata`; Apache, PostgreSQL, UI, redes e portas foram preservados.

Prova real: campanha `afea6654-6928-4daf-a466-e23fe0561c4b`, três consultas consecutivas (agência de marketing, publicidade, curso de inglês em Nova Friburgo), limite de saída 6, meta 3. As três completaram na primeira tentativa; 6 empresas únicas, 3 selecionadas, cerca de 147 s. Estados nativos do motor confirmados `ok` para `e11a2996-29a8-47d7-bb56-145eec311c44`, `afa2ad50-b928-461b-abea-d76cb65f5b46` e `00500818-2c67-4255-ba5f-e70f87c954e3`.

O início do container permaneceu `2026-11-02T20:05:48.891059173Z` antes/depois; nenhum reinício nem recuperação do Kit nessa prova. Interface, pontos, CSV e revisão passaram com esses dados reais. Regressão do produto: 34 testes, 60 testes oficiais do Kit e smoke desktop/mobile passaram. Relatórios/capturas locais em `data/live-validation/`, ignorados pelo Git.

Um teste pequeno sem travar não prova ausência de toda falha futura; as regressões forçadas cobrem especificamente esse defeito de encerramento. O patch é local, proveniente de PR ainda aberta, e deve ser reavaliado ao atualizar a dependência. A recuperação do Kit permanece como proteção adicional.

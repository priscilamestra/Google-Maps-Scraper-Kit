# Contrato do backend para o painel

## Estado operacional

O backend executa campanhas somente após uma ação explícita. Não existe cron ou pesquisa recorrente.

Modos disponíveis:

- `companies`: Google Maps, sites, contatos e redes publicados;
- `people`: perfis profissionais públicos indexados, sem login ou scraping autenticado;
- `companies_and_people`: empresas primeiro, depois sites e candidatos a decisores.

Cada termo recebe sua própria parcela da meta. Resultados são deduplicados, qualificados e mantidos no SQLite. CSV é somente uma saída para download; planilhas não fazem parte do fluxo obrigatório.

## API

Iniciar:

```powershell
uv run leads-prospect serve-api --host 127.0.0.1 --port 8090
```

Rotas para o frontend:

```text
GET    /api/v1/health
GET    /api/v1/campaigns
POST   /api/v1/campaigns
GET    /api/v1/campaigns/{campaign_id}
POST   /api/v1/campaigns/{campaign_id}/approve
POST   /api/v1/campaigns/{campaign_id}/cancel
POST   /api/v1/jobs/{job_id}/pause
POST   /api/v1/jobs/{job_id}/resume
GET    /api/v1/campaigns/{campaign_id}/results
PATCH  /api/v1/campaigns/{campaign_id}/results/{company_id}
PATCH  /api/v1/people/{person_id}
GET    /api/v1/campaigns/{campaign_id}/export
GET    /api/v1/usage
GET    /api/v1/sources/health
```

O POST de campanha recebe o mesmo JSON dos arquivos em `config/`. Empresas e pessoas podem ser revisadas como `pending`, `approved` ou `rejected`.

## Execução e estados

O worker iniciado com a API processa um job por vez. Campanhas e jobs sobrevivem à reinicialização do computador. Há lease, retentativas, backoff, circuit breaker e cache por domínio.

Cancelar uma campanha interrompe as próximas etapas. Se um job já estiver dentro de uma requisição externa, ele conclui a etapa atual antes de parar; isso evita corrupção de dados.

## Qualidade

- empresas recebem score de completude e confiança;
- termos negativos e campos obrigatórios podem excluir resultados;
- e-mails de exemplo conhecidos são removidos;
- pessoas de sites oficiais ficam como `verified`;
- resultados indexados com localidade compatível ficam como `likely`;
- demais coincidências ficam como `candidate` e exigem revisão.

Número exato de funcionários não é inferido. O campo só poderá ser filtrado quando houver uma fonte pública que o comprove.

## Limites de segurança

- API do produto: `127.0.0.1:8090`;
- API interna do Maps: `127.0.0.1:8081`;
- nenhuma mensagem ou convite é enviado;
- nenhuma conta autenticada do LinkedIn é automatizada;
- dados e credenciais locais permanecem ignorados pelo Git.

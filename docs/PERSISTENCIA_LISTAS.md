# Persistência de listas — Google Maps Scraper Kit

Implementado e validado em 02/11/2026. Requisito: preservar listas, mapa e CSV como histórico operacional do extrator. Sem Supabase e sem redesenho da interface.

## Auditoria antes da alteração

SQLite já persistia campaign_id, nome/data, modo, termos/localização/parâmetros completos, jobs/estado/erros, empresas, evidências e vínculo campaign_companies. A interface já consultava o histórico e as coordenadas sem executar nova busca. A fila já tinha lease, recuperação, retry e chaves para deduplicação.

Lacuna encontrada: os campos da empresa e seus contatos vinham do catálogo/evidências globais. Uma nova coleta da mesma empresa podia mudar a visão e o CSV da lista antiga. O download também dependia do diretório de trabalho e escrevia diretamente sobre o arquivo final.

## O que mudou

- Migração aditiva: campaign_companies.result_json guarda a visão empresarial da lista; campaigns.results_frozen_at marca sua conclusão. Sem apagar tabelas, registros ou histórico Git.
- Ao terminar a extração (inclusive parcial, falha final ou cancelamento seguro), os resultados disponíveis são preservados uma vez. Revisão/seleção continuam nas colunas próprias do vínculo por lista.
- Abrir a lista usa seus dados salvos e suas coordenadas; mudanças posteriores no catálogo não substituem os dados observados da lista fechada.
- Filtros e seleção usam os resultados da própria lista, inclusive depois de outra pesquisa da mesma empresa.
- Uma interrupção recuperável mantém jobs, bruto e empresas já importados; retomada usa a mesma campanha e não duplica o vínculo. Ao esgotar tentativas, a lista parcial é preservada antes de processar outra campanha.
- CSV regenerável pelo endpoint existente, sem consultar Maps/sites. Arquivo por campaign_id em exports ao lado do SQLite: na instalação atual, data/exports/campaign-{id}.csv.
- Escrita em temporário no mesmo diretório, flush/fsync e troca atômica; falha de gravação preserva a versão anterior. Apagar um CSV não apaga a lista: o download regenera o arquivo.
- Nenhum componente visual novo: histórico, tabela, mapa e botão de CSV continuam usando as mesmas rotas.

O CSV não precisa de uma tabela de arquivos ou de um segundo banco: sua identidade vem de campaign_id e seu conteúdo é derivado dos registros persistidos da lista. Revisão alterada conscientemente gera um CSV atualizado; revisão inalterada mantém conteúdo consistente.

## Migração e limite histórico

Backup anterior: data/backups/leads-before-list-history-20261102-185640.db. As 12 campanhas existentes receberam a visão empresarial disponível no banco durante a migração. Não foi feita nova extração.

Valores que já haviam sido sobrescritos no catálogo antes desta implementação não podem ser recuperados com precisão; a migração não inventa versões antigas. Dados pessoais de versões anteriores continuam preservados como legado no banco, sem reintroduzi-los no extrator.

Durante execução, os resultados importados podem continuar mudando. A visão estável é fechada no estado terminal; consulta a uma execução interrompida não representa conclusão da coleta.

## Armazenamento e Docker

- SQLite: data/google-maps-scraper-kit.db no diretório Windows do projeto, fora do container do motor.
- Listas/empresas/evidências: SQLite, com vínculos e deduplicação existentes.
- CSV da API: data/exports/; arquivos antigos em exports/ e saídas explícitas da CLI não foram apagados.
- Motor: mesmo volume google-maps-scraper-kit_gmaps_data para artefatos brutos do Kit. Recriar o container com o volume preservado não remove as listas da aplicação.
- Não usar down -v nem limpeza destrutiva. Para transferir/copiar o banco em uso, usar backup SQLite consistente, como o realizado antes da migração.

## Validações executadas

- Testes automatizados: duas listas em datas diferentes para a mesma empresa preservam nome, contato, e-mail, evidências e coordenadas próprios; a segunda não altera CSV/resultados da primeira.
- API recriada sobre o mesmo SQLite: consulta e download continuam funcionando, sem novos jobs.
- CSV removido em banco temporário: download regenera conteúdo idêntico sem nova extração.
- Falha simulada de gravação: CSV anterior intacto, temporário descartado.
- Interrupção e lease vencido: recuperação na mesma campanha sem duplicação. Esgotamento de tentativas preserva a lista parcial antes de outra coleta.
- Navegador real: alternância entre duas listas sintéticas, pontos diferentes no mesmo mapa, downloads próprios e reload; nenhum worker/scraping iniciado nesse teste.
- Prova real: resultados e CSV da campanha 9dc3f64a-cb91-4566-974d-7764c3661460 comparados por SHA-256 antes/depois de recriar o container do motor e reiniciar a API. Ambos idênticos.
- Integridade SQLite ok, foreign_key_check sem violações, IDs antigos preservados, fila vazia e consumo externo inalterado.

Comandos:

```powershell
uv run python -m unittest discover -s tests -v
uv run python tests/history_browser.py
uv run python tests/browser_smoke.py
```

Relatório real local: data/live-validation/list-history-report.json, ignorado pelo Git. A prova de persistência não executou novas buscas nem usou créditos externos.

## Separação dos históricos

Google Maps Scraper Kit: histórico operacional de extrações, listas/empresas, parâmetros, mapa, revisão e CSV em SQLite.

Lead Intelligence AI: futuro histórico de ICP, qualificação, enriquecimento comercial, inteligência e prospecção em Supabase. Não foi criado ou integrado nesta entrega.

PRD vigente: ../PRD_GOOGLE_MAPS_SCRAPER_KIT.md. A persistência operacional é requisito permanente e não deve ser retirada por a integração entre produtos usar CSV.

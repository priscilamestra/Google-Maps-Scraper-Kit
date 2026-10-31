# Integrações externas

Cada repositório externo entra como submódulo próprio e mantém licença, histórico e origem. Antes de adicioná-lo:

1. auditar documentação, licença, segurança e instalação;
2. registrar commit e decisão em `docs/audits/` e `docs/ADRs/`;
3. executar testes oficiais;
4. adaptar apenas por configuração ou wrapper do pacote principal;
5. não armazenar segredos ou dados coletados dentro do submódulo.

Integrações atuais:

- `google-maps-scraper-kit`: fonte experimental de empresas, ainda sem prova real por falta de Docker.

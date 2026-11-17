# Integrações externas

Cada repositório externo entra como submódulo próprio e mantém licença, histórico e origem. Antes de adicioná-lo:

1. auditar documentação, licença, segurança e instalação;
2. registrar commit e decisão em `docs/audits/` e `docs/ADRs/`;
3. executar testes oficiais;
4. adaptar apenas por configuração ou wrapper do pacote principal;
5. não armazenar segredos ou dados coletados dentro do submódulo.

Integrações atuais:

- `google-maps-scraper-kit`: Kit oficial preservado, motor e coleta real validados; porta externa 8081.
- `google-maps-scraper` e `scrapemate`: clones oficiais de inspeção, ignorados pelo Git do produto. Exceção documentada ao fluxo de submódulos: o build obtém fontes por commits verificados e aplica um patch rastreável fora dos clones. Licenças/origens preservadas. Veja `docs/audits/engine-close-fix.md` e `infrastructure/engine/`.

# ADR-001 — Validação inicial da fonte Maps

**Status:** provisório
**Data:** 30/10/2026

## Contexto

O PRD escolhe o Google Maps Scraper Kit como primeira fonte de empresas. A fonte precisa ser barata, local, mensurável e isolada da lógica principal.

## Decisão

Manter o repositório oficial em `integrations/google-maps-scraper-kit`, sem modificar seu código. O núcleo do Google Maps Scraper Kit conversará com ele por wrapper/API somente depois da prova mínima.

O banco e a fila permanecem no pacote Python principal. O Kit não será copiado nem incorporado ao domínio.

## Portão para o próximo passo

1. instalar e iniciar Docker Desktop;
2. validar que os serviços ficam em `127.0.0.1`;
3. executar uma busca de profundidade 1 e limite 5;
4. registrar duração, linhas brutas, únicas, campos cobertos, falhas e bloqueios;
5. decidir retenção e uso permitido dos dados antes de uma campanha real.

Até esses itens serem concluídos, a fonte Maps está **NO-GO para uso real** e **GO apenas para integração offline**.

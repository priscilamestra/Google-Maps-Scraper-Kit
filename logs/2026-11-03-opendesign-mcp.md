# Incidente: OpenDesign MCP não reconhecido em algumas sessões

- **Data e horário:** 03/11/2026, entre 11:45 e 11:47 (UTC-03:00, horário de Brasília)
- **Componente:** OpenDesign 0.26.1 / Codex Desktop / Codex CLI
- **Estado final:** resolvido e validado
- **Referência operacional:** [docs/OPENDESIGN_MCP.md](../docs/OPENDESIGN_MCP.md)

## Sintomas

Algumas sessões retornavam `No MCP server named 'open-design' found`, embora o OpenDesign já tivesse funcionado anteriormente com Local Codex. Durante uma tentativa anterior de autenticação também ocorreu `invalid_auth_step`. A consulta MCP feita dentro do ambiente isolado mostrava somente parte dos servidores registrados, enquanto o perfil Windows real continha o registro `open-design`.

## Causa identificada

Os resultados vinham de contextos diferentes:

- o Codex Desktop carrega um snapshot das ferramentas MCP ao iniciar cada sessão;
- uma sessão aberta antes do registro não atualiza esse snapshot automaticamente;
- comandos executados como `CodexSandboxOffline` usam o ambiente isolado e não representam o inventário MCP do usuário Windows;
- o Codex CLI executado no contexto normal do usuário Windows lê o registro global correto.

Não havia corrupção da instalação nem ausência do servidor no perfil real. O erro de autenticação anterior não persistia: o CLI real estava autenticado com ChatGPT.

## Verificações e conclusões

1. Confirmada a instalação oficial do OpenDesign em versão `0.26.1.0` no perfil do usuário Windows.
2. Confirmado em `config.toml` o registro global STDIO de `open-design`, sem alterar outros servidores MCP.
3. `codex mcp get open-design --json`, executado no contexto real do Windows, retornou o servidor habilitado e sem motivo de desativação.
4. `codex mcp list` no perfil real listou `open-design` como habilitado; a listagem dentro de `CodexSandboxOffline` não o exibiu, confirmando a diferença de ambiente.
5. `codex login status` no perfil real confirmou autenticação ativa via ChatGPT.
6. Uma sessão nova, efêmera e somente leitura do Codex CLI realizou a chamada MCP real `open-design/list_projects`; a chamada foi concluída e retornou dois projetos.

## Solução aplicada

O registro global existente foi preservado. Não foi necessária reinstalação, alteração de credenciais nem substituição do arquivo de configuração. A validação foi feita em uma sessão nova do Codex, no contexto correto do usuário Windows, permitindo que o snapshot atual carregasse o MCP registrado.

## Prevenção

Ao encontrar `No MCP server named 'open-design' found`:

1. confirmar o contexto do processo e não usar a listagem de `CodexSandboxOffline` como estado global;
2. verificar no PowerShell normal do usuário Windows com `codex mcp get open-design --json` e `codex mcp list`;
3. confirmar `codex login status` sem apagar credenciais ou iniciar logout;
4. abrir um novo chat do Codex após registrar ou atualizar o MCP;
5. validar com uma chamada MCP real e somente leitura;
6. reinstalar o OpenDesign somente se a aplicação estiver realmente ausente ou a instalação estiver comprovadamente inválida.

Nenhum token, credencial ou segredo foi registrado neste documento.

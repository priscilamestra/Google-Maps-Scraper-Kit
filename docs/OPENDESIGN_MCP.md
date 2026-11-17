# OpenDesign MCP no Codex

Estado validado em 03/11/2026:

- OpenDesign 0.26.1 instalado no perfil Windows do usuário.
- servidor `open-design` registrado globalmente como STDIO e habilitado;
- Local Codex autenticado com ChatGPT;
- chamada real `open-design/list_projects` concluída em uma sessão efêmera nova, com dois projetos retornados.

## Por que algumas sessões mostram “No MCP server named 'open-design' found”

O Codex Desktop executa comandos comuns em um usuário isolado (`CodexSandboxOffline`). Um `codex mcp list` iniciado dentro dessa sandbox não representa o inventário do perfil Windows real. Além disso, uma sessão já aberta conserva o snapshot de ferramentas carregado quando ela começou e não passa a expor um MCP registrado depois.

## Verificação correta

Execute no contexto normal do usuário Windows:

```powershell
codex mcp get open-design --json
codex mcp list
codex login status
```

O servidor deve aparecer habilitado e apontar para a instalação oficial em `AppData\Local\Programs\Open Design`. Para validar ferramentas após registrar ou atualizar o MCP, abra um novo chat do Codex; não reinstale o OpenDesign apenas porque um chat antigo não atualizou seu snapshot.

Não copie o registro para arquivos locais do projeto e não substitua `config.toml`: isso criaria duas fontes de configuração e poderia apagar outras conexões MCP.

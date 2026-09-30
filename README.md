# Televisão.TV → M3U para SS IPTV

Projeto para gerar automaticamente uma lista M3U a partir do catálogo público do Televisão.TV.

## Recursos

- Atualização automática pelo GitHub Actions.
- Execução a cada 6 horas.
- Execução manual em Actions → Atualizar lista M3U → Run workflow.
- Categorias conforme o catálogo da fonte.
- `tvg-name`.
- `tvg-logo`.
- `group-title`.
- Detecção de novos canais.
- Remoção de canais que não possuem stream ativo.
- Validação dos streams.
- Fallback para Chromium/Playwright quando o site bloqueia clientes HTTP ou depende de JavaScript.
- Preservação da última M3U válida quando a fonte está temporariamente indisponível.
- `status.json` com relatório da execução.

## Arquivos

```text
.
├── gerar_m3u.py
├── canais.m3u
├── status.json
├── requirements.txt
├── README.md
├── LICENSE
├── .gitignore
└── .github/
    └── workflows/
        └── atualizar.yml
```

## GitHub

Coloque todos os arquivos na raiz do repositório.

Depois:

1. Abra `Actions`.
2. Abra `Atualizar lista M3U`.
3. Execute `Run workflow`.

A lista publicada pelo GitHub fica em:

```text
https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/canais.m3u
```

## SS IPTV

Use a URL `raw.githubusercontent.com` acima como fonte remota da playlist.

## Erro HTTP 403

O coletor tenta primeiro HTTP com cabeçalhos de navegador. Se o servidor responder 403, 429 ou erro semelhante, ele tenta Chromium/Playwright.

Se a fonte continuar indisponível, o programa não apaga uma `canais.m3u` anterior válida.

Isso evita que uma falha temporária da origem resulte em uma playlist vazia.

## Observação

A disponibilidade dos streams depende das fontes de transmissão. O projeto apenas coleta os endereços que são disponibilizados pelas páginas e valida a resposta HTTP.

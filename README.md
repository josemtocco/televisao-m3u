# Televisão.TV M3U v3

Gerador automático de playlist M3U para SS IPTV a partir do catálogo público do Televisão.TV.

## Principais melhorias desta versão

A versão 3 não depende de uma lista fixa de slugs de canais.

Ela:

1. abre a página inicial;
2. identifica as categorias pela navegação do próprio site;
3. abre cada categoria;
4. coleta todos os links internos;
5. usa heurísticas para identificar páginas individuais de canais;
6. abre as páginas dos canais;
7. procura `.m3u8`, `.mpd`, `.m3u` e players/iframes;
8. resolve players quando necessário;
9. testa o stream;
10. gera a M3U.

Também gera um `status.json` detalhado.

## Estrutura

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

## Execução no GitHub

O workflow executa automaticamente a cada 6 horas.

Também pode ser executado manualmente:

`Actions` → `Atualizar lista M3U` → `Run workflow`.

## URL para SS IPTV

Depois de publicar:

```text
https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/canais.m3u
```

## Diagnóstico

O `status.json` mostra:

- quantidade de categorias;
- páginas de categorias acessadas;
- páginas de canais descobertas;
- páginas de canais únicas;
- streams encontrados;
- streams ativos;
- streams inativos;
- nome do canal;
- categoria;
- página original;
- URL do stream;
- URL do logo.

## Proteção contra falhas

Se o Televisão.TV retornar 403 ou exigir JavaScript, o projeto tenta Chromium.

Se a fonte estiver temporariamente indisponível e já existir uma `canais.m3u` válida, ela é preservada.

A playlist não é substituída por uma lista vazia apenas porque uma execução falhou.

## Observação

A disponibilidade e os direitos de transmissão dos streams pertencem às respectivas fontes. O projeto apenas processa os endereços que a fonte disponibiliza publicamente.

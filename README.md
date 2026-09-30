# Televisão.TV → M3U para SS IPTV

Projeto que lê o catálogo público do **Televisão.TV**, descobre os canais e suas categorias, extrai o stream disponibilizado na página de cada canal, testa se o stream responde e gera uma playlist M3U para uso no SS IPTV.

## O que o projeto faz

- Varre as categorias do Televisão.TV automaticamente.
- Descobre os canais sem manter uma lista fixa de nomes.
- Mantém o nome do canal em `tvg-name` e no texto da entrada `#EXTINF`.
- Usa `group-title` com a categoria encontrada no site.
- Usa `tvg-logo` quando a página fornece uma imagem/logo.
- Remove canais que não têm stream detectável ou cujo stream não responde.
- Adiciona canais novos encontrados no catálogo.
- Se um canal aparecer em mais de uma categoria, ele é incluído nas categorias correspondentes.
- Atualiza automaticamente a cada 6 horas pelo GitHub Actions.
- Publica `canais.m3u` e `status.json` no próprio repositório.

## Importante sobre os sinais

O Televisão.TV funciona como catálogo/agregador de páginas e informa que transmissões podem mudar de endereço, ficar indisponíveis ou ser removidas. O projeto não tenta contornar bloqueios, autenticação, DRM ou medidas de proteção.

Use somente transmissões que você tenha direito de acessar e redistribuir. A playlist gerada contém os endereços públicos encontrados nas páginas do catálogo.

## Instalação no GitHub

1. Crie um repositório no GitHub.
2. Coloque estes arquivos no diretório principal.
3. Mantenha `.github/workflows/atualizar.yml`, pois o GitHub exige que workflows fiquem nesse diretório especial.
4. Faça o primeiro `push`.
5. Vá em **Actions → Atualizar lista M3U → Run workflow** para executar imediatamente.
6. Depois disso, o agendamento executará a cada 6 horas.

## URL para o SS IPTV

Depois de publicar o repositório, use a URL **Raw** do arquivo `canais.m3u`, por exemplo:

`https://raw.githubusercontent.com/SEU-USUARIO/SEU-REPOSITORIO/main/canais.m3u`

Substitua `SEU-USUARIO` e `SEU-REPOSITORIO` pelos dados do seu repositório.

## Execução local

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python gerar_m3u.py
```

Os arquivos gerados são:

- `canais.m3u` — playlist para o SS IPTV.
- `status.json` — relatório da última varredura.

## Formato M3U

Cada entrada usa o padrão:

```text
#EXTINF:-1 tvg-name="Nome do canal" tvg-logo="https://..." group-title="Categoria",Nome do canal
https://...stream...
```

## Por que o workflow pode remover canais?

A cada execução a playlist é reconstruída a partir do catálogo atual. Assim, um canal que deixou de existir ou cujo stream não responde não permanece indefinidamente no arquivo. Canais novos passam a aparecer na próxima execução.

## Limites e manutenção

O site pode alterar sua estrutura HTML, o player ou a forma de disponibilizar os streams. Se isso ocorrer, ajuste as funções `discover_categories`, `discover_channels` ou `extract_stream` em `gerar_m3u.py`.

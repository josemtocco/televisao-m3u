# Televisão.TV M3U v4

Corrige a descoberta de canais usando a estrutura atual do Televisão.TV.

A v4 usa as 14 categorias principais do menu e extrai links de `href`,
`data-href`, `data-url`, `data-link`, `data-channel-url` e outros atributos.
Também possui fallback por regex.

Depois abre cada página de canal, procura m3u8/mpd/m3u e iframes, valida o
stream e gera `canais.m3u` com `tvg-name`, `tvg-logo` e `group-title`.

O workflow roda a cada 6 horas e pode ser executado manualmente em Actions.

URL da playlist:
`https://raw.githubusercontent.com/USUARIO/REPOSITORIO/main/canais.m3u`

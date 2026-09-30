# Televisão.TV → M3U para SS IPTV

Versão 6.

Correções principais:
- usa somente `a:visible` para não confundir o catálogo global de aproximadamente 370 canais com a categoria atual;
- coleta as categorias separadamente;
- não espera `networkidle` nas páginas de canais;
- não faz GET/HEAD de 15 segundos para validar cada stream;
- captura URLs `.m3u8`, `.mpd` e `.m3u` do HTML, scripts, respostas de rede e players/iframes;
- processa até 20 páginas simultaneamente;
- grava `status.json` detalhado;
- atualiza a M3U somente quando encontra streams.

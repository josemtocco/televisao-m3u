# Televisão.TV → M3U para SS IPTV

A descoberta de canais usa Chromium/Playwright diretamente no DOM renderizado e lê `a.href`. O formato esperado das páginas de canal é `https://televisao.tv/slug`.

O workflow atualiza a lista a cada 6 horas e permite execução manual. Se a descoberta retornar zero, `canais.m3u` não é substituída por uma lista vazia.

O log mostra a quantidade de links e exemplos por categoria, facilitando diagnosticar qualquer alteração futura do site.

# Catálogo inicial de dados

Cada execução deve registrar data de download, URL, licença, arquivo de origem, checksum, escala/resolução e limitações.

| Tema | Fonte primária | Uso no índice | Observação crítica |
|---|---|---|---|
| Limite estadual e municípios | IBGE | Recorte, agregação e filtros | Usar a versão oficial mais recente |
| Fazendas georeferenciadas | SIGEF/INCRA | Polígonos individuais prioritários | GeoParquet para análise e MVT para visualização; certificação geométrica não prova domínio ou regularidade jurídica |
| Complemento de fazendas | SNCI/INCRA | Somente remanescentes sem sobreposição SIGEF | Exibir como `SNCI complementar` |
| Precipitação | CHIRPS v3 | Chuva, sazonalidade, chuva efetiva | Resolução de 0,05°; checar cobertura de estações |
| Meteorologia | NASA POWER | ET0 FAO-56 e extremos climáticos | Usar para triagem regional, não projeto de irrigação final |
| Água | ANA/HidroWeb | Contexto de bacia e estações | Não confirma vazão captável ou outorga |
| Desmatamento | INPE/TerraBrasilis PRODES | Alerta espacial e corte temporal de 31/12/2020 | Mostrar cobertura disponível, nunca inferir ausência de alerta fora da área monitorada |
| Embargos | IBAMA | Alerta oficial | Registrar órgão, data e versão do extrato |
| Frete | ANTT | Referência regulatória em R$/viagem e R$/t | Não representa cotação comercial de cacau |
| Terra | INCRA RAMT/PPR Bahia | Indicador regional de custo | Não é avaliação individual |

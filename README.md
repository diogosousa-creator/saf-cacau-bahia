# SAF Cacau Bahia

WebGIS para priorizar áreas de desenvolvimento de sistemas agroflorestais com cacau irrigado na Bahia.

## Arquitetura

- **Índice territorial:** hexágonos regulares de 5 km para toda a Bahia.
- **Fazendas georeferenciadas:** SIGEF prioritário, complementado por geometrias SNCI sem sobreposição analítica com o SIGEF.
- **Entrega web de fazendas:** tiles vetoriais MVT por zoom; o navegador carrega somente os imóveis visíveis. O GeoParquet analítico permanece como base de cálculo.
- **Pontuação:** nove critérios de 0 a 100, com pesos configuráveis de 0 a 10; peso zero exclui o critério do cálculo virtual.

O painel calcula a pontuação territorial e a pontuação do imóvel separadamente. A análise de imóvel usa estatísticas espaciais ponderadas pela área da sua geometria analítica.

## Índice inicial

1. Tipo de solo
2. Relevo e drenagem
3. Clima e irrigação
4. Segurança hídrica
5. PRODES e embargos
6. Logística até Ilhéus
7. Custo da terra
8. Mão de obra e infraestrutura

## Execução local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Após atualizar SIGEF/SNCI, gere a camada web com:

```bash
python scripts/build_georeferenced_farms.py --bahia data/processed/bahia_boundary.geojson --sigef "data/raw/sigef_ba/Sigef Brasil_BA.shp" --snci "$(find data/raw/snci_ba -maxdepth 1 -name '*.shp' -print -quit)" --output data/processed
python scripts/build_farm_vector_tiles.py
```

Sem dados processados, o aplicativo abre em modo demonstração, com valores explicitamente fictícios. Para carregar dados reais, o pipeline deve escrever `data/processed/assessments.parquet`.

## Fontes previstas

- SIGEF e SNCI/Acervo Fundiário INCRA
- IBGE
- CHIRPS v3 e NASA POWER
- ANA/HidroWeb
- TerraBrasilis/INPE PRODES
- IBAMA, embargos oficiais
- ANTT, piso mínimo de frete
- INCRA, RAMT/PPR Bahia

## PRODES e embargos

O PRODES vem dos rasters oficiais 30 m do INPE/TerraBrasilis por bioma
(Caatinga, Cerrado e Mata Atlântica), salvos em
`data/raw/socioambiental/prodes_raster/` com seus arquivos `.qml` de legenda.
Embargos, UC e TI ficam em `data/raw/socioambiental/`:

```text
embargos_ibama_poligonal_vigente_bahia.geoparquet
embargos_ibama_pontual_bahia.geoparquet
unidades_conservacao_cnuc_bahia.geoparquet
terras_indigenas_funai_bahia.geoparquet
```

Processamento:

```bash
.venv/bin/python scripts/build_prodes_raster_bahia.py   # mosaico Bahia 30 m
.venv/bin/python scripts/build_prodes_raster_stats.py   # área por período em hexágonos e imóveis
.venv/bin/python scripts/ingest_prodes_embargos.py --reuse-restrictions
```

Qualquer embargo Ibama vigente (poligonal ou pontual) leva a unidade a
vermelho. Sem embargo, vale o período PRODES mais recente cujo desmatamento
chega a 5% da área mapeada da unidade (≈108 ha no hexágono de 5 km):

| Classe | Condição | Nota |
|---|---|---|
| Verde escuro | nada relevante depois de 2008 (marco do Código Florestal) | 100 |
| Verde claro | 2009–2014 | 75 |
| Amarelo | 2015–2020 (o "ano 2020" do PRODES termina em jul/2020, antes do marco EUDR de 31/12/2020) | 50 |
| Laranja | a partir de 2021; nos imóveis, 1 ha ou mais já basta | 25 |
| Vermelho | embargo Ibama vigente | 0 |

UC e TI não mudam a cor: ficam como alerta explícito na caixa de informação.

## Ranking de municípios

Abaixo do mapa geral, o bloco "Ranking de municípios para prospecção" ordena os
417 municípios pela nota geral média, recalculada com os pesos da barra lateral.
O vínculo geográfico (hexágono e parcela → município) é pré-calculado:

```bash
.venv/bin/python scripts/build_municipal_ranking_base.py
```

Nota territorial: média das notas dos hexágonos ponderada pela área de cada
hexágono no município. Nota dos imóveis: média das parcelas ponderada pela área;
cada parcela conta no município que contém a maior parte dela. Ao clicar em um
município, o mapa geral enquadra o município e destaca seu contorno.

## Uso atual do solo (MapBiomas)

Critério de prospecção: áreas já abertas e baratas favorecem a implantação. Fonte: MapBiomas Brasil Coleção 11, ano 2025, pixel de 30 m.

- Nota = Σ(% da área aproveitável no grupo × nota): pastagem 100; mosaico de usos 75; lavoura temporária (soja, cana, algodão, arroz, outras) 50; lavoura perene e silvicultura 25; vegetação nativa 0. Água, área urbana, rocha e mineração ficam fora do denominador; sem área aproveitável, sem nota.
- **Base original:** `data/raw/mapbiomas_cobertura/mapbiomas_c11_cobertura_2025_bahia.tif` (códigos oficiais, grade nativa, fora do Git; ver `LEIAME.txt`). Exportada com `scripts/gee_export_mapbiomas_cobertura_bahia.js` no Google Earth Engine, porque o download direto do MapBiomas está suspenso.
- Pipeline: `build_landuse_stats.py` (área por grupo em hexágonos e imóveis, mesmo método do PRODES) → `ingest_landuse.py` (nota nas duas bases) → `build_presentation_farms.py` → `build_base_vector_tiles.py` (`s8`; hexágonos com `--max-tolerance 150`) → `build_landuse_tiles.py` (mapa temático e referência vetorial do MapBiomas, picotada em blocos).

## Tooltips e geometria dos mapas temáticos

Cada mapa temático empilha fontes com atributos diferentes (nota, MapBiomas, IBGE, COPDEM, referências climáticas) sob um único tooltip do pydeck. Por isso cada feição traz o próprio texto em português dividido nas linhas `t0…t6`, e todos os mapas usam o modelo `INDIVIDUAL_MAP_TOOLTIP` do `app.py`. Linhas separadas permitem que valores repetidos sejam gravados uma vez por tile.

- Solo, relevo, Pedologia IBGE e declividade COPDEM: gerados direto por `build_soil_hex_vector_tiles.py` (`--tooltip soil|relief`) e `build_soil_relief_reference_tiles.py`.
- Clima, segurança hídrica, custo da terra, logística, PRODES e mão de obra: `scripts/add_tooltip_text_tiles.py --kind … --fix-hex-geometry` sobre a versão anterior dos tiles, sem recalcular valores.

A simplificação dos hexágonos de 5 km fica limitada a ~150 m: com a tolerância antiga (~78 km/2^z) todos os hexágonos viravam quadriláteros nos zooms 4–5, deixando frestas no mapa. `--fix-hex-geometry` restaura o hexágono verdadeiro da grade `bahia_hex_5km.geojson`.

## Limitações

SIGEF, SNCI e a triagem espacial não comprovam propriedade, regularidade ambiental, concessão de outorga ou elegibilidade para exportação. A aplicação prioriza áreas para diligência comercial e técnica.

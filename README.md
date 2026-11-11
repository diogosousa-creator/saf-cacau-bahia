# SAF Cacau Bahia

WebGIS para priorizar áreas de desenvolvimento de sistemas agroflorestais com cacau irrigado na Bahia.

## Arquitetura

- **Índice territorial:** hexágonos regulares de 5 km para toda a Bahia.
- **Fazendas georeferenciadas:** SIGEF prioritário, complementado por geometrias SNCI sem sobreposição analítica com o SIGEF.
- **Entrega web de fazendas:** tiles vetoriais MVT por zoom; o navegador carrega somente os imóveis visíveis. O GeoParquet analítico permanece como base de cálculo.
- **Pontuação:** oito critérios de 0 a 100, com pesos configuráveis de 0 a 10; peso zero exclui o critério do cálculo virtual.

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

O processamento socioambiental usa geometrias analíticas e exige, em
`data/raw/socioambiental/`, os seguintes recortes oficiais da Bahia:

```text
prodes_incrementos_bahia.geoparquet
embargos_ibama_poligonal_vigente_bahia.geoparquet
embargos_ibama_pontual_bahia.geoparquet
unidades_conservacao_cnuc_bahia.geoparquet
terras_indigenas_funai_bahia.geoparquet
```

Após a conferência de data, vigência e atributos das fontes, execute:

```bash
.venv/bin/python scripts/ingest_prodes_embargos.py
```

Hexágonos ficam vermelhos quando a união de embargo poligonal vigente, UC e
TI alcança 50% ou mais de sua área; qualquer interseção menor fica laranja.
Em imóveis certificados, qualquer toque de embargo poligonal vigente fica
vermelho e embargo pontual fica laranja, sem buffer. Em ausência de embargo,
a classe temporal PRODES é definida pela maior área entre até 2008, 2009–2020
e após 2020. UC/TI permanecem como alertas explícitos para os imóveis.

## Limitações

SIGEF, SNCI e a triagem espacial não comprovam propriedade, regularidade ambiental, concessão de outorga ou elegibilidade para exportação. A aplicação prioriza áreas para diligência comercial e técnica.

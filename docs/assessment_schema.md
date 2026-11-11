# Base analítica por hexágono

`data/processed/assessments.parquet` é a base canônica do WebGIS para a malha territorial da Bahia. Cada linha representa um único hexágono de 5 km, identificado por `id`.

## Regras de preenchimento

- Indicadores contínuos usam média ponderada pela área de pixels ou interseções dentro do hexágono.
- Indicadores de restrição e aptidão espacial usam percentual de área do hexágono.
- Notas temáticas e `nota_geral` permanecem vazias até que suas fontes e fórmulas tenham sido processadas e auditadas.
- `status_calculo`, `versao_fontes` e `data_atualizacao` permitem rastrear cada rodada de processamento.

## Grupos de campos

| Grupo | Campos principais |
|---|---|
| Identidade | `id`, `tipo`, `area_ha`, `latitude`, `longitude` |
| Clima e irrigação | precipitação, ET0, ETc, irrigação média e P90 |
| Tipo de solo | solo favorável, textura e limitações edáficas |
| Declividade e Relevo | declividade e contexto topográfico |
| Água | índice de segurança hídrica |
| Comercial | distância, tempo, frete, terra e mão de obra/infraestrutura |
| Socioambiental | percentual PRODES pós-2020 e número de embargos |
| Resultado | oito notas temáticas, `nota_geral` e `classe_potencial` |

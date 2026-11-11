# Condição agroclimática e demanda de irrigação

O critério `clima_irrigacao` é destinado à triagem regional de cacau irrigado
de alta tecnologia. Ele descreve a pressão atmosférica e a demanda de projeto,
não a disponibilidade legal ou física de água — que pertence a
`seguranca_hidrica`.

## Fórmula de nota

`S_clima = 0,50 × S_termico + 0,20 × S_carga_termica + 0,20 × S_demanda_P80 + 0,10 × S_sazonalidade`

Os quatro termos são normalizados para 0–100 e devem ser calculados na grade
nativa, antes da média ponderada por área em hexágonos e imóveis.

## Subcomponentes

| Subcomponente | Variável | Regra inicial |
|---|---|---|
| Térmico | Temperatura média anual | 23–27 °C = 100; 21 = 60, 20 = 40, 19 ou menos = 20; 29 = 80, 31 = 60, 32 = 40 e acima de 32 = 20. Interpolação linear entre os pontos. |
| Carga térmica | P95 da Tmax diária | ≤32 °C = 100; 34 = 75; 36 = 45; ≥38 = 20. Interpolação linear entre os pontos. |
| Demanda P80 | Lâmina bruta anual no cenário seco | ≤700 mm = 100; 1.000 = 80; 1.300 = 60; 1.600 = 40; ≥1.900 = 20. Interpolação linear entre os pontos. |
| Sazonalidade | Meses consecutivos com P <60 mm | ≤1 = 100; 2 = 85; 3 = 70; 4 = 50; 5 = 30; ≥6 = 10. |

Para cada mês: `ETc = 1,10 × ET0`; `Irr_liq = max(0, ETc − P_efetiva)`;
`Irr_bruta = Irr_liq / eficiência`. A V1 usa Kc 1,10, eficiência de 0,90,
chuva efetiva mensal USDA-SCS e P80 de demanda anual. A ET0 é calculada por
FAO-56 Penman–Monteith diário com ERA5-Land. Kc, eficiência, período e método
de chuva efetiva são parâmetros versionados, não observações climáticas.

## Campos por unidade

- `temperatura_media_anual_c`, `temperatura_mes_mais_frio_c`, `tmax_p95_c`
- `vpd_epoca_seca_kpa`, `eto_anual_mm`, `eto_trimestre_seco_mm`
- `precipitacao_anual_mm`, `maior_sequencia_meses_secos`
- `lamina_irrigacao_liquida_p80_mm`, `lamina_irrigacao_bruta_p80_mm`
- `clima_termico_score`, `clima_carga_termica_score`, `clima_demanda_score`, `clima_sazonalidade_score`
- `clima_irrigacao_score`, cobertura, confiança, fontes, período, resolução nativa e alertas.

## Salvaguarda de interpretação

Clones melhorados, sombra e fertirrigação podem mitigar parte do estresse, mas
não recebem bonificação automática no mapa. Temperatura extrema, VPD, água,
sanidade, escolha clonal e desenho de sombra exigem validação local.

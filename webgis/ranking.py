"""Ranking municipal recalculado com os pesos da barra lateral.

A regra é a mesma do mapa geral: a nota de cada hexágono e de cada imóvel é a
média ponderada dos critérios com dado e peso maior que zero.  Em seguida:

* nota territorial do município = média das notas dos hexágonos, ponderada
  pela área de cada hexágono dentro do município;
* nota dos imóveis = média das notas das parcelas, ponderada pela área
  certificada de cada parcela (cada parcela pertence ao município com a maior
  parte da sua área).  O tamanho do lote é a área certificada original, antes
  do recorte visual das sobreposições.

Somente tabelas são usadas aqui; nenhuma geometria é processada no app.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from webgis.scoring import CRITERIA, weighted_score_available


def _weighted_mean(values: pd.Series, weights: pd.Series, groups: pd.Series) -> pd.Series:
    ok = values.notna() & weights.gt(0)
    frame = pd.DataFrame({"g": groups[ok], "vw": values[ok] * weights[ok], "w": weights[ok]})
    summed = frame.groupby("g")[["vw", "w"]].sum()
    return summed["vw"] / summed["w"]


def municipal_ranking(hex_scores: pd.DataFrame, hex_weights: pd.DataFrame, farm_scores: pd.DataFrame,
                      farm_municipio: pd.DataFrame, base: pd.DataFrame, weights: dict[str, int],
                      min_lot_ha: float = 0.0) -> pd.DataFrame:
    """Return one row per municipality with territorial and property scores.

    ``min_lot_ha`` selects the parcels of interest: the property score, the
    number of qualifying parcels and their area use only parcels with at least
    this area.  Municipalities with no qualifying parcel keep ``lotes_alvo = 0``.
    """
    hexes = hex_scores[["id", *CRITERIA]].copy()
    hexes["nota"], _ = weighted_score_available(hexes, weights)
    links = hex_weights.merge(hexes, on="id", how="inner")
    territorial = _weighted_mean(links["nota"], links["area_ha"], links["cod_mun"]).rename("nota_territorial")

    # Weakest criterion: area-weighted mean of each criterion with weight > 0.
    active = [key for key in CRITERIA if float(weights.get(key, 0)) > 0]
    per_criterion = pd.DataFrame({key: _weighted_mean(pd.to_numeric(links[key], errors="coerce"), links["area_ha"], links["cod_mun"])
                                  for key in active})
    weakest = per_criterion.idxmin(axis=1).map(CRITERIA).rename("criterio_mais_fraco") if active else pd.Series(dtype=object)

    farms = farm_scores[["id_fazenda", "area_ha", *CRITERIA]].merge(farm_municipio, on="id_fazenda", how="inner")
    farms["nota"], _ = weighted_score_available(farms, weights)
    farms["area_ha"] = pd.to_numeric(farms["area_ha"], errors="coerce").fillna(0)
    # Lot size = certified area before the visual overlap clip (falls back to
    # the displayed area for older link tables).  The municipal total keeps the
    # displayed area, which has no double counting between nested parcels.
    if "area_original_ha" in farms.columns:
        farms["area_lote_ha"] = pd.to_numeric(farms["area_original_ha"], errors="coerce").fillna(farms["area_ha"])
    else:
        farms["area_lote_ha"] = farms["area_ha"]
    counts = farms.groupby("cod_mun").agg(imoveis=("id_fazenda", "size"), area_imoveis_ha=("area_ha", "sum"),
                                          maior_lote_ha=("area_lote_ha", "max"))
    target = farms[farms["area_lote_ha"] >= float(min_lot_ha)]
    target_counts = target.groupby("cod_mun").agg(lotes_alvo=("id_fazenda", "size"), area_lotes_alvo_ha=("area_lote_ha", "sum"))
    farm_mean = _weighted_mean(target["nota"], target["area_lote_ha"], target["cod_mun"]).rename("nota_imoveis")

    result = (base[["cod_mun", "municipio"]].set_index("cod_mun")
              .join(territorial).join(counts).join(target_counts).join(farm_mean).join(weakest))
    for column in ("imoveis", "lotes_alvo"):
        result[column] = result[column].fillna(0).astype(int)
    for column in ("area_imoveis_ha", "area_lotes_alvo_ha", "maior_lote_ha"):
        result[column] = result[column].fillna(0.0)
    return result.reset_index()

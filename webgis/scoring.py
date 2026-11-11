"""Scoring functions shared by the WebGIS interface and processing pipeline."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


CRITERIA = {
    "tipo_solo": "Tipo de solo",
    "relevo_drenagem": "Declividade e Relevo",
    "clima_irrigacao": "Clima e irrigação",
    "seguranca_hidrica": "Segurança hídrica",
    "risco_socioambiental": "PRODES e embargos",
    "logistica_ilheus": "Logística até Ilhéus",
    "custo_terra": "Custo da terra",
    "mao_obra_infra": "Mão de obra e infraestrutura",
}

# Ordem física já gravada nos atributos ``s0…s7`` dos tiles MVT-base.  Ela é
# deliberadamente estável: a ordem de apresentação pode evoluir sem alterar o
# significado de uma camada publicada nem obrigar o navegador a baixar uma
# nova geometria.  Qualquer reconstrução de tiles deve preservar esta ordem.
TILE_SCORE_FIELDS = (
    "tipo_solo",
    "relevo_drenagem",
    "clima_irrigacao",
    "seguranca_hidrica",
    "custo_terra",
    "mao_obra_infra",
    "risco_socioambiental",
    "logistica_ilheus",
)


def weighted_score(frame: pd.DataFrame, weights: dict[str, int]) -> pd.Series:
    keys = list(CRITERIA)
    weight_array = np.asarray([weights[key] for key in keys], dtype=float)
    if (weight_array < 0).any() or weight_array.sum() == 0:
        raise ValueError("Informe pelo menos um peso maior que zero.")
    values = frame[keys].astype(float).to_numpy()
    return pd.Series(values @ weight_array / weight_array.sum(), index=frame.index).clip(0, 100)


def weighted_score_available(frame: pd.DataFrame, weights: dict[str, int]) -> tuple[pd.Series, pd.Series]:
    """Score using only available, nonzero-weight criteria per spatial unit.

    The second returned series is the percentage of total selected weight that
    was actually supported by data.  Missing values are never treated as zero.
    """
    keys = list(CRITERIA)
    w = pd.Series({key: float(weights[key]) for key in keys})
    if (w < 0).any() or w.sum() == 0:
        raise ValueError("Informe pelo menos um peso maior que zero.")
    values = frame.reindex(columns=keys).apply(pd.to_numeric, errors="coerce")
    known = values.notna()
    denominator = known.mul(w, axis=1).sum(axis=1)
    numerator = values.fillna(0).mul(w, axis=1).sum(axis=1)
    score = (numerator / denominator.replace(0, np.nan) * 100 / 100).clip(0, 100)
    coverage = (denominator / w.sum() * 100).clip(0, 100)
    return score, coverage


def potential_class(score: float) -> str:
    if score >= 80:
        return "Muito alto"
    if score >= 65:
        return "Alto"
    if score >= 50:
        return "Médio"
    if score >= 35:
        return "Baixo"
    return "Muito baixo"


def add_scores(frame: pd.DataFrame, weights: dict[str, int]) -> pd.DataFrame:
    result = frame.copy()
    result["nota_geral"] = weighted_score(result, weights).round(1)
    result["classe"] = result["nota_geral"].map(potential_class)
    return result


def available_criteria(columns: Iterable[str]) -> list[str]:
    return [key for key in CRITERIA if key in columns]

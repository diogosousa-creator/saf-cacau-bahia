"""Normalize OSM road distance to Ilhéus for the commercial WebGIS index.

The road-distance field remains the auditable evidence.  This script adds a
continuous 0–100 logistical accessibility score without changing geometries.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def normalized_score(
    distance_km: pd.Series,
    distance_min_km: float,
    distance_max_km: float,
) -> pd.Series:
    """Linear 0–100 normalisation against the fixed Bahia-wide route range.

    The exact score remains independent of the five display colours.  The
    reference range is calculated once from every valid Bahia hexagon and
    applied unchanged to both hexagons and certified properties.
    """
    d = pd.to_numeric(distance_km, errors="coerce").where(lambda value: np.isfinite(value))
    span = distance_max_km - distance_min_km
    if not np.isfinite(span) or span <= 0:
        raise ValueError("A faixa de referência de distância deve ser positiva.")
    score = 100.0 * (distance_max_km - d) / span
    return score.clip(0, 100).where(d.notna()).round(2)


def distance_class(distance_km: pd.Series, distance_min_km: float, distance_max_km: float) -> pd.Series:
    """Real-km display classes that exactly match the five score colours."""
    d = pd.to_numeric(distance_km, errors="coerce")
    span = distance_max_km - distance_min_km
    cutoff_80 = distance_max_km - 0.80 * span
    cutoff_65 = distance_max_km - 0.65 * span
    cutoff_50 = distance_max_km - 0.50 * span
    cutoff_35 = distance_max_km - 0.35 * span
    return pd.Series(np.select(
        [d.le(cutoff_80), d.le(cutoff_65), d.le(cutoff_50), d.le(cutoff_35)],
        [
            f"Muito favorável (≤{cutoff_80:.0f} km)",
            f"Favorável ({cutoff_80:.0f}–{cutoff_65:.0f} km)",
            f"Intermediária ({cutoff_65:.0f}–{cutoff_50:.0f} km)",
            f"Desfavorável ({cutoff_50:.0f}–{cutoff_35:.0f} km)",
        ],
        default=f"Muito desfavorável (>{cutoff_35:.0f} km)",
    ), index=distance_km.index).where(np.isfinite(d))


def apply(frame: pd.DataFrame, distance_min_km: float, distance_max_km: float) -> pd.DataFrame:
    result = frame.copy()
    route = pd.to_numeric(result["logistica_distancia_rota_km"], errors="coerce")
    snap = pd.to_numeric(result.get("logistica_distancia_snap_km"), errors="coerce")
    valid = np.isfinite(route) & result["logistica_status_rota"].eq("rota_osm_calculada")
    score = normalized_score(route, distance_min_km, distance_max_km).where(valid)
    result["logistica_ilheus"] = score
    result["logistica_ilheus_score"] = score
    result["logistica_ilheus_coverage_pct"] = np.where(valid, 100.0, 0.0)
    result["logistica_ilheus_status"] = np.where(valid, "calculada", "desconhecida_sem_rota")
    result["logistica_ilheus_confidence"] = np.select(
        [valid & snap.le(2), valid & snap.le(10)], ["alta", "moderada"], default="baixa"
    )
    result["logistica_ilheus_alert_level"] = np.select(
        [~valid, valid & snap.gt(10), valid & snap.gt(2)], ["diligencia", "diligencia", "atencao"], default="nenhum"
    )
    result["logistica_ilheus_alert_flags"] = np.select(
        [~valid, valid & snap.gt(10), valid & snap.gt(2)],
        ["sem_rota_osm", "snap_superior_10_km", "snap_2_a_10_km"], default="[]"
    )
    result["logistica_ilheus_source_key"] = "osm_geofabrik_nordeste"
    result["logistica_ilheus_min_referencia_km"] = float(distance_min_km)
    result["logistica_ilheus_max_referencia_km"] = float(distance_max_km)
    result["logistica_ilheus_method_version"] = "v2_normalizacao_linear_bahia_2026"
    result["logistica_classe_km"] = distance_class(route, distance_min_km, distance_max_km).where(valid)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    args = parser.parse_args()
    assessments_path = args.processed / "assessments.parquet"
    farms_path = args.processed / "fazendas_georeferenciadas.geoparquet"
    assessments_raw = pd.read_parquet(assessments_path)
    farms_raw = gpd.read_parquet(farms_path)
    # One shared reference range avoids clipping a property simply because it
    # lies marginally beyond the centroid route of the background hexagons.
    reference = pd.concat([
        assessments_raw[["logistica_distancia_rota_km", "logistica_status_rota"]],
        farms_raw[["logistica_distancia_rota_km", "logistica_status_rota"]],
    ], ignore_index=True)
    routes = pd.to_numeric(reference["logistica_distancia_rota_km"], errors="coerce")
    valid = np.isfinite(routes) & reference["logistica_status_rota"].eq("rota_osm_calculada")
    distance_min_km = float(routes.loc[valid].min())
    distance_max_km = float(routes.loc[valid].max())
    assessments = apply(assessments_raw, distance_min_km, distance_max_km)
    farms = apply(farms_raw, distance_min_km, distance_max_km)
    assessments.to_parquet(assessments_path, index=False)
    farms.to_parquet(farms_path, index=False)
    print({
        "hexagonos_com_nota": int(assessments["logistica_ilheus"].notna().sum()),
        "imoveis_com_nota": int(farms["logistica_ilheus"].notna().sum()),
        "hex_sem_rota": int(assessments["logistica_ilheus"].isna().sum()),
        "imoveis_sem_rota": int(farms["logistica_ilheus"].isna().sum()),
        "referencia_min_km": round(distance_min_km, 3),
        "referencia_max_km": round(distance_max_km, 3),
    })


if __name__ == "__main__":
    main()

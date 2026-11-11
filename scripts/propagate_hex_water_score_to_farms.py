"""Propagate the best intersecting hex water-evidence class to certified farms.

This is deliberately limited to ``seguranca_hidrica`` for the commercial map.
It keeps the farm's direct score intact and records both values, so a higher
touching-hex class is clearly an indication of nearby water evidence rather
than a statement that the farm itself owns that water point.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"


def main() -> None:
    assessments = pd.read_parquet(PROCESSED / "assessments.parquet", columns=["id", "seguranca_hidrica"])
    hexes = gpd.read_file(PROCESSED / "bahia_hex_5km.geojson")[["id_hex", "geometry"]]
    hexes = hexes.merge(assessments, left_on="id_hex", right_on="id", how="left").rename(columns={"seguranca_hidrica": "seguranca_hidrica_hex_intersectante"})
    farms = gpd.read_parquet(PROCESSED / "fazendas_georeferenciadas.geoparquet")
    direct = pd.to_numeric(farms["seguranca_hidrica"], errors="coerce")
    joined = gpd.sjoin(
        farms[["id_fazenda", "geometry"]].to_crs(hexes.crs),
        hexes[["id_hex", "seguranca_hidrica_hex_intersectante", "geometry"]],
        how="left", predicate="intersects",
    )
    best = joined.groupby("id_fazenda", as_index=False)["seguranca_hidrica_hex_intersectante"].max()
    result = farms.drop(columns=[
        "seguranca_hidrica_direta_fazenda", "seguranca_hidrica_hex_intersectante_max",
        "seguranca_hidrica_origem_mapa",
    ], errors="ignore").merge(best.rename(columns={"seguranca_hidrica_hex_intersectante": "seguranca_hidrica_hex_intersectante_max"}), on="id_fazenda", how="left", validate="one_to_one")
    result["seguranca_hidrica_direta_fazenda"] = direct.to_numpy()
    own = pd.to_numeric(result["seguranca_hidrica_direta_fazenda"], errors="coerce")
    hex_score = pd.to_numeric(result["seguranca_hidrica_hex_intersectante_max"], errors="coerce")
    result["seguranca_hidrica"] = np.fmax(own.fillna(-1), hex_score.fillna(-1)).replace(-1, np.nan)
    result["seguranca_hidrica_origem_mapa"] = np.where(
        hex_score.gt(own.fillna(-1)), "hexagono_intersectante_melhor_classe", "geometria_propria"
    )
    result.to_parquet(PROCESSED / "fazendas_georeferenciadas.geoparquet", index=False)
    elevated = int(result["seguranca_hidrica_origem_mapa"].eq("hexagono_intersectante_melhor_classe").sum())
    print(f"Imóveis atualizados: {len(result):,}; elevados pelo melhor hexágono intersectante: {elevated:,}.")


if __name__ == "__main__":
    main()

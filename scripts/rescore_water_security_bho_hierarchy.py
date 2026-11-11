"""Upgrade water-security scores using BHO6 hierarchy without redoing raster zonal stats.

Only the nearest-reach context and derived water score are refreshed.  The
already-calculated MapBiomas and SIAGAS attributes remain immutable, keeping
this correction fast and traceable.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from ingest_water_security_stats import BHO, METRIC_CRS, score


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"


def nearest_context(units: gpd.GeoDataFrame, id_column: str, streams: gpd.GeoDataFrame) -> pd.DataFrame:
    left = units[[id_column, "geometry"]].to_crs(METRIC_CRS)
    right = streams[["nustrahler", "nuareamont", "geometry"]].to_crs(METRIC_CRS)
    joined = gpd.sjoin_nearest(left, right, how="left", distance_col="bho_distancia_min_m")
    # Tied nearest segments are harmless: retain the largest hierarchy among
    # reaches at the same nearest distance, never a farther river.
    return joined[[id_column, "bho_distancia_min_m", "nustrahler", "nuareamont"]].groupby(id_column, as_index=False).agg(
        bho_distancia_min_m=("bho_distancia_min_m", "min"),
        bho_strahler_mais_proximo=("nustrahler", "max"),
        bho_area_montante_mais_proxima_km2=("nuareamont", "max"),
    )


def refresh(base: pd.DataFrame, units: gpd.GeoDataFrame, id_column: str, streams: gpd.GeoDataFrame) -> pd.DataFrame:
    replace = [
        "bho_distancia_min_m", "bho_strahler_mais_proximo", "bho_area_montante_mais_proxima_km2",
        "nota_seguranca_hidrica", "seguranca_hidrica", "seguranca_hidrica_score",
        "seguranca_hidrica_cobertura_pct", "seguranca_hidrica_confidence", "seguranca_hidrica_status",
        "seguranca_hidrica_source_key", "seguranca_hidrica_method_version", "seguranca_hidrica_alert_level",
        "seguranca_hidrica_alert_flags",
    ]
    frame = base.drop(columns=replace, errors="ignore").merge(
        nearest_context(units, id_column, streams), on=id_column, how="left", validate="one_to_one"
    )
    return score(frame)


def main() -> None:
    streams = gpd.read_parquet(BHO)
    hex_units = gpd.read_file(PROCESSED / "bahia_hex_5km.geojson")[["id_hex", "geometry"]]
    farms = gpd.read_parquet(PROCESSED / "fazendas_georeferenciadas.geoparquet")
    assessments = pd.read_parquet(PROCESSED / "assessments.parquet")
    out_hex = refresh(assessments.rename(columns={"id": "id_hex"}), hex_units, "id_hex", streams).rename(columns={"id_hex": "id"})
    out_farms = refresh(farms, farms[["id_fazenda", "geometry"]], "id_fazenda", streams)
    out_hex.to_parquet(PROCESSED / "assessments.parquet", index=False)
    out_farms.to_parquet(PROCESSED / "fazendas_georeferenciadas.geoparquet", index=False)
    print(f"V2 aplicada: {len(out_hex):,} hexágonos; {len(out_farms):,} imóveis.")


if __name__ == "__main__":
    main()

"""Ingest pre-calculated climate rasters into the two canonical WebGIS stores.

The WebGIS never reads climatic rasters at runtime.  This script uses
coverage-weighted zonal means directly over the original spatial units:
five-kilometre hexagons and certified SIGEF/SNCI properties.  The resulting
raw indicators are retained so the climate score can be recalibrated without
reprocessing the source rasters.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from exactextract import exact_extract
import rasterio


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
DOWNLOADS = Path("/Users/diogosantossouza/Downloads")

RASTERS = {
    "chirps": DOWNLOADS / "saf_cacau_bahia_chirps_resumo_1996_2025.tif",
    "thermal": DOWNLOADS / "saf_cacau_bahia_era5land_termico_1996_2025.tif",
    "demand": DOWNLOADS / "saf_cacau_bahia_demanda_irrigacao_p80_1996_2025.tif",
}

RAW_COLUMNS = (
    "clima_p_mm",
    "clima_pe_mm",
    "clima_dryseq_months",
    "clima_tmean_c",
    "clima_tmax_p95_c",
    "clima_demanda_irrigacao_p80_mm",
    "clima_cobertura_pct",
    "nota_clima_irrigacao",
)


def _extract_means(raster: Path, units: gpd.GeoDataFrame, id_column: str) -> pd.DataFrame:
    """Return zonal means, falling back to the nearest valid source pixel.

    The fallback is only for units on the edge of a raster's valid footprint.
    It is recorded explicitly rather than being presented as a polygon average.
    """
    if not raster.exists():
        raise FileNotFoundError(f"Raster climático ausente: {raster}")
    result = exact_extract(
        str(raster),
        units[[id_column, "geometry"]],
        ["mean"],
        include_cols=[id_column],
        output="pandas",
        strategy="feature-sequential",
    )
    value_columns = [column for column in result if column.endswith("_mean") or column == "mean"]
    result["_nearest_pixel"] = False
    needs_fallback = result[value_columns].isna().any(axis=1)
    if not needs_fallback.any():
        return result

    with rasterio.open(raster) as source:
        projected = units[[id_column, "geometry"]].to_crs(source.crs)
        points = projected.set_index(id_column).geometry.representative_point()
        # These climate rasters have compact national/regional grids.  Reading
        # the grid once is safer and more reproducible than guessing a radius.
        values = source.read(masked=True)
        filled_values = values.filled(np.nan)
        # GeoTIFFs may mark absent cells either with a mask or with NaN values.
        valid = (~np.ma.getmaskarray(values).any(axis=0)) & np.isfinite(filled_values).all(axis=0)
        rows, cols = np.where(valid)
        xs, ys = rasterio.transform.xy(source.transform, rows, cols, offset="center")
        xy = np.column_stack([xs, ys])
        for row in result.index[needs_fallback]:
            unit_id = result.at[row, id_column]
            point = points.loc[unit_id]
            nearest = (np.square(xy[:, 0] - point.x) + np.square(xy[:, 1] - point.y)).argmin()
            for band, column in enumerate(value_columns):
                result.at[row, column] = float(filled_values[band, rows[nearest], cols[nearest]])
            result.at[row, "_nearest_pixel"] = True
    return result


def _score_piecewise(values: pd.Series, points: list[tuple[float, float]]) -> pd.Series:
    """Linearly interpolate an agronomic 0–100 response curve."""
    x, y = zip(*points)
    numeric = pd.to_numeric(values, errors="coerce")
    result = np.interp(numeric.fillna(x[0]), x, y)
    return pd.Series(result, index=values.index).where(numeric.notna())


def score_climate(frame: pd.DataFrame) -> pd.DataFrame:
    """Initial agronomic rule for irrigated commercial cacao.

    Rainfall total is deliberately excluded from the weighted score: effective
    rainfall already affects the P80 irrigation deficit, so also scoring total
    rainfall would double count aridity.  It remains stored for QA/tooltips.
    """
    result = frame.copy()
    s_tmean = _score_piecewise(result["clima_tmean_c"], [
        (19, 20), (20, 40), (21, 60), (23, 100), (27, 100),
        (29, 80), (31, 60), (32, 40), (33, 20),
    ])
    s_tmax = _score_piecewise(result["clima_tmax_p95_c"], [
        (32, 100), (34, 75), (36, 45), (38, 20), (40, 5),
    ])
    s_demand = _score_piecewise(result["clima_demanda_irrigacao_p80_mm"], [
        (700, 100), (1000, 80), (1300, 60), (1600, 40), (1900, 20), (2200, 5),
    ])
    s_dry = _score_piecewise(result["clima_dryseq_months"], [
        (1, 100), (2, 85), (3, 70), (4, 50), (5, 30), (6, 10),
    ])
    s_pe = _score_piecewise(result["clima_pe_mm"], [
        (100, 15), (200, 25), (400, 45), (600, 65), (800, 85), (1000, 100),
    ])
    components = pd.concat([s_tmean, s_tmax, s_demand, s_dry, s_pe], axis=1)
    components.columns = ["tmean", "tmax", "demand", "dry", "pe"]
    weights = pd.Series({"tmean": 0.45, "tmax": 0.20, "demand": 0.20, "dry": 0.10, "pe": 0.05})
    known_weight = components.notna().mul(weights, axis=1).sum(axis=1)
    result["nota_clima_irrigacao"] = (
        components.fillna(0).mul(weights, axis=1).sum(axis=1)
        .div(known_weight.replace(0, np.nan)).mul(100).div(100).round(1)
    )
    # A partial edge cell may be useful for audit, but must not masquerade as
    # a complete climate assessment in the main aptitude index.
    result.loc[components.isna().any(axis=1), "nota_clima_irrigacao"] = np.nan
    return result


def build_raw_table(units: gpd.GeoDataFrame, id_column: str) -> pd.DataFrame:
    chirps = _extract_means(RASTERS["chirps"], units, id_column).rename(columns={
        "band_1_mean": "clima_p_mm",
        "band_2_mean": "clima_pe_mm",
        "band_3_mean": "clima_dryseq_months",
        "_nearest_pixel": "clima_chirps_pixel_proximo",
    })
    thermal = _extract_means(RASTERS["thermal"], units, id_column).rename(columns={
        "band_1_mean": "clima_tmean_c",
        "band_2_mean": "clima_tmax_p95_c",
        "_nearest_pixel": "clima_termico_pixel_proximo",
    })
    demand = _extract_means(RASTERS["demand"], units, id_column).rename(columns={
        "mean": "clima_demanda_irrigacao_p80_mm",
        "_nearest_pixel": "clima_demanda_pixel_proximo",
    })
    raw = chirps.merge(thermal, on=id_column, how="outer").merge(demand, on=id_column, how="outer")
    indicators = list(RAW_COLUMNS[:6])
    # This is availability of the six required indicators, not a claim about
    # sub-pixel precision.  Missing source cells reduce confidence transparently.
    raw["clima_cobertura_pct"] = raw[indicators].notna().mean(axis=1).mul(100).round(1)
    raw["clima_pixel_proximo"] = raw[[
        "clima_chirps_pixel_proximo", "clima_termico_pixel_proximo", "clima_demanda_pixel_proximo"
    ]].fillna(False).any(axis=1)
    raw = score_climate(raw)
    return raw[[id_column, *RAW_COLUMNS, "clima_pixel_proximo"]]


def attach(base: pd.DataFrame, stats: pd.DataFrame, base_id: str, stats_id: str) -> pd.DataFrame:
    result = base.drop(columns=[column for column in [*RAW_COLUMNS, "clima_pixel_proximo"] if column in base], errors="ignore")
    result = result.merge(stats.rename(columns={stats_id: base_id}), on=base_id, how="left", validate="one_to_one")
    result["clima_irrigacao"] = result["nota_clima_irrigacao"]
    result["clima_irrigacao_score"] = result["nota_clima_irrigacao"]
    available = result["nota_clima_irrigacao"].notna()
    partial = result["clima_cobertura_pct"].lt(100)
    result.loc[:, "clima_irrigacao_coverage_pct"] = result["clima_cobertura_pct"]
    result.loc[available, "clima_irrigacao_confidence"] = "medium"
    result.loc[available, "clima_irrigacao_status"] = "complete"
    result.loc[partial, "clima_irrigacao_confidence"] = "low"
    result.loc[partial, "clima_irrigacao_status"] = "partial"
    result.loc[:, "clima_irrigacao_source_key"] = "chirps_1996_2025+era5land_1996_2025"
    result.loc[:, "clima_irrigacao_method_version"] = "CACAU_IRRIGADO_CLIMA_V1"
    result.loc[available, "clima_irrigacao_alert_level"] = "none"
    result.loc[partial, "clima_irrigacao_alert_level"] = "warning"
    result.loc[available, "clima_irrigacao_alert_flags"] = "[]"
    result.loc[partial, "clima_irrigacao_alert_flags"] = '["cobertura_climatica_incompleta"]'
    proxy = result["clima_pixel_proximo"].fillna(False)
    result.loc[proxy, "clima_irrigacao_confidence"] = "low"
    result.loc[proxy, "clima_irrigacao_status"] = "nearest_pixel"
    result.loc[proxy, "clima_irrigacao_alert_level"] = "warning"
    result.loc[proxy, "clima_irrigacao_alert_flags"] = '["valor_do_pixel_climatico_mais_proximo"]'
    # A basic QA flag for the physical relation between total and effective rain.
    invalid_pe = result["clima_pe_mm"].gt(result["clima_p_mm"])
    result.loc[invalid_pe, "clima_irrigacao_alert_level"] = "warning"
    result.loc[invalid_pe, "clima_irrigacao_alert_flags"] = '["precipitacao_efetiva_maior_que_total"]'
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    args = parser.parse_args()
    processed = args.processed

    hex_units = gpd.read_file(processed / "bahia_hex_5km.geojson")[["id_hex", "geometry"]]
    farm_units = gpd.read_parquet(processed / "fazendas_georeferenciadas.geoparquet")[["id_fazenda", "geometry"]]
    hex_stats = build_raw_table(hex_units, "id_hex")
    farm_stats = build_raw_table(farm_units, "id_fazenda")

    hex_base = pd.read_parquet(processed / "assessments.parquet")
    farm_base = gpd.read_parquet(processed / "fazendas_georeferenciadas.geoparquet")
    hex_out = attach(hex_base, hex_stats, "id", "id_hex")
    farm_out = attach(farm_base, farm_stats, "id_fazenda", "id_fazenda")

    hex_stats.to_parquet(processed / "clima_irrigacao_hex_stats.parquet", index=False)
    farm_stats.to_parquet(processed / "clima_irrigacao_fazendas_stats.parquet", index=False)
    hex_out.to_parquet(processed / "assessments.parquet", index=False)
    farm_out.to_parquet(processed / "fazendas_georeferenciadas.geoparquet", index=False)
    print(f"Clima integrado: {len(hex_out):,} hexágonos ({hex_out.nota_clima_irrigacao.notna().sum():,} com nota) e "
          f"{len(farm_out):,} imóveis ({farm_out.nota_clima_irrigacao.notna().sum():,} com nota).")


if __name__ == "__main__":
    main()

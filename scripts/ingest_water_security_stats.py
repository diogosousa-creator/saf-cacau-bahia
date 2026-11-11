"""Build the preliminary water-availability evidence score for the WebGIS.

The result is a screening indicator, not proof of an irrigable flow, water
quality, legal availability or an outorga.  Every metric is calculated from
the original geometry of each hexagon or certified property; the site never
uses the score of the background hexagon for a farm.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from exactextract import exact_extract


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "hidrica"
PROCESSED = ROOT / "data" / "processed"
DOWNLOADS = Path("/Users/diogosantossouza/Downloads")
WATER_RASTER = RAW / "saf_cacau_bahia_mapbiomas_agua_c5_1985_2025.tif"
BHO = RAW / "bho6_bahia_ordem4mais.geoparquet"
SIAGAS = RAW / "siagas_bahia_pocos.geoparquet"
METRIC_CRS = "EPSG:5880"  # SIRGAS 2000 / Brazil Albers equal-area, metres

RAW_COLUMNS = [
    "agua_frequencia_historica_pct", "agua_persistente_area_pct",
    "agua_sazonal_area_pct", "agua_frequencia_recente_pct",
    "bho_trecho_intersecta", "bho_distancia_min_m", "bho_comprimento_no_imovel_km",
    "bho_strahler_max_intersectado", "bho_area_montante_max_km2",
    "bho_strahler_mais_proximo", "bho_area_montante_mais_proxima_km2",
    "siagas_pocos_no_imovel", "siagas_pocos_2km", "siagas_pocos_5km",
    "siagas_aquiferos_distintos_5km", "siagas_pocos_com_vazao_especifica_5km",
    "siagas_vazao_especifica_mediana_5km", "nota_seguranca_hidrica",
]


def curve(series: pd.Series, points: list[tuple[float, float]]) -> pd.Series:
    xs, ys = zip(*points)
    clean = pd.to_numeric(series, errors="coerce")
    return pd.Series(np.interp(clean.fillna(xs[0]), xs, ys), index=series.index).where(clean.notna())


def zonal_water(units: gpd.GeoDataFrame, unit_id: str) -> pd.DataFrame:
    if not WATER_RASTER.exists():
        raise FileNotFoundError(
            f"Raster MapBiomas Água ausente: {WATER_RASTER.name}. "
            "Conclua a exportação do Earth Engine, una as partes e execute a ingestão."
        )
    raw = exact_extract(
        str(WATER_RASTER), units[[unit_id, "geometry"]], ["mean"], include_cols=[unit_id],
        output="pandas", strategy="feature-sequential",
    )
    return raw.rename(columns={
        "band_1_mean": "agua_frequencia_historica_pct",
        "band_2_mean": "agua_persistente_area_pct",  # mean binary -> fraction
        "band_3_mean": "agua_sazonal_area_pct",       # mean binary -> fraction
        "band_4_mean": "agua_frequencia_recente_pct",
    }).assign(
        agua_persistente_area_pct=lambda x: x["agua_persistente_area_pct"] * 100,
        agua_sazonal_area_pct=lambda x: x["agua_sazonal_area_pct"] * 100,
    )


def bho_stats(units: gpd.GeoDataFrame, unit_id: str, streams: gpd.GeoDataFrame, chunk_size: int = 3000) -> pd.DataFrame:
    """Measure direct drainage and shortest edge-to-drainage distance.

    Proximity is an accessibility *context* for a future intake study, never a
    statement of riparian right, flow, quality or outorga.
    """
    target = units[[unit_id, "geometry"]].to_crs(METRIC_CRS)
    network = streams[["nustrahler", "nuareamont", "geometry"]].to_crs(METRIC_CRS)
    # Keep the hydraulic hierarchy of the *nearest* reach. Distance to an
    # intermittent small stream cannot by itself indicate irrigation security.
    nearest = gpd.sjoin_nearest(
        target, network, how="left", distance_col="bho_distancia_min_m",
    )[[unit_id, "bho_distancia_min_m", "nustrahler", "nuareamont"]].groupby(unit_id, as_index=False).agg(
        bho_distancia_min_m=("bho_distancia_min_m", "min"),
        bho_strahler_mais_proximo=("nustrahler", "max"),
        bho_area_montante_mais_proxima_km2=("nuareamont", "max"),
    )
    out: list[pd.DataFrame] = []
    for start in range(0, len(target), chunk_size):
        subset = target.iloc[start:start + chunk_size]
        pairs = gpd.sjoin(subset, network, predicate="intersects", how="inner")
        if pairs.empty:
            continue
        # The sjoin contains the source property geometry; recover only the
        # matching network geometry before measuring shared reach length.
        right = network.geometry.rename("stream_geometry")
        pairs = pairs.join(right, on="index_right")
        length_km = pairs.geometry.intersection(pairs.stream_geometry).length / 1000
        stats = pd.DataFrame({
            unit_id: pairs[unit_id].to_numpy(),
            "length_km": length_km.to_numpy(),
            "strahler": pd.to_numeric(pairs.nustrahler, errors="coerce").to_numpy(),
            "area_mont": pd.to_numeric(pairs.nuareamont, errors="coerce").to_numpy(),
        }).groupby(unit_id, as_index=False).agg(
            bho_comprimento_no_imovel_km=("length_km", "sum"),
            bho_strahler_max_intersectado=("strahler", "max"),
            bho_area_montante_max_km2=("area_mont", "max"),
        )
        out.append(stats)
    direct = pd.concat(out, ignore_index=True).groupby(unit_id, as_index=False).agg({
        "bho_comprimento_no_imovel_km": "sum",
        "bho_strahler_max_intersectado": "max",
        "bho_area_montante_max_km2": "max",
    }) if out else pd.DataFrame(columns=[unit_id, "bho_comprimento_no_imovel_km", "bho_strahler_max_intersectado", "bho_area_montante_max_km2"])
    result = pd.DataFrame({unit_id: units[unit_id]}).merge(nearest, on=unit_id, how="left").merge(direct, on=unit_id, how="left")
    result["bho_trecho_intersecta"] = result["bho_comprimento_no_imovel_km"].fillna(0).gt(0)
    return result


def _well_count_for_distance(units: gpd.GeoDataFrame, unit_id: str, wells: gpd.GeoDataFrame, distance_m: float, chunk_size: int = 2500) -> pd.DataFrame:
    target = units[[unit_id, "geometry"]].to_crs(METRIC_CRS)
    points = wells[["str_aquifero", "num_vazao_especifica", "geometry"]].to_crs(METRIC_CRS)
    results: list[pd.DataFrame] = []
    for start in range(0, len(target), chunk_size):
        subset = target.iloc[start:start + chunk_size].copy()
        subset["geometry"] = subset.geometry.buffer(distance_m)
        pairs = gpd.sjoin(subset, points, predicate="intersects", how="inner")
        if pairs.empty:
            continue
        table = pd.DataFrame({
            unit_id: pairs[unit_id].to_numpy(),
            "aquifero": pairs.str_aquifero.fillna("Não informado").astype(str).to_numpy(),
            "vazao_especifica": pd.to_numeric(pairs.num_vazao_especifica, errors="coerce").to_numpy(),
        })
        results.append(table)
    if not results:
        return pd.DataFrame(columns=[unit_id, "pocos", "aquiferos", "vazao_completa", "vazao_mediana"])
    all_pairs = pd.concat(results, ignore_index=True)
    return all_pairs.groupby(unit_id, as_index=False).agg(
        pocos=("aquifero", "size"),
        aquiferos=("aquifero", "nunique"),
        vazao_completa=("vazao_especifica", lambda x: int(pd.Series(x).notna().sum())),
        vazao_mediana=("vazao_especifica", "median"),
    )


def well_stats(units: gpd.GeoDataFrame, unit_id: str, wells: gpd.GeoDataFrame) -> pd.DataFrame:
    # Point inside the analysed geometry is materially different from a well in
    # the surrounding landscape.  Context rings are evidence only; they never
    # assert connection to the same aquifer.
    direct = _well_count_for_distance(units, unit_id, wells, 0)
    near2 = _well_count_for_distance(units, unit_id, wells, 2000)
    near5 = _well_count_for_distance(units, unit_id, wells, 5000)
    result = pd.DataFrame({unit_id: units[unit_id]})
    for table, prefix in ((direct, "no_imovel"), (near2, "2km"), (near5, "5km")):
        table = table.rename(columns={
            "pocos": f"siagas_pocos_{prefix}",
            "aquiferos": f"siagas_aquiferos_distintos_{prefix}",
            "vazao_completa": f"siagas_pocos_com_vazao_especifica_{prefix}",
            "vazao_mediana": f"siagas_vazao_especifica_mediana_{prefix}",
        })
        result = result.merge(table, on=unit_id, how="left")
    for column in result.columns:
        if column.startswith("siagas_pocos") or column.startswith("siagas_aquiferos"):
            result[column] = result[column].fillna(0).astype("int32")
    return result


def score(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    # 30-m MapBiomas evidence only within the unit; a stream beside a farm does
    # not score as water access. The curve is deliberately gradual because
    # water pixels occupy a narrow fraction of a 5-km hexagon.
    s_water_pixel = curve(result["agua_persistente_area_pct"], [(0, 0), (0.01, 15), (0.05, 30), (0.2, 60), (1, 85)])
    # BHO6 is a hydrographic network, not a discharge or permanence record.
    # Distance therefore provides at most a yellow pre-screening signal; the
    # closest reach's Strahler order/upstream area caps small streams further.
    # Green requires independent persistent-water or groundwater evidence.
    s_distance_base = curve(result["bho_distancia_min_m"], [
        (0, 55), (150, 55), (250, 52), (500, 48), (1000, 42),
        (2000, 30), (3000, 20), (5000, 8), (7500, 0),
    ])
    nearest_order = pd.to_numeric(result.get("bho_strahler_mais_proximo"), errors="coerce").fillna(0)
    nearest_area = pd.to_numeric(result.get("bho_area_montante_mais_proxima_km2"), errors="coerce").fillna(0)
    bho_cap = pd.Series(np.select(
        [(nearest_order >= 6) & (nearest_area >= 500),
         (nearest_order >= 5) & (nearest_area >= 100),
         (nearest_order >= 5),
         (nearest_order >= 4) & (nearest_area >= 25)],
        [55, 50, 45, 40], default=32), index=result.index, dtype="float64")
    s_distance = pd.concat([s_distance_base, bho_cap], axis=1).min(axis=1, skipna=True)
    s_surface = pd.concat([s_water_pixel, s_distance], axis=1).max(axis=1, skipna=True)
    # A well inside a unit is stronger evidence than nearby wells.  The 2/5 km
    # rings only identify a local hydrogeological context. Specific discharge
    # is stored for later engineering review but not converted into supply
    # without confirmed unit/method metadata.
    nearby = result["siagas_pocos_5km"].fillna(0)
    s_subsurface = pd.Series(np.select(
        [result["siagas_pocos_no_imovel"].fillna(0).gt(0),
         result["siagas_pocos_2km"].fillna(0).ge(3), nearby.ge(5)],
        [85, 55, 35], default=0), index=result.index, dtype="float64")
    components = pd.DataFrame({"subterranea": s_subsurface, "superficial": s_surface})
    # Surface and groundwater are alternative potential sources. A weighted
    # average would incorrectly make a river-adjacent parcel red merely for
    # lacking a nearby well; use the strongest evidence and reserve green for
    # direct/persistent or well-supported evidence.
    result["nota_seguranca_hidrica"] = components.max(axis=1, skipna=True).round(1)
    result["seguranca_hidrica"] = result["nota_seguranca_hidrica"]
    result["seguranca_hidrica_score"] = result["nota_seguranca_hidrica"]
    result["seguranca_hidrica_cobertura_pct"] = components.notna().mean(axis=1).mul(100).round(1)
    result["seguranca_hidrica_confidence"] = "low"
    result.loc[result["siagas_pocos_com_vazao_especifica_5km"].fillna(0).ge(3), "seguranca_hidrica_confidence"] = "medium"
    result["seguranca_hidrica_status"] = "preliminary_evidence"
    result["seguranca_hidrica_source_key"] = "mapbiomas_agua_c5_1985_2025+bho6_ana+siagas_sgb"
    result["seguranca_hidrica_method_version"] = "EVIDENCIA_HIDRICA_PRELIMINAR_V2_HIERARQUIA_BHO6"
    result["seguranca_hidrica_alert_level"] = "warning"
    result["seguranca_hidrica_alert_flags"] = '["score_nao_equivale_a_outorga","validar_captacao_vazao_e_qualidade"]'
    return result


def integrate(base: pd.DataFrame, stats: pd.DataFrame, base_id: str, stats_id: str) -> pd.DataFrame:
    stale = [c for c in RAW_COLUMNS if c in base]
    extra = [c for c in base if c.startswith("seguranca_hidrica_")]
    base = base.drop(columns=[*stale, *extra, "seguranca_hidrica"], errors="ignore")
    return base.merge(stats.rename(columns={stats_id: base_id}), on=base_id, how="left", validate="one_to_one")


def process_units(units: gpd.GeoDataFrame, unit_id: str, streams: gpd.GeoDataFrame, wells: gpd.GeoDataFrame) -> pd.DataFrame:
    water = zonal_water(units, unit_id)
    drainage = bho_stats(units, unit_id, streams)
    wells_stats = well_stats(units, unit_id, wells)
    joined = water.merge(drainage, on=unit_id, how="left").merge(wells_stats, on=unit_id, how="left")
    # BHO6 covers the state.  Therefore an absent intersection is zero direct
    # network evidence, not an unknown condition that should be removed from
    # the score denominator.
    for column in ("bho_comprimento_no_imovel_km", "bho_strahler_max_intersectado", "bho_area_montante_max_km2"):
        joined[column] = joined[column].fillna(0)
    joined["bho_trecho_intersecta"] = joined["bho_trecho_intersecta"].fillna(False)
    return score(joined)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    args = parser.parse_args()
    processed = args.processed
    if not BHO.exists() or not SIAGAS.exists():
        raise FileNotFoundError("Execute os downloads BHO6 e SIAGAS antes da ingestão.")
    streams = gpd.read_parquet(BHO)
    wells = gpd.read_parquet(SIAGAS)
    hex_units = gpd.read_file(processed / "bahia_hex_5km.geojson")[["id_hex", "geometry"]]
    farm_units = gpd.read_parquet(processed / "fazendas_georeferenciadas.geoparquet")[["id_fazenda", "geometry"]]
    hex_stats = process_units(hex_units, "id_hex", streams, wells)
    farm_stats = process_units(farm_units, "id_fazenda", streams, wells)
    hex_base = pd.read_parquet(processed / "assessments.parquet")
    farm_base = gpd.read_parquet(processed / "fazendas_georeferenciadas.geoparquet")
    integrate(hex_base, hex_stats, "id", "id_hex").to_parquet(processed / "assessments.parquet", index=False)
    integrate(farm_base, farm_stats, "id_fazenda", "id_fazenda").to_parquet(processed / "fazendas_georeferenciadas.geoparquet", index=False)
    hex_stats.to_parquet(processed / "seguranca_hidrica_hex_stats.parquet", index=False)
    farm_stats.to_parquet(processed / "seguranca_hidrica_fazendas_stats.parquet", index=False)
    print(f"Segurança hídrica integrada: {len(hex_stats):,} hexágonos e {len(farm_stats):,} imóveis.")


if __name__ == "__main__":
    main()

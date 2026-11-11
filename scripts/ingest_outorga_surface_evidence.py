"""Build the outorga-linked surface-water evidence layer and scores.

Only an active, operating, surface abstraction with current validity is used.
The point is snapped to BHO6 and propagates *only* through the same COCURSODAG;
tributaries remain unscored unless they have their own qualifying outorga.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "hidrica"
PROCESSED = ROOT / "data" / "processed"
METRIC_CRS = "EPSG:5880"
OUTORGAS = RAW / "outorgas_superficiais_bahia_cnarh.geoparquet"
BHO = RAW / "bho6_bahia_ordem4mais.geoparquet"
SIAGAS = RAW / "siagas_bahia_pocos.geoparquet"
OUTORGAS_UNIFICADAS = PROCESSED / "outorgas_oficiais_bahia.geoparquet"


def active_intakes() -> gpd.GeoDataFrame:
    data = gpd.read_parquet(OUTORGAS_UNIFICADAS).to_crs("EPSG:4326")
    mask = (
        data["tipo_agua"].eq("superficial")
        & data["outorga_ativa"].fillna(False)
        & data["tipo_interferencia"].fillna("").str.casefold().eq("captação")
        & data.geometry.notna() & ~data.geometry.is_empty
    )
    out = data.loc[mask].copy()
    out["outorga_vencimento"] = out["data_vencimento"]
    out["outorga_id"] = out["outorga_ato"].fillna(out["codigo_cnarh"]).astype(str)
    return out


def link_to_bho(points: gpd.GeoDataFrame, network: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    # 500 m is a QA tolerance for coordinates/geometries, not an influence buffer.
    net = network[["cotrecho", "cocursodag", "cobacia", "nustrahler", "nuareamont", "geometry"]].to_crs(METRIC_CRS)
    linked = gpd.sjoin_nearest(
        points.to_crs(METRIC_CRS), net, how="left", max_distance=500,
        distance_col="outorga_bho_snap_m",
    ).dropna(subset=["cocursodag"]).copy()
    linked["cocursodag"] = linked["cocursodag"].astype(str)
    active_courses = set(linked["cocursodag"])
    courses = net[net["cocursodag"].astype(str).isin(active_courses)].copy()
    courses["cocursodag"] = courses["cocursodag"].astype(str)
    return linked.to_crs("EPSG:4326"), courses.to_crs("EPSG:4326")


def unit_stats(units: gpd.GeoDataFrame, unit_id: str, intakes: gpd.GeoDataFrame, courses: gpd.GeoDataFrame) -> pd.DataFrame:
    target = units[[unit_id, "geometry"]].to_crs(METRIC_CRS)
    points = intakes[["outorga_id", "cocursodag", "geometry"]].to_crs(METRIC_CRS)
    lines = courses[["cocursodag", "geometry"]].to_crs(METRIC_CRS)
    inside = gpd.sjoin(target, points, how="left", predicate="intersects")
    inside_stats = inside.dropna(subset=["index_right"]).groupby(unit_id, as_index=False).agg(
        outorga_superficial_ativa_qtd=("outorga_id", "nunique"),
        outorga_superficial_id_no_imovel=("outorga_id", "first"),
    )
    near = gpd.sjoin_nearest(target, lines, how="left", distance_col="outorga_superficial_distancia_curso_m")
    near = near[[unit_id, "outorga_superficial_distancia_curso_m", "cocursodag"]].groupby(unit_id, as_index=False).agg(
        outorga_superficial_distancia_curso_m=("outorga_superficial_distancia_curso_m", "min"),
        outorga_superficial_cocursodag_mais_proximo=("cocursodag", "first"),
    )
    # A course crossing a unit is distinct from a capture point inside it.
    intersects = gpd.sjoin(target, lines, how="left", predicate="intersects")
    intersects = intersects.dropna(subset=["index_right"]).groupby(unit_id, as_index=False).size().rename(columns={"size": "outorga_superficial_curso_intersecta_qtd"})
    result = pd.DataFrame({unit_id: units[unit_id]}).merge(inside_stats, on=unit_id, how="left").merge(near, on=unit_id, how="left").merge(intersects, on=unit_id, how="left")
    result["outorga_superficial_ativa_qtd"] = result["outorga_superficial_ativa_qtd"].fillna(0).astype("int32")
    result["outorga_superficial_curso_intersecta_qtd"] = result["outorga_superficial_curso_intersecta_qtd"].fillna(0).astype("int32")
    result["outorga_superficial_ativa_no_imovel"] = result["outorga_superficial_ativa_qtd"].gt(0)
    # Dark green: active intake point in the analysed unit. Light green: the
    # authorised course itself crosses it. Neighbours receive a declining cue.
    distance = result["outorga_superficial_distancia_curso_m"].fillna(np.inf)
    result["nota_evidencia_outorga_superficial"] = np.select(
        [result["outorga_superficial_ativa_no_imovel"], result["outorga_superficial_curso_intersecta_qtd"].gt(0), distance.le(5_000), distance.le(10_000)],
        [100, 75, 55, 40], default=20,
    ).astype("float64")
    return result


def _well_count(units: gpd.GeoDataFrame, unit_id: str, wells: gpd.GeoDataFrame, distance_m: float = 0) -> pd.DataFrame:
    target = units[[unit_id, "geometry"]].to_crs(METRIC_CRS).copy()
    if distance_m:
        target["geometry"] = target.geometry.buffer(distance_m)
    pairs = gpd.sjoin(target, wells[["geometry"]].to_crs(METRIC_CRS), how="inner", predicate="intersects")
    if pairs.empty:
        return pd.DataFrame(columns=[unit_id, "count"])
    return pairs.groupby(unit_id, as_index=False).size().rename(columns={"size": "count"})


def well_evidence(units: gpd.GeoDataFrame, unit_id: str) -> pd.DataFrame:
    wells = gpd.read_parquet(SIAGAS)
    status = wells["str_tipo_situacao"].fillna("").astype(str).str.strip()
    yield_test = pd.to_numeric(wells["num_vazao_especifica"], errors="coerce")
    # A SIAGAS record is an inventory record.  It is strong enough for yellow
    # only when its reported situation is operational and it has a positive
    # productivity test. All other records are orange context if inside unit.
    active = wells[status.isin(["Bombeando", "Equipado"]) & yield_test.gt(0)].copy()
    uncertain = wells.drop(index=active.index).copy()
    result = pd.DataFrame({unit_id: units[unit_id]})
    for frame, name, distance in (
        (active, "siagas_pocos_ativos_teste_no_imovel", 0),
        (active, "siagas_pocos_ativos_teste_2km", 2_000),
        (uncertain, "siagas_pocos_incertos_no_imovel", 0),
    ):
        stats = _well_count(units, unit_id, frame, distance).rename(columns={"count": name})
        result = result.merge(stats, on=unit_id, how="left")
        result[name] = result[name].fillna(0).astype("int32")
    return result


def official_groundwater_evidence(units: gpd.GeoDataFrame, unit_id: str) -> pd.DataFrame:
    records = gpd.read_parquet(OUTORGAS_UNIFICADAS)
    base = records[
        records["tipo_agua"].eq("subterranea")
        & records["tipo_interferencia"].fillna("").str.casefold().eq("captação")
    ].copy()
    active = base[base["outorga_ativa"].fillna(False)]
    inactive = base[~base["outorga_ativa"].fillna(False)]
    result = pd.DataFrame({unit_id: units[unit_id]})
    for wells, field in ((active, "outorga_subterranea_ativa_no_imovel_qtd"), (inactive, "outorga_subterranea_inativa_no_imovel_qtd")):
        stats = _well_count(units, unit_id, wells, 0).rename(columns={"count": field})
        result = result.merge(stats, on=unit_id, how="left")
        result[field] = result[field].fillna(0).astype("int32")
    return result


def apply_score(base: pd.DataFrame, stats: pd.DataFrame, base_id: str, stats_id: str) -> pd.DataFrame:
    cols = [c for c in base if c.startswith("outorga_superficial_") or c.startswith("outorga_subterranea_") or c.startswith("siagas_pocos_ativos_teste_") or c.startswith("siagas_pocos_incertos_") or c in {"nota_evidencia_outorga_superficial", "nota_evidencia_subterranea"}]
    result = base.drop(columns=cols, errors="ignore").merge(stats.rename(columns={stats_id: base_id}), on=base_id, how="left", validate="one_to_one")
    # Fixed hierarchy requested for the statewide commercial screening:
    # an active outorga inside wins; a productive operational well is yellow;
    # uncertain/dry/inactive/untested records are orange context only.
    subsurface = np.select(
        [result["outorga_subterranea_ativa_no_imovel_qtd"].gt(0), result["siagas_pocos_ativos_teste_no_imovel"].gt(0), result["siagas_pocos_ativos_teste_2km"].ge(3), result["outorga_subterranea_inativa_no_imovel_qtd"].gt(0), result["siagas_pocos_incertos_no_imovel"].gt(0)],
        [75, 55, 55, 55, 40], default=20,
    )
    result["nota_evidencia_subterranea"] = subsurface.astype("float64")
    result["nota_seguranca_hidrica"] = np.where(result["outorga_superficial_ativa_no_imovel"], 100.0, subsurface).round(1)
    result["seguranca_hidrica"] = result["nota_seguranca_hidrica"]
    result["seguranca_hidrica_score"] = result["nota_seguranca_hidrica"]
    result["seguranca_hidrica_method_version"] = "EVIDENCIA_HIDRICA_V5_OUTORGAS_OFICIAIS_UNIFICADAS"
    result["seguranca_hidrica_source_key"] = "outorgas_oficiais_bahia_cnarh_snirh+siagas_sgb"
    result["seguranca_hidrica_alert_flags"] = '["score_nao_equivale_a_outorga","titularidade_vazao_e_validade_a_confirmar"]'
    return result


def main() -> None:
    intakes, courses = link_to_bho(active_intakes(), gpd.read_parquet(BHO))
    intakes.to_parquet(PROCESSED / "outorgas_superficiais_ativas_bahia.geoparquet", index=False)
    courses.to_parquet(PROCESSED / "cursos_bho6_com_outorga_ativa.geoparquet", index=False)
    hexes = gpd.read_file(PROCESSED / "bahia_hex_5km.geojson")[["id_hex", "geometry"]]
    farms = gpd.read_parquet(PROCESSED / "fazendas_georeferenciadas.geoparquet")
    hex_stats = unit_stats(hexes, "id_hex", intakes, courses).merge(well_evidence(hexes, "id_hex"), on="id_hex", how="left").merge(official_groundwater_evidence(hexes, "id_hex"), on="id_hex", how="left")
    farm_stats = unit_stats(farms[["id_fazenda", "geometry"]], "id_fazenda", intakes, courses).merge(well_evidence(farms[["id_fazenda", "geometry"]], "id_fazenda"), on="id_fazenda", how="left").merge(official_groundwater_evidence(farms[["id_fazenda", "geometry"]], "id_fazenda"), on="id_fazenda", how="left")
    assessments = pd.read_parquet(PROCESSED / "assessments.parquet")
    out_hex = apply_score(assessments.rename(columns={"id": "id_hex"}), hex_stats, "id_hex", "id_hex").rename(columns={"id_hex": "id"})
    out_farms = apply_score(farms, farm_stats, "id_fazenda", "id_fazenda")
    out_hex.to_parquet(PROCESSED / "assessments.parquet", index=False)
    out_farms.to_parquet(PROCESSED / "fazendas_georeferenciadas.geoparquet", index=False)
    hex_stats.to_parquet(PROCESSED / "seguranca_hidrica_outorga_hex_stats.parquet", index=False)
    farm_stats.to_parquet(PROCESSED / "seguranca_hidrica_outorga_fazendas_stats.parquet", index=False)
    print(f"Captações ativas vinculadas: {len(intakes):,}; cursos BHO6: {courses.cocursodag.nunique():,}; hexágonos: {len(hex_stats):,}; imóveis: {len(farm_stats):,}.")


if __name__ == "__main__":
    main()

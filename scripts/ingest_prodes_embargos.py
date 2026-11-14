"""Integrate official PRODES and territorial-restriction evidence.

The script intentionally separates two decision rules:

* hexagons: territorial restrictions are proportional (red only when their
  combined footprint reaches 50% of the cell);
* certified properties: any intersection with a *current polygonal* federal
  embargo is red.  Point embargos are orange alerts and never receive an
  invented influence buffer.

All statistics are calculated on the analytical geometries, never on the MVT
geometries simplified for browser delivery.  The inputs must already be
clipped to Bahia and use only official sources.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "socioambiental"
PROCESSED = ROOT / "data" / "processed"
METRIC_CRS = "EPSG:5880"
# Área mínima mapeada pelo PRODES (ha) usada na regra EUDR dos imóveis.
EUDR_MIN_POST2020_HA = 1.0
# Um período "conta" na unidade quando seu desmatamento chega a 5% da área
# mapeada pelo PRODES (hexágono de 5 km ≈ 108 ha). Vale o período mais recente
# que atinge o limiar; abaixo dele as manchas não mudam a classe.
LIMIAR_PERIODO_PCT = 5.0

# Incrementos devem ser mutuamente exclusivos. A área acumulada até 2000,
# quando usada, deve chegar já marcada como ``year=2000``.
# Até 2008 = marco do Código Florestal; o "ano 2020" do PRODES termina em
# jul/2020, antes do marco EUDR (31/12/2020), por isso fica em 2015–2020.
PERIODS = {
    "pre2008": lambda year: year <= 2008,
    "2009_2014": lambda year: (year >= 2009) & (year <= 2014),
    "2015_2020": lambda year: (year >= 2015) & (year <= 2020),
    "post2020": lambda year: year >= 2021,
}


def _read(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Fonte obrigatória não encontrada: {path}")
    frame = gpd.read_parquet(path) if path.suffix.lower() in {".parquet", ".geoparquet"} else gpd.read_file(path)
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    return frame.to_crs(METRIC_CRS)


def _empty_area_stats(ids: pd.Series, id_field: str) -> pd.DataFrame:
    result = pd.DataFrame({id_field: ids.astype(str)})
    for period in PERIODS:
        result[f"prodes_{period}_ha"] = 0.0
    return result


def prodes_area_stats(units: gpd.GeoDataFrame, unit_id: str, prodes: gpd.GeoDataFrame) -> pd.DataFrame:
    """Area by period, using intersections in an equal-area CRS.

    TerraBrasilis yearly increments are non-overlapping by design.  We still
    union fragments belonging to the same unit and period so duplicated source
    fragments are not counted twice.
    """
    frame = prodes.copy()
    # The PRODES preparation step normally supplies explicit, mutually
    # exclusive intervals.  This is essential for ``pre2008`` because the
    # Marco UE layer preserves historical deforestation that predates annual
    # coverage in some biomes.  Retain a year-based fallback for older inputs.
    if "periodo" in frame.columns:
        frame["_period"] = frame["periodo"].astype(str)
        frame = frame[frame["_period"].isin(PERIODS)].copy()
    else:
        year_col = next((col for col in ("year", "ano", "ANO") if col in frame.columns), None)
        if year_col is None:
            raise ValueError("A camada PRODES precisa conter 'periodo' ou o ano do incremento (year/ano).")
        frame["_year"] = pd.to_numeric(frame[year_col], errors="coerce")
        frame = frame[frame["_year"].notna()].copy()
        frame["_period"] = np.select(
            [rule(frame["_year"]) for rule in PERIODS.values()],
            list(PERIODS),
            default=None,
        )
    frame = frame[frame["_period"].notna()].copy()
    base = units[[unit_id, "geometry"]].to_crs(METRIC_CRS).copy()
    base[unit_id] = base[unit_id].astype(str)
    out = _empty_area_stats(base[unit_id], unit_id)
    if frame.empty:
        return out

    # A single overlay of all 818k PRODES geometries against all properties
    # creates a multi-million-row intermediate object and can exhaust memory.
    # Query the source STRtree in bounded unit batches instead. Geometry is
    # still intersected in the equal-area CRS and unioned by unit/period, but
    # no browser-facing or approximate geometry is ever used.
    source = frame[["_period", "geometry"]].reset_index(drop=True)
    source_index = source.sindex
    rows: list[dict[str, object]] = []
    batch_size = 750
    for start in range(0, len(base), batch_size):
        subset = base.iloc[start : start + batch_size]
        pairs = source_index.query(subset.geometry, predicate="intersects")
        if pairs.size == 0:
            continue
        for local_pos in np.unique(pairs[0]):
            unit = subset.iloc[int(local_pos)]
            candidate_pos = pairs[1][pairs[0] == local_pos]
            candidates = source.iloc[candidate_pos]
            intersections = candidates.geometry.intersection(unit.geometry)
            valid = intersections.notna() & ~intersections.is_empty
            if not valid.any():
                continue
            clipped = gpd.GeoDataFrame(
                {"_period": candidates.loc[valid, "_period"].to_numpy()},
                geometry=intersections.loc[valid].to_numpy(),
                crs=METRIC_CRS,
            )
            for period, group in clipped.groupby("_period", sort=False):
                # ``union_all`` per unit/period prevents any duplicated source
                # fragments from being counted twice.
                rows.append({unit_id: str(unit[unit_id]), "_period": period, "area_ha": group.geometry.union_all().area / 10_000})
        print(f"PRODES {unit_id}: {min(start + batch_size, len(base)):,}/{len(base):,}", flush=True)
    if not rows:
        return out
    long = pd.DataFrame(rows)
    pivot = long.pivot(index=unit_id, columns="_period", values="area_ha").fillna(0).reset_index()
    for period in PERIODS:
        source = pivot.get(period, pd.Series(0.0, index=pivot.index))
        pivot[f"prodes_{period}_ha"] = source
    keep = [unit_id, *[f"prodes_{period}_ha" for period in PERIODS]]
    return out.drop(columns=keep[1:]).merge(pivot[keep], on=unit_id, how="left").fillna({field: 0.0 for field in keep[1:]})


def _restriction_stats(
    units: gpd.GeoDataFrame,
    unit_id: str,
    embargo_polygons: gpd.GeoDataFrame,
    conservation_units: gpd.GeoDataFrame,
    indigenous_lands: gpd.GeoDataFrame,
    embargo_points: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Compute exact protected/embargo footprint and point-embargo flags."""
    base = units[[unit_id, "geometry"]].to_crs(METRIC_CRS).copy()
    base[unit_id] = base[unit_id].astype(str)
    base["_area_ha"] = base.geometry.area / 10_000
    area_rows = []
    for label, layer in (
        ("embargo", embargo_polygons),
        ("uc", conservation_units),
        ("ti", indigenous_lands),
    ):
        layer = layer.to_crs(METRIC_CRS)
        if layer.empty:
            continue
        pieces = gpd.overlay(base[[unit_id, "geometry"]], layer[["geometry"]], how="intersection", keep_geom_type=False)
        if pieces.empty:
            continue
        for unit, group in pieces.groupby(unit_id, sort=False):
            area_rows.append({unit_id: str(unit), "tipo": label, "geometry": group.geometry.union_all()})

    result = base[[unit_id, "_area_ha"]].copy()
    for label in ("embargo", "uc", "ti"):
        result[f"{label}_intersecao_ha"] = 0.0
        result[f"{label}_intersecao_pct"] = 0.0
    if area_rows:
        by_type = gpd.GeoDataFrame(area_rows, geometry="geometry", crs=METRIC_CRS)
        for label, group in by_type.groupby("tipo", sort=False):
            areas = group.assign(_ha=group.geometry.area / 10_000)[[unit_id, "_ha"]]
            result = result.drop(columns=[f"{label}_intersecao_ha"], errors="ignore").merge(
                areas.rename(columns={"_ha": f"{label}_intersecao_ha"}), on=unit_id, how="left"
            )
            result[f"{label}_intersecao_ha"] = result[f"{label}_intersecao_ha"].fillna(0.0)
            result[f"{label}_intersecao_pct"] = (result[f"{label}_intersecao_ha"] / result["_area_ha"] * 100).fillna(0.0)

        # The red/orange hex rule uses the union of all restrictions, not a
        # sum of three areas which can overlap one another.
        union_rows = []
        for unit, group in by_type.groupby(unit_id, sort=False):
            union_rows.append({unit_id: str(unit), "restricao_unida_ha": group.geometry.union_all().area / 10_000})
        union = pd.DataFrame(union_rows)
        result = result.merge(union, on=unit_id, how="left")
    else:
        result["restricao_unida_ha"] = 0.0
    result["restricao_unida_ha"] = result["restricao_unida_ha"].fillna(0.0)
    result["restricao_unida_pct"] = (result["restricao_unida_ha"] / result["_area_ha"] * 100).fillna(0.0)

    points = embargo_points.to_crs(METRIC_CRS)
    if points.empty:
        result["embargo_pontual_qtd"] = 0
    else:
        hits = gpd.sjoin(base[[unit_id, "geometry"]], points[["geometry"]], how="left", predicate="intersects")
        counts = hits.dropna(subset=["index_right"]).groupby(unit_id).size().rename("embargo_pontual_qtd").reset_index()
        result = result.merge(counts, on=unit_id, how="left")
        result["embargo_pontual_qtd"] = result["embargo_pontual_qtd"].fillna(0).astype("int32")
    return result.drop(columns="_area_ha")


def _temporal_class(frame: pd.DataFrame) -> pd.Series:
    """Classe pelo período PRODES mais recente com desmatamento relevante.

    Verde escuro: nada relevante depois de 2008; Verde claro: 2009–2014;
    Amarelo: 2015–2020; Laranja: a partir de 2021. "Relevante" é atingir
    ``LIMIAR_PERIODO_PCT`` da área mapeada pelo PRODES na unidade.
    """
    mapped = frame["prodes_area_mapeada_ha"].fillna(0).astype(float)
    limit = mapped * LIMIAR_PERIODO_PCT / 100
    def hits(column: str) -> pd.Series:
        area = frame[column].fillna(0).astype(float)
        return (area > 0) & area.ge(limit)
    result = np.select(
        [mapped.le(0), hits("prodes_post2020_ha"), hits("prodes_2015_2020_ha"), hits("prodes_2009_2014_ha")],
        ["Sem evidência temporal classificável", "Laranja", "Amarelo", "Verde claro"],
        default="Verde escuro",
    )
    return pd.Series(result, index=frame.index)


def _score_for_class(classification: pd.Series) -> pd.Series:
    return classification.map({
        "Verde escuro": 100.0,
        "Verde claro": 75.0,
        "Amarelo": 50.0,
        "Laranja": 25.0,
        "Vermelho": 0.0,
    })


def _any_embargo(frame: pd.DataFrame) -> pd.Series:
    return frame["embargo_intersecao_ha"].fillna(0).gt(0) | frame["embargo_pontual_qtd"].fillna(0).gt(0)


def classify_hexes(table: pd.DataFrame) -> pd.DataFrame:
    result = table.copy()
    temporal = _temporal_class(result)
    # Qualquer embargo Ibama (poligonal ou pontual) leva o hexágono a vermelho.
    # UC e TI não mudam a cor: ficam como alerta explícito na unidade.
    result["classe_prodes_embargos"] = np.where(_any_embargo(result), "Vermelho", temporal)
    result["risco_socioambiental"] = _score_for_class(result["classe_prodes_embargos"])
    result["prodes_embargos_regra"] = "HEX_V2_EMBARGO_E_PERIODO_RECENTE_5PCT"
    return result


def classify_farms(table: pd.DataFrame) -> pd.DataFrame:
    result = table.copy()
    temporal = _temporal_class(result)
    # EUDR (Regulamento UE 2023/1115): marco 31/12/2020. Nos imóveis, qualquer
    # desmatamento PRODES a partir de 2021 igual ou acima da área mínima
    # mapeada (1 ha) leva a laranja; os demais períodos seguem o limiar de 5%.
    eudr_recent = result["prodes_post2020_ha"].fillna(0).ge(EUDR_MIN_POST2020_HA)
    result["classe_prodes_embargos"] = np.select(
        [_any_embargo(result), eudr_recent],
        ["Vermelho", "Laranja"],
        default=temporal,
    )
    result["risco_socioambiental"] = _score_for_class(result["classe_prodes_embargos"])
    result["prodes_embargos_regra"] = "IMOVEL_V3_EMBARGO_EUDR_1HA_PERIODO_5PCT"
    return result


def _attach(base: pd.DataFrame, stats: pd.DataFrame, base_id: str, stats_id: str, classifier) -> pd.DataFrame:
    fields = [
        *[f"prodes_{period}_ha" for period in PERIODS],
        "prodes_vegetacao_nativa_ha", "prodes_hidrografia_ha", "prodes_nao_observado_ha", "prodes_outros_ha", "prodes_area_mapeada_ha",
        "embargo_intersecao_ha", "embargo_intersecao_pct", "uc_intersecao_ha", "uc_intersecao_pct",
        "ti_intersecao_ha", "ti_intersecao_pct", "restricao_unida_ha", "restricao_unida_pct",
        "embargo_pontual_qtd", "classe_prodes_embargos", "risco_socioambiental", "prodes_embargos_regra",
        "risco_socioambiental_score", "risco_socioambiental_status", "risco_socioambiental_confidence",
        "risco_socioambiental_source_key", "risco_socioambiental_method_version", "risco_socioambiental_alert_flags",
    ]
    legacy = ["prodes_2009_2020_ha"]   # período único usado até a versão V2
    result = base.drop(columns=[field for field in [*fields, *legacy] if field in base.columns], errors="ignore")
    result = result.merge(stats.rename(columns={stats_id: base_id}), on=base_id, how="left", validate="one_to_one")
    result = classifier(result)
    result["risco_socioambiental_score"] = result["risco_socioambiental"]
    result["risco_socioambiental_status"] = np.where(
        result["classe_prodes_embargos"].eq("Sem evidência temporal classificável"), "coverage_insufficient", "complete"
    )
    result["risco_socioambiental_confidence"] = np.where(
        result["risco_socioambiental_status"].eq("complete"), "medium", "low"
    )
    result["risco_socioambiental_source_key"] = "prodes_inpe_raster_2025_2026+embargos_ibama_pamgia+cnuc_mma+funai"
    result["risco_socioambiental_method_version"] = "PRODES_EMBARGOS_V3_RASTER_4PERIODOS"
    def flags(row: pd.Series) -> str:
        values = []
        if row["embargo_intersecao_ha"] > 0: values.append("embargo_ibama_poligonal")
        if row["embargo_pontual_qtd"] > 0: values.append("embargo_ibama_pontual")
        if row["uc_intersecao_ha"] > 0: values.append("unidade_conservacao")
        if row["ti_intersecao_ha"] > 0: values.append("terra_indigena")
        return "[" + ",".join(f'\"{value}\"' for value in values) + "]"
    result["risco_socioambiental_alert_flags"] = result.apply(flags, axis=1)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--vector-prodes", action="store_true",
                        help="Usa a preparação vetorial antiga (WFS/Marco UE) em vez do raster oficial 30 m.")
    parser.add_argument("--reuse-restrictions", action="store_true",
                        help="Reaproveita embargo/UC/TI já calculados nos arquivos *_stats.parquet.")
    args = parser.parse_args()
    raw = args.raw

    hexes = gpd.read_file(args.processed / "bahia_hex_5km.geojson").to_crs(METRIC_CRS)
    farms = gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet").to_crs(METRIC_CRS)
    assessments = pd.read_parquet(args.processed / "assessments.parquet")

    restriction_fields = [
        "embargo_intersecao_ha", "embargo_intersecao_pct", "uc_intersecao_ha", "uc_intersecao_pct",
        "ti_intersecao_ha", "ti_intersecao_pct", "restricao_unida_ha", "restricao_unida_pct", "embargo_pontual_qtd",
    ]

    def restrictions(units: gpd.GeoDataFrame, id_field: str, previous: str) -> pd.DataFrame:
        if args.reuse_restrictions:
            old = pd.read_parquet(args.processed / previous, columns=[id_field, *restriction_fields])
            old[id_field] = old[id_field].astype(str)
            missing = set(units[id_field].astype(str)) - set(old[id_field])
            if missing:
                raise RuntimeError(f"{previous}: {len(missing):,} unidades sem restrição calculada.")
            return old
        return _restriction_stats(
            units, id_field,
            _read(raw / "embargos_ibama_poligonal_vigente_bahia.geoparquet"),
            _read(raw / "unidades_conservacao_cnuc_bahia.geoparquet"),
            _read(raw / "terras_indigenas_funai_bahia.geoparquet"),
            _read(raw / "embargos_ibama_pontual_bahia.geoparquet"),
        )

    def prodes_stats(units: gpd.GeoDataFrame, id_field: str, raster_file: str) -> pd.DataFrame:
        if args.vector_prodes:
            return prodes_area_stats(units, id_field, _read(raw / "prodes_incrementos_bahia.geoparquet"))
        stats = pd.read_parquet(args.processed / raster_file)
        stats[id_field] = stats[id_field].astype(str)
        if len(stats) != len(units) or set(stats[id_field]) != set(units[id_field].astype(str)):
            raise RuntimeError(f"{raster_file} não corresponde às unidades atuais.")
        return stats

    hex_stats = prodes_stats(hexes, "id_hex", "prodes_raster_hex_stats.parquet").merge(
        restrictions(hexes, "id_hex", "prodes_embargos_hex_stats.parquet"), on="id_hex", how="left", validate="one_to_one")
    farm_stats = prodes_stats(farms, "id_fazenda", "prodes_raster_fazendas_stats.parquet").merge(
        restrictions(farms, "id_fazenda", "prodes_embargos_fazendas_stats.parquet"), on="id_fazenda", how="left", validate="one_to_one")
    out_hex = _attach(assessments.rename(columns={"id": "id_hex"}), hex_stats, "id_hex", "id_hex", classify_hexes).rename(columns={"id_hex": "id"})
    out_farms = _attach(farms, farm_stats, "id_fazenda", "id_fazenda", classify_farms)
    timestamp = datetime.now(timezone.utc).isoformat()
    for table in (out_hex, out_farms):
        table["risco_socioambiental_processed_at_utc"] = timestamp
    out_hex.to_parquet(args.processed / "assessments.parquet", index=False)
    out_farms.to_parquet(args.processed / "fazendas_georeferenciadas.geoparquet", index=False)
    hex_stats.to_parquet(args.processed / "prodes_embargos_hex_stats.parquet", index=False)
    farm_stats.to_parquet(args.processed / "prodes_embargos_fazendas_stats.parquet", index=False)
    print(f"PRODES/embargos integrado: {len(out_hex):,} hexágonos e {len(out_farms):,} imóveis.")


if __name__ == "__main__":
    main()

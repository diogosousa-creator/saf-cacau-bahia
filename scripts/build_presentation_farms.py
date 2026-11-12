"""Build the non-overlapping presentation/picking layer for certified farms.

This never changes ``fazendas_georeferenciadas.geoparquet``.  The output is a
separate layer with one record per official INCRA + parcel identifier, intended
solely for WebGIS display and clicking.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely


SCORE_FIELDS = [
    "tipo_solo", "relevo_drenagem", "clima_irrigacao", "seguranca_hidrica",
    "risco_socioambiental", "logistica_ilheus", "custo_terra", "mao_obra_infra", "uso_solo",
]


def polygonal(geometry):
    if geometry is None or geometry.is_empty:
        return None
    geometry = shapely.make_valid(geometry)
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    parts = [part for part in shapely.get_parts(geometry) if part.geom_type in {"Polygon", "MultiPolygon"} and not part.is_empty]
    return shapely.union_all(parts) if parts else None


def overlap_pairs(frame: gpd.GeoDataFrame, tolerance_m2: float = 0.01):
    pairs = frame.sindex.query(frame.geometry, predicate="intersects")
    left, right = pairs
    keep = left < right
    left, right = left[keep], right[keep]
    area = np.zeros(len(left), dtype="float64")
    for start in range(0, len(left), 20_000):
        end = min(start + 20_000, len(left))
        area[start:end] = shapely.area(shapely.intersection(frame.geometry.array.take(left[start:end]), frame.geometry.array.take(right[start:end])))
    keep = area > tolerance_m2
    return left[keep], right[keep]


class UnionFind:
    def __init__(self, size: int): self.parent = list(range(size))
    def find(self, item: int):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item
    def union(self, left: int, right: int):
        left, right = self.find(left), self.find(right)
        if left != right: self.parent[right] = left


def clip_distinct_ids(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Erase overlaps deterministically while keeping one rendered geometry per ID."""
    left, right = overlap_pairs(frame)
    print(f"Sobreposições entre IDs distintos antes do auto-clip: {len(left):,}")
    if not len(left):
        return frame
    uf = UnionFind(len(frame)); neighbours: dict[int, set[int]] = {}
    for a, b in zip(left, right):
        a, b = int(a), int(b); uf.union(a, b)
        neighbours.setdefault(a, set()).add(b); neighbours.setdefault(b, set()).add(a)
    groups: dict[int, list[int]] = {}
    for item in neighbours: groups.setdefault(uf.find(item), []).append(item)
    source_priority = frame["tem_sigef"].map({True: 0, False: 1}).to_numpy()
    area = frame.geometry.area.to_numpy(dtype="float64")
    geometries = list(frame.geometry.array)
    for members in groups.values():
        # SIGEF wins over SNCI. For equal authority, retain the smaller/more
        # specific property and erase its footprint from the broader one.
        ordered = sorted(members, key=lambda item: (source_priority[item], area[item], str(frame.iloc[item]["id_apresentacao"])))
        complete: set[int] = set()
        for item in ordered:
            blockers = [geometries[other] for other in neighbours[item] if other in complete and geometries[other] is not None]
            if blockers:
                geometries[item] = polygonal(shapely.difference(geometries[item], shapely.union_all(blockers)))
            complete.add(item)
    result = frame.copy(); result["geometry"] = geometries
    return result[result.geometry.notna() & ~result.geometry.is_empty].copy()


def weighted_score(group: pd.DataFrame, field: str) -> float | None:
    values = pd.to_numeric(group[field], errors="coerce")
    valid = values.notna()
    if not valid.any():
        return None
    return float(np.average(values[valid], weights=group.loc[valid, "_weight_m2"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lookup", type=Path, required=True)
    args = parser.parse_args()

    columns = ["id_fazenda", "fonte_geometria", "identificador_incra", "identificador_oficial_poligono", *SCORE_FIELDS, "geometry"]
    master = gpd.read_parquet(args.input, columns=columns)
    if master.crs is None or (~master.geometry.is_valid).any() or master.geometry.is_empty.any():
        raise ValueError("Base-mãe inválida: nenhuma camada de apresentação foi criada.")

    sigef = master[master.fonte_geometria.eq("SIGEF")].copy()
    snci = master[master.fonte_geometria.eq("SNCI complementar")].copy()

    # 1) SNCI complements SIGEF only: erase every area already covered by a
    # SIGEF geometry and retain solely its extra, non-overlapping portions.
    sigef_index = sigef.sindex
    snci_extra = []
    for row in snci.itertuples():
        geometry = row.geometry
        candidates = list(sigef_index.query(geometry, predicate="intersects"))
        if candidates:
            geometry = polygonal(shapely.difference(geometry, shapely.union_all(list(sigef.geometry.iloc[candidates]))))
        if geometry is not None and not geometry.is_empty:
            record = row._asdict(); record["geometry"] = geometry; snci_extra.append(record)
    snci_extra = gpd.GeoDataFrame(snci_extra, geometry="geometry", crs=master.crs)
    combined = pd.concat([sigef, snci_extra], ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs=master.crs)
    combined["_weight_m2"] = combined.geometry.area
    incra = combined["identificador_incra"].fillna("").astype(str).str.strip()
    parcel = combined["identificador_oficial_poligono"].fillna("").astype(str).str.strip()
    # ``id_fazenda`` is only a fallback for older records with no official
    # parcel code.  The presentation ID is otherwise INCRA + parcel, never
    # just INCRA, so adjacent parcels of one property remain independently
    # clickable features.
    parcel = parcel.where(parcel.ne(""), combined["id_fazenda"].astype(str))
    combined["id_apresentacao"] = np.where(
        incra.ne(""), "INCRA-" + incra + "__PARCELA-" + parcel,
        "SEM-INCRA__PARCELA-" + parcel,
    )
    combined["identificador_parcela"] = parcel

    # 2) Merge only repeated parts of the *same parcel*. Different parcels in
    # one INCRA remain distinct features and must be clicked independently.
    records = []
    for visual_id, group in combined.groupby("id_apresentacao", sort=False):
        geometry = polygonal(shapely.union_all(list(group.geometry)))
        if geometry is None or geometry.is_empty:
            continue
        sources = set(group.fonte_geometria)
        record = {
            "id_apresentacao": visual_id,
            "id_fazenda": visual_id,
            "identificador_incra": group.identificador_incra.dropna().astype(str).iloc[0] if group.identificador_incra.notna().any() else None,
            "identificador_parcela": group.identificador_parcela.astype(str).iloc[0],
            "referencia_exibicao": (
                f"Parcela: {group.identificador_parcela.astype(str).iloc[0]} · INCRA: {group.identificador_incra.dropna().astype(str).iloc[0]}"
                if group.identificador_incra.notna().any()
                else f"Parcela certificada: {group.identificador_parcela.astype(str).iloc[0]}"
            ),
            "fonte_geometria": "SIGEF + SNCI complementar" if len(sources) > 1 else next(iter(sources)),
            "tem_sigef": "SIGEF" in sources,
            "geometry": geometry,
        }
        for field in SCORE_FIELDS:
            record[field] = weighted_score(group, field)
        records.append(record)
    presentation = gpd.GeoDataFrame(records, geometry="geometry", crs=master.crs)

    # 3) Different official IDs are made strictly disjoint for visual picking.
    presentation = clip_distinct_ids(presentation)
    presentation["area_ha"] = presentation.geometry.area / 10_000
    presentation["tipo"] = "Imóvel certificado — apresentação"
    presentation["geometria_apresentacao_regra"] = "SIGEF_prioritario;SNCI_so_extra;uniao_por_INCRA_e_parcela;auto_clip_entre_parcelas"

    left, right = overlap_pairs(presentation)
    if len(left):
        raise ValueError(f"QA falhou: {len(left)} sobreposições materiais após o auto-clip.")
    if presentation.id_apresentacao.duplicated().any():
        raise ValueError("QA falhou: IDs de apresentação duplicados.")

    output_columns = ["id_apresentacao", "id_fazenda", "identificador_incra", "identificador_parcela", "referencia_exibicao", "fonte_geometria", "tem_sigef", "area_ha", "tipo", "geometria_apresentacao_regra", *SCORE_FIELDS, "geometry"]
    presentation = presentation[output_columns]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    presentation.to_parquet(args.output, index=False)

    lookup_columns = ["id_fazenda", "referencia_exibicao", "area_ha", *SCORE_FIELDS]
    presentation[lookup_columns].to_parquet(args.lookup, index=False)
    print(f"Apresentação: {len(presentation):,} imóveis; SIGEF: {int(presentation.tem_sigef.sum()):,}; apenas SNCI: {int((~presentation.tem_sigef).sum()):,}")
    print("QA final: 0 sobreposições materiais entre geometrias de apresentação.")


if __name__ == "__main__":
    main()

"""Repair invalid certified-farm polygons without changing attributes or IDs."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "processed" / "fazendas_georeferenciadas.geoparquet"


def polygonal_only(geometry):
    """Keep only polygonal components emitted by make_valid()."""
    if geometry.is_empty:
        return geometry
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = []
        for part in geometry.geoms:
            if isinstance(part, Polygon):
                polygons.append(part)
            elif isinstance(part, MultiPolygon):
                polygons.extend(part.geoms)
        return unary_union(polygons) if polygons else geometry
    return geometry


def main() -> None:
    farms = gpd.read_parquet(PATH)
    invalid = ~farms.geometry.is_valid
    before = farms.loc[invalid, ["id_fazenda", "geometry"]].copy()
    if not invalid.any():
        print("Nenhuma geometria inválida encontrada.")
        return

    repaired = before.geometry.make_valid().map(polygonal_only)
    if (~repaired.is_valid).any() or repaired.is_empty.any():
        raise ValueError("Reparo recusado: uma geometria continuou inválida ou vazia.")
    area_delta = (repaired.area.to_numpy() - before.geometry.area.to_numpy())
    if abs(area_delta).max() > 0.01:
        raise ValueError("Reparo recusado: área poligonal foi alterada além da tolerância de 0,01 m².")

    farms.loc[invalid, "geometry"] = repaired
    temp = PATH.with_suffix(".repairing.parquet")
    farms.to_parquet(temp, index=False)
    check = gpd.read_parquet(temp, columns=["id_fazenda", "geometry"])
    if (~check.geometry.is_valid).any() or check.geometry.is_empty.any() or check.id_fazenda.duplicated().any():
        temp.unlink(missing_ok=True)
        raise ValueError("Verificação pós-gravação falhou; base original preservada.")
    temp.replace(PATH)
    print(f"Geometrias reparadas: {int(invalid.sum())}; atributos, IDs e área preservados.")


if __name__ == "__main__":
    main()

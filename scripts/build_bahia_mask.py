"""Dissolve the official IBGE municipal mesh into a Bahia boundary and mask."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon, mapping


WEB_CRS = "EPSG:4326"
WORLD_RING = [(-180, -85), (180, -85), (180, 85), (-180, 85), (-180, -85)]


def exterior_rings(geometry) -> list[list[tuple[float, float]]]:
    if isinstance(geometry, Polygon):
        return [list(geometry.exterior.coords)]
    if isinstance(geometry, MultiPolygon):
        return [list(part.exterior.coords) for part in geometry.geoms]
    raise TypeError("O limite dissolvido não é um polígono.")


def feature(geometry, properties: dict) -> dict:
    return {"type": "Feature", "properties": properties, "geometry": mapping(geometry)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--municipios", type=Path, required=True, help="Shapefile oficial de municípios da Bahia")
    parser.add_argument("--output", type=Path, default=Path("data/processed"))
    args = parser.parse_args()

    municipios = gpd.read_file(args.municipios)
    if municipios.crs is None:
        raise ValueError("A malha municipal não possui CRS definido.")
    geometry = municipios.to_crs(WEB_CRS).geometry.union_all()
    args.output.mkdir(parents=True, exist_ok=True)

    boundary = {"type": "FeatureCollection", "features": [feature(geometry, {"nome": "Bahia", "fonte": "IBGE 2024"})]}
    exterior_mask = Polygon(WORLD_RING, holes=exterior_rings(geometry))
    mask = {"type": "FeatureCollection", "features": [feature(exterior_mask, {"nome": "Fora da Bahia"})]}

    (args.output / "bahia_boundary.geojson").write_text(json.dumps(boundary), encoding="utf-8")
    (args.output / "bahia_exterior_mask.geojson").write_text(json.dumps(mask), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Build a continuous regular 5 km hexagonal mesh clipped to Bahia."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, Polygon
from shapely.prepared import prep


ANALYSIS_CRS = "EPSG:5880"  # SIRGAS 2000 / Brazil Polyconic
WEB_CRS = "EPSG:4326"
CENTER_DISTANCE_M = 5_000


def hexagon(center_x: float, center_y: float, side_m: float) -> Polygon:
    return Polygon(
        [
            (
                center_x + side_m * math.cos(math.radians(60 * index + 30)),
                center_y + side_m * math.sin(math.radians(60 * index + 30)),
            )
            for index in range(6)
        ]
    )


def build_grid(boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    boundary_geometry = boundary.geometry.union_all()
    prepared_boundary = prep(boundary_geometry)
    min_x, min_y, max_x, max_y = boundary_geometry.bounds
    side_m = CENTER_DISTANCE_M / math.sqrt(3)
    row_spacing_m = 1.5 * side_m
    cells: list[dict] = []
    row = 0
    center_y = min_y - side_m
    while center_y <= max_y + side_m:
        center_x = min_x - CENTER_DISTANCE_M + (CENTER_DISTANCE_M / 2 if row % 2 else 0)
        while center_x <= max_x + CENTER_DISTANCE_M:
            # Keep full regular hexagons. The WebGIS exterior mask visually clips
            # the small parts that extend beyond the official state boundary.
            if prepared_boundary.covers(Point(center_x, center_y)):
                geometry = hexagon(center_x, center_y, side_m)
                area_bahia_ha = (
                    geometry.area / 10_000
                    if prepared_boundary.contains(geometry)
                    else geometry.intersection(boundary_geometry).area / 10_000
                )
                cells.append(
                    {
                        "id_hex": f"HEX-{row:04d}-{len(cells) + 1:06d}",
                        "area_bahia_ha": round(area_bahia_ha, 2),
                        "geometry": geometry,
                    }
                )
            center_x += CENTER_DISTANCE_M
        center_y += row_spacing_m
        row += 1
    grid = gpd.GeoDataFrame(cells, crs=ANALYSIS_CRS)
    grid["area_ha"] = grid["area_bahia_ha"]
    return grid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary", type=Path, required=True, help="Limite estadual da Bahia em GeoJSON")
    parser.add_argument("--output", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    args = parser.parse_args()

    boundary = gpd.read_file(args.boundary)
    if boundary.crs is None:
        raise ValueError("O limite estadual não possui CRS definido.")
    grid = build_grid(boundary.to_crs(ANALYSIS_CRS)).to_crs(WEB_CRS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    grid.to_file(args.output, driver="GeoJSON")
    print(f"Hexágonos criados: {len(grid):,}")


if __name__ == "__main__":
    main()

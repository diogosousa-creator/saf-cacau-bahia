"""Create the two spatial units used by the WebGIS.

Inputs are local extracts from official sources.  This script deliberately does
not download sources itself: every extract must be registered in the data
catalogue before it is processed.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from shapely.ops import unary_union


ANALYSIS_CRS = "EPSG:5880"  # SIRGAS 2000 / Brazil Polyconic
WEB_CRS = "EPSG:4326"
MIN_SNCI_REMAINDER_HA = 1.0


def read_layer(path: Path) -> gpd.GeoDataFrame:
    layer = gpd.read_file(path)
    if layer.crs is None:
        raise ValueError(f"{path} não possui CRS definido.")
    layer = layer.to_crs(ANALYSIS_CRS)
    layer = layer[layer.geometry.notna()].copy()
    layer["geometry"] = layer.geometry.make_valid()
    return layer[~layer.geometry.is_empty].copy()


def hexagon(cx: float, cy: float, side: float) -> Polygon:
    return Polygon(
        [(cx + side * math.cos(math.radians(60 * i + 30)), cy + side * math.sin(math.radians(60 * i + 30))) for i in range(6)]
    )


def build_hexagons(boundary: gpd.GeoDataFrame, center_distance_m: float = 5000) -> gpd.GeoDataFrame:
    """Build regular flat-top hexagons with about 5 km between adjacent centers."""
    geom = unary_union(boundary.geometry)
    minx, miny, maxx, maxy = geom.bounds
    side = center_distance_m / math.sqrt(3)
    dx = center_distance_m
    dy = 1.5 * side
    cells: list[dict] = []
    row = 0
    y = miny - side
    while y <= maxy + side:
        x = minx - center_distance_m + (center_distance_m / 2 if row % 2 else 0)
        while x <= maxx + center_distance_m:
            candidate = hexagon(x, y, side)
            clipped = candidate.intersection(geom)
            if not clipped.is_empty and clipped.area > 0:
                cells.append({"id_hex": f"HEX-{row:04d}-{len(cells):06d}", "geometry": clipped})
            x += dx
        y += dy
        row += 1
    return gpd.GeoDataFrame(cells, crs=ANALYSIS_CRS)


def build_certified_properties(sigef: gpd.GeoDataFrame, snci: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """SIGEF plus the non-overlapping residual part of SNCI.

    Original geometry is never changed at source.  ``geometry`` represents the
    exclusive analytic footprint used to avoid double counting.
    """
    sigef = sigef.copy()
    sigef["fonte_geometria"] = "SIGEF"
    sigef["area_analitica_ha"] = sigef.geometry.area / 10_000

    sigef_union = unary_union(sigef.geometry)
    snci = snci.copy()
    snci["geometry"] = snci.geometry.apply(lambda geometry: geometry.difference(sigef_union))
    snci = snci[~snci.geometry.is_empty].copy()
    snci["area_analitica_ha"] = snci.geometry.area / 10_000
    snci = snci[snci["area_analitica_ha"] >= MIN_SNCI_REMAINDER_HA].copy()
    snci["fonte_geometria"] = "SNCI_COMPLEMENTAR"

    common = sorted(set(sigef.columns).union(snci.columns))
    for column in common:
        if column not in sigef:
            sigef[column] = pd.NA
        if column not in snci:
            snci[column] = pd.NA
    combined = pd.concat([sigef[common], snci[common]], ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs=ANALYSIS_CRS)
    combined["id_imovel"] = [f"IC-{index:08d}" for index in range(1, len(combined) + 1)]
    return combined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bahia", type=Path, required=True, help="Limite oficial da Bahia")
    parser.add_argument("--sigef", type=Path, required=True, help="Extrato SIGEF")
    parser.add_argument("--snci", type=Path, required=True, help="Extrato SNCI")
    parser.add_argument("--output", type=Path, default=Path("data/processed"))
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    bahia = read_layer(args.bahia)
    sigef = read_layer(args.sigef)
    snci = read_layer(args.snci)

    build_hexagons(bahia).to_crs(WEB_CRS).to_parquet(args.output / "hexagonos_bahia_5km.geoparquet")
    build_certified_properties(sigef, snci).to_crs(WEB_CRS).to_parquet(args.output / "imoveis_certificados.geoparquet")


if __name__ == "__main__":
    main()

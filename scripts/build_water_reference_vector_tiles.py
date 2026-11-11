"""Create progressive, display-only MVT layers for the water evidence map.

The analytical score remains pre-calculated in the two master datasets.  These
tiles are deliberately reference layers for the individual map only: MapBiomas
Água is polygonised per web tile, BHO6 is rendered as a simplified network and
SIAGAS as points.  Nothing here is loaded by the main aptitude map.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.transform import Affine
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform
from pyproj import Transformer


def encode(features: list[dict], bounds: tuple[float, float, float, float], name: str) -> bytes:
    return mapbox_vector_tile.encode(
        {"name": name, "features": features},
        default_options={"quantize_bounds": bounds, "extents": 4096},
    )


def write_tile(output: Path, group: str, tile: mercantile.Tile, payload: bytes) -> None:
    dest = output / group / str(tile.z) / str(tile.x)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{tile.y}.pbf").write_bytes(payload)


def simplify_wgs84(geom, tolerance_m: float):
    """Simplify in metres, retaining valid geometry for a compact MVT."""
    if tolerance_m <= 0:
        return geom
    series = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs("EPSG:3857")
    result = series.simplify(tolerance_m, preserve_topology=True).to_crs("EPSG:4326").iloc[0]
    return result if result.is_valid else geom


def build_bho(bho_path: Path, output: Path, minzoom: int, maxzoom: int) -> int:
    # Transform once.  Doing a GeoSeries CRS conversion per feature makes the
    # statewide drainage network unnecessarily slow.
    bho = gpd.read_parquet(bho_path).to_crs("EPSG:3857")
    bho = bho[bho.geometry.notna() & ~bho.geometry.is_empty].copy()
    index = bho.sindex
    to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform
    to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
    bounds_wgs84 = gpd.GeoSeries(bho.geometry, crs="EPSG:3857").to_crs("EPSG:4326").total_bounds
    count = 0
    for z in range(minzoom, maxzoom + 1):
        tolerance_m = {5: 550, 6: 260, 7: 100, 8: 35, 9: 12}.get(z, 8)
        for tile in mercantile.tiles(*bounds_wgs84, zooms=[z]):
            b = mercantile.bounds(tile)
            clip = shp_transform(to_3857, box(b.west, b.south, b.east, b.north))
            positions = list(index.query(clip, predicate="intersects"))
            if not positions:
                continue
            subset = bho.iloc[positions]
            features = []
            for row in subset.itertuples(index=False):
                geom = row.geometry.intersection(clip).simplify(tolerance_m, preserve_topology=True)
                if geom.is_empty:
                    continue
                geom = shp_transform(to_wgs84, geom)
                order = int(getattr(row, "nustrahler", 0) or 0)
                features.append({"geometry": mapping(geom), "properties": {
                    "nome": str(getattr(row, "noespecif", "") or getattr(row, "nogenerico", "") or "Trecho BHO6"),
                    "cotrecho": str(getattr(row, "cotrecho", "") or ""),
                    "ordem_strahler": order,
                    "area_montante_km2": round(float(getattr(row, "nuareamont", 0) or 0), 2),
                    "largura_px": min(4.5, 0.55 + order * 0.38),
                }})
            if features:
                write_tile(output, "bho6", tile, encode(features, (b.west, b.south, b.east, b.north), "bho6"))
                count += 1
    return count


def build_siagas(siagas_path: Path, output: Path, minzoom: int, maxzoom: int) -> int:
    wells = gpd.read_parquet(siagas_path).to_crs("EPSG:4326")
    wells = wells[wells.geometry.notna() & ~wells.geometry.is_empty].copy()
    wells["vazao_especifica"] = pd.to_numeric(wells.get("num_vazao_especifica"), errors="coerce")
    # Visual hierarchy only: no supplied productivity is rendered neutrally,
    # rather than being interpreted as an unproductive well.
    wells["ponto_tamanho"] = wells["vazao_especifica"].map(
        lambda x: 3.0 if pd.isna(x) or x <= 0 else min(13.0, 3.0 + 2.0 * math.log1p(float(x)))
    )
    index = wells.sindex
    count = 0
    for z in range(minzoom, maxzoom + 1):
        for tile in mercantile.tiles(*wells.total_bounds, zooms=[z]):
            b = mercantile.bounds(tile)
            clip = box(b.west, b.south, b.east, b.north)
            positions = list(index.query(clip, predicate="intersects"))
            if not positions:
                continue
            features = []
            for row in wells.iloc[positions].itertuples(index=False):
                ve = getattr(row, "vazao_especifica", np.nan)
                features.append({"geometry": mapping(row.geometry), "properties": {
                    "idt_ponto": str(getattr(row, "idt_ponto", "") or ""),
                    "municipio": str(getattr(row, "str_municipio", "") or "Não informado"),
                    "aquifero": str(getattr(row, "str_aquifero", "") or "Não informado"),
                    "uso_agua": str(getattr(row, "str_uso_agua", "") or "Não informado"),
                    "profundidade_m": None if pd.isna(getattr(row, "num_profundidade", np.nan)) else round(float(getattr(row, "num_profundidade")), 1),
                    "vazao_especifica": None if pd.isna(ve) else round(float(ve), 3),
                    "ponto_tamanho": round(float(getattr(row, "ponto_tamanho")), 1),
                }})
            if features:
                write_tile(output, "siagas", tile, encode(features, (b.west, b.south, b.east, b.north), "siagas"))
                count += 1
    return count


def _resampled_transform(window: Window, src_transform: Affine, width: int, height: int) -> Affine:
    base = rasterio.windows.transform(window, src_transform)
    return base * Affine.scale(window.width / width, window.height / height)


def build_mapbiomas_water(raster_path: Path, output: Path, minzoom: int, maxzoom: int) -> int:
    count = 0
    with rasterio.open(raster_path) as src:
        if src.crs is None or src.crs.to_epsg() != 4326:
            raise ValueError("O GeoTIFF MapBiomas deve estar em EPSG:4326.")
        left, bottom, right, top = src.bounds
        for z in range(minzoom, maxzoom + 1):
            # 512 px at low zoom and 1024 px at close zoom preserves the 30 m
            # source where it is legible, without creating a statewide polygon soup.
            max_dim = 512 if z <= 6 else 1024
            tolerance_m = {5: 450, 6: 200, 7: 80, 8: 30, 9: 8}.get(z, 5)
            for tile in mercantile.tiles(left, bottom, right, top, zooms=[z]):
                b = mercantile.bounds(tile)
                win = from_bounds(b.west, b.south, b.east, b.north, transform=src.transform)
                full = Window(0, 0, src.width, src.height)
                win = win.round_offsets().round_lengths().intersection(full)
                if win.width <= 0 or win.height <= 0:
                    continue
                out_h, out_w = min(max_dim, int(win.height)), min(max_dim, int(win.width))
                transform = _resampled_transform(win, src.transform, out_w, out_h)
                # Bands 2 and 3 from the validated local GEE export: persistent
                # and seasonal water, 2021–2025.  Persistent is displayed above seasonal.
                # Rasterio supports nearest-neighbour for direct reads.  This is
                # a visual generalisation only; score statistics were calculated
                # separately on the original 30 m grid.
                persistent = src.read(2, window=win, out_shape=(out_h, out_w), resampling=Resampling.nearest) > 0
                seasonal = src.read(3, window=win, out_shape=(out_h, out_w), resampling=Resampling.nearest) > 0
                seasonal &= ~persistent
                for label, mask, group in (
                    ("Água sazonal MapBiomas (2021–2025)", seasonal, "mapbiomas_sazonal"),
                    ("Água persistente MapBiomas (2021–2025)", persistent, "mapbiomas_persistente"),
                ):
                    if not mask.any():
                        continue
                    features = []
                    for geom_json, _ in shapes(mask.astype("uint8"), mask=mask, transform=transform):
                        geom = simplify_wgs84(shape(geom_json), tolerance_m)
                        if geom.is_empty:
                            continue
                        features.append({"geometry": mapping(geom), "properties": {"classe": label}})
                    if features:
                        write_tile(output, group, tile, encode(features, (b.west, b.south, b.east, b.north), group))
                        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bho", type=Path, default=Path("data/raw/hidrica/bho6_bahia_ordem4mais.geoparquet"))
    parser.add_argument("--siagas", type=Path, default=Path("data/raw/hidrica/siagas_bahia_pocos.geoparquet"))
    parser.add_argument("--water", type=Path, default=Path("data/raw/hidrica/saf_cacau_bahia_mapbiomas_agua_c5_1985_2025.tif"))
    parser.add_argument("--output", type=Path, default=Path("static/hidrica_referencias_tiles_v3"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print("BHO6:", build_bho(args.bho, args.output, 5, 9), "tiles")
    print("SIAGAS:", build_siagas(args.siagas, args.output, 7, 9), "tiles")
    print("MapBiomas Água:", build_mapbiomas_water(args.water, args.output, 5, 9), "tiles")


if __name__ == "__main__":
    main()

"""Tiles do mapa temático "Uso atual do solo".

* ``static/hex_uso_solo_tiles_v1/score/{1..5}``: hexágonos de 5 km pela nota
  (classes 35/50/65/80), com tooltip em linhas ``t0…t6``.
* ``static/uso_solo_referencia_tiles_v5/{grupo}``: grupo de uso dominante do MapBiomas
  (referência visual; não altera a nota), vetorial como o PRODES.  Os polígonos são
  vetorizados em blocos de 32×32 células, de modo que nenhum polígono atravessa o
  estado com milhares de buracos (o deck.gl descartava tiles que não conseguia
  triangular).  Zoom 4: 7,7 km; 5: 3,8 km; 6: 1,9 km; 7: 960 m; 8: 480 m; 9: 240 m (ampliado acima disso).
  Cada tile fica abaixo de 65.535 vértices: acima disso o deck.gl pinta o tile de preto.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes
from shapely.geometry import box, mapping, shape

from add_tooltip_text_tiles import num, tooltip_properties

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
DISPLAY = ROOT / "data" / "interim" / "uso_solo_grupo_bahia_240m.tif"
HEX_OUT = ROOT / "static" / "hex_uso_solo_tiles_v1"
REF_OUT = ROOT / "static" / "uso_solo_referencia_v1"
REF_TILES = ROOT / "static" / "uso_solo_referencia_tiles_v5"
BLOCK = 32
LEVELS = {4: 32, 5: 16, 6: 8, 7: 4, 8: 2, 9: 1}   # zoom -> fator sobre 240 m; cada tile < 65.535 vértices (limite do deck.gl)
GROUP_FOLDERS = {1: "pastagem", 2: "mosaico", 3: "temporaria", 4: "perene", 5: "nativa", 6: "nao_aproveitavel"}
GROUP_NAMES = {1: "Pastagem", 2: "Mosaico de usos", 3: "Lavoura temporária", 4: "Lavoura perene / silvicultura",
               5: "Vegetação nativa", 6: "Água, área urbana, rocha ou mineração"}


def score_class(value: float) -> int:
    if pd.isna(value):
        return 0
    return 5 if value >= 80 else 4 if value >= 65 else 3 if value >= 50 else 2 if value >= 35 else 1


def hex_tooltip(row) -> str:
    if pd.isna(row.uso_solo):
        return "<b>Uso atual do solo</b><br/>Sem área aproveitável mapeada"
    return ("<b>Uso atual do solo</b>"
            f"<br/>Nota: {num(row.uso_solo)}/100 · {row.classe_uso_solo}"
            f"<br/>Pastagem: {num(row.uso_pastagem_pct, 0)}%"
            f"<br/>Mosaico de usos: {num(row.uso_mosaico_pct, 0)}%"
            f"<br/>Lavoura temporária: {num(row.uso_temporaria_pct, 0)}%"
            f"<br/>Lavoura perene / silvicultura: {num(row.uso_perene_pct, 0)}%"
            f"<br/>Vegetação nativa: {num(row.uso_nativa_pct, 0)}%")


def write_tile(folder: Path, tile, features: list[dict], bounds, layer: str) -> None:
    if not features:
        return
    dest = folder / str(tile.z) / str(tile.x)
    dest.mkdir(parents=True, exist_ok=True)
    encoded = mapbox_vector_tile.encode({"name": layer, "features": features},
                                        default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north),
                                                         "on_invalid_geometry": mapbox_vector_tile.encoder.on_invalid_geometry_make_valid})
    (dest / f"{tile.y}.pbf").write_bytes(encoded)


def build_hex() -> int:
    columns = ["id", "uso_solo", "classe_uso_solo", *[f"uso_{g}_pct" for g in ("pastagem", "mosaico", "temporaria", "perene", "nativa")]]
    hexes = gpd.read_file(PROCESSED / "bahia_hex_5km.geojson").to_crs("EPSG:4326")
    hexes = hexes.merge(pd.read_parquet(PROCESSED / "assessments.parquet", columns=columns), left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    hexes["cat"] = hexes["uso_solo"].map(score_class)
    hexes["props"] = [tooltip_properties(hex_tooltip(r)) for r in hexes.itertuples(index=False)]
    index, made = hexes.sindex, 0
    for zoom in range(4, 8):
        tolerance_m = min(78_271.5 / (2 ** zoom), 150)   # keeps the 6 vertices of each hexagon
        for tile in mercantile.tiles(*hexes.total_bounds, zooms=[zoom]):
            b = mercantile.bounds(tile)
            clip = box(b.west, b.south, b.east, b.north)
            pos = list(index.query(clip, predicate="intersects"))
            if not pos:
                continue
            sub = hexes.iloc[pos].copy()
            sub["geometry"] = sub.geometry.intersection(clip)
            sub = sub[~sub.geometry.is_empty].to_crs("EPSG:3857")
            sub["geometry"] = sub.geometry.simplify(tolerance_m, preserve_topology=True)
            sub = sub.to_crs("EPSG:4326")
            for cat in range(1, 6):
                part = sub[sub["cat"].eq(cat)]
                write_tile(HEX_OUT / "score" / str(cat), tile,
                           [{"geometry": mapping(r.geometry), "properties": r.props} for r in part.itertuples(index=False)], b, "uso_solo_hex")
                made += int(len(part) > 0)
    return made


PALETTE = {0: (0, 0, 0, 0), 1: (237, 222, 142, 215), 2: (255, 239, 195, 215), 3: (233, 116, 237, 205),
           4: (41, 98, 196, 205), 5: (31, 141, 73, 205), 6: (150, 160, 170, 200)}


def build_reference_image() -> str:
    """Reference layer as one indexed PNG (240 m, Web Mercator) for a BitmapLayer.

    Vector polygons of the dominant-use raster reached ~20k vertices and
    thousands of holes; deck.gl silently dropped tiles it could not
    triangulate.  A single image has no such failure mode and weighs ~1.3 MB
    for the whole state.  Bounds (lon/lat) are saved next to it.
    """
    import json
    from PIL import Image
    from pyproj import Transformer
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    with rasterio.open(DISPLAY) as src:
        transform, width, height = calculate_default_transform(src.crs, "EPSG:3857", src.width, src.height, *src.bounds)
        image = np.zeros((height, width), dtype=np.uint8)
        reproject(rasterio.band(src, 1), image, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs="EPSG:3857", resampling=Resampling.nearest)
    png = Image.fromarray(image)
    png.putpalette([c for k in range(7) for c in PALETTE[k][:3]] + [0] * (768 - 21))
    alpha = bytes([PALETTE[k][3] for k in range(7)] + [0] * 249)
    REF_OUT.mkdir(parents=True, exist_ok=True)
    png.save(REF_OUT / "uso_dominante_240m.png", optimize=True, transparency=alpha)
    to_lonlat = Transformer.from_crs(3857, 4326, always_xy=True)
    west, south = to_lonlat.transform(transform.c, transform.f + transform.e * height)
    east, north = to_lonlat.transform(transform.c + transform.a * width, transform.f)
    (REF_OUT / "uso_dominante_240m.json").write_text(json.dumps({"bounds": [west, south, east, north]}))
    return f"{width}×{height} px"


def _mode(data: np.ndarray, factor: int) -> np.ndarray:
    """Grupo dominante em células de factor×factor (0 = sem dado)."""
    if factor == 1:
        return data
    h, w = data.shape[0] // factor * factor, data.shape[1] // factor * factor
    g = data[:h, :w].reshape(h // factor, factor, w // factor, factor)
    counts = np.stack([(g == k).sum(axis=(1, 3)) for k in range(1, 7)])
    return np.where(counts.sum(axis=0) > 0, counts.argmax(axis=0) + 1, 0).astype(np.uint8)


def _block_polygons(data: np.ndarray, transform) -> gpd.GeoDataFrame:
    rows = []
    for r in range(0, data.shape[0], BLOCK):
        for c in range(0, data.shape[1], BLOCK):
            block = data[r:r + BLOCK, c:c + BLOCK]
            if not block.any():
                continue
            bt = transform * transform.translation(c, r)
            for geom, value in shapes(block, mask=block > 0, transform=bt, connectivity=4):
                rows.append((int(value), shape(geom)))
    return gpd.GeoDataFrame({"g": [v for v, _ in rows]}, geometry=[g for _, g in rows], crs="EPSG:4326")


def build_reference_tiles() -> int:
    with rasterio.open(DISPLAY) as src:
        base, base_transform = src.read(1), src.transform
    made = 0
    for factor in sorted(set(LEVELS.values()), reverse=True):
        data = _mode(base, factor)
        polys = _block_polygons(data, base_transform * base_transform.scale(factor, factor))
        index = polys.sindex
        print(f"  {240 * factor} m: {len(polys):,} polígonos", flush=True)
        for zoom in [z for z, f in LEVELS.items() if f == factor]:
            for tile in mercantile.tiles(*polys.total_bounds, zooms=[zoom]):
                b = mercantile.bounds(tile)
                clip = box(b.west, b.south, b.east, b.north)
                pos = index.query(clip, predicate="intersects")
                if not len(pos):
                    continue
                sub = polys.iloc[pos].copy()
                sub["geometry"] = sub.geometry.intersection(clip)
                sub = sub[~sub.geometry.is_empty].explode(index_parts=False)
                sub = sub[sub.geometry.geom_type.eq("Polygon")]
                for group, folder in GROUP_FOLDERS.items():
                    part = sub[sub["g"].eq(group)]
                    write_tile(REF_TILES / folder, tile, [{"geometry": mapping(g), "properties": {"uso": folder}} for g in part.geometry], b, folder)
                    made += int(len(part) > 0)
    return made


if __name__ == "__main__":
    import sys
    if "--only-reference" not in sys.argv:
        print(f"Hexágonos uso do solo: {build_hex():,} tiles", flush=True)
    if "--only-reference" in sys.argv or "--png" in sys.argv:
        print(f"Referência MapBiomas 240 m (PNG): {build_reference_image()}", flush=True)
    print(f"Referência MapBiomas vetorial: {build_reference_tiles():,} tiles", flush=True)

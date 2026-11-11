"""Área PRODES por período em hexágonos e imóveis, direto do raster 30 m.

Lê o mosaico gerado por ``build_prodes_raster_bahia.py`` em blocos e soma a
área real de cada pixel (corrigida pela latitude, elipsoide GRS80) em cada
unidade.  Pixels são atribuídos pelo centro (``all_touched=False``).

Imóveis sobrepostos são rasterizados em camadas sem sobreposição: cada imóvel
recebe todos os seus pixels, inclusive os que divide com um vizinho.

Saídas
------
* ``prodes_raster_hex_stats.parquet`` e ``prodes_raster_fazendas_stats.parquet``
* ``prodes_periodo_bahia_240m.tif``: raster 8× mais grosso, só para a camada de
  referência visual do mapa (classe do período dominante na célula).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, bounds as window_bounds, transform as window_transform
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
MOSAIC = ROOT / "data" / "interim" / "prodes_bahia_30m.tif"
PROCESSED = ROOT / "data" / "processed"

# Grupos analíticos a partir do código unificado (ano − 2000).
GROUPS = ["pre2008", "2009_2020", "post2020", "vegetacao_nativa", "hidrografia", "nao_observado", "outros"]
LUT = np.zeros(256, dtype=np.uint8)          # 0 = sem dado (255)
LUT[0:9] = 1                                  # d2000…d2008 (acumulado até 2000 + 2001–2008)
LUT[9:21] = 2                                 # 2009–2020
LUT[21:41] = 3                                # 2021 em diante
LUT[91] = 5
LUT[99] = 6
LUT[100] = 4
LUT[101] = 7
NGROUP = len(GROUPS) + 1
DISPLAY_FACTOR = 8


def pixel_area_ha(transform, row_off: int, rows: int) -> np.ndarray:
    """Area (ha) of one pixel for each row, GRS80 ellipsoid."""
    a, e2 = 6378137.0, 0.00669438002290
    lat = np.radians(transform.f + transform.e * (row_off + np.arange(rows) + 0.5))
    res = np.radians(abs(transform.a))
    sin2 = np.sin(lat) ** 2
    m = a * (1 - e2) / (1 - e2 * sin2) ** 1.5          # meridional radius
    n = a / np.sqrt(1 - e2 * sin2)                       # prime vertical radius
    return (res * m) * (res * n * np.cos(lat)) / 10_000


def layers_without_overlap(units: gpd.GeoDataFrame) -> np.ndarray:
    """Greedy colouring so overlapping units never share a rasterization layer."""
    metric = units.geometry.to_crs("EPSG:5880")
    a, b = metric.sindex.query(metric, predicate="intersects")
    keep = a < b
    a, b = a[keep], b[keep]
    if len(a):
        area = metric.values[a].intersection(metric.values[b]).area
        a, b = a[area > 0], b[area > 0]
    adjacency: dict[int, set[int]] = defaultdict(set)
    for i, j in zip(a, b):
        adjacency[i].add(j)
        adjacency[j].add(i)
    layer = np.zeros(len(units), dtype=np.int16)
    for i in sorted(adjacency, key=lambda k: -len(adjacency[k])):
        used = {layer[j] for j in adjacency[i]}
        c = 1
        while c in used:
            c += 1
        layer[i] = c
    layer[layer == 0] = 1
    for i, neigh in adjacency.items():
        assert all(layer[j] != layer[i] for j in neigh), "camadas com sobreposição"
    return layer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mosaic", type=Path, default=MOSAIC)
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    parser.add_argument("--block", type=int, default=4096)
    args = parser.parse_args()

    src = rasterio.open(args.mosaic)
    hexes = gpd.read_file(args.processed / "bahia_hex_5km.geojson")[["id_hex", "geometry"]].to_crs(src.crs)
    farms = gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet", columns=["id_fazenda", "geometry"]).to_crs(src.crs)
    farms["geometry"] = farms.geometry.make_valid()
    hexes["geometry"] = hexes.geometry.make_valid()
    farm_layer = layers_without_overlap(farms)
    print(f"Hexágonos: {len(hexes):,} | imóveis: {len(farms):,} em {farm_layer.max()} camadas sem sobreposição", flush=True)

    unit_sets = [("hex", hexes, np.ones(len(hexes), dtype=np.int16)), ("farm", farms, farm_layer)]
    sums = {name: np.zeros((len(frame) + 1, NGROUP), dtype=np.float64) for name, frame, _ in unit_sets}
    index = {name: frame.sindex for name, frame, _ in unit_sets}

    dh = int(np.ceil(src.height / DISPLAY_FACTOR))
    dw = int(np.ceil(src.width / DISPLAY_FACTOR))
    display = np.zeros((dh, dw), dtype=np.uint8)

    blocks = [(r, c) for r in range(0, src.height, args.block) for c in range(0, src.width, args.block)]
    for n, (row_off, col_off) in enumerate(blocks, 1):
        rows = min(args.block, src.height - row_off)
        cols = min(args.block, src.width - col_off)
        window = Window(col_off, row_off, cols, rows)
        data = src.read(1, window=window)
        group = LUT[data]
        if not group.any():
            continue
        area = np.broadcast_to(pixel_area_ha(src.transform, row_off, rows)[:, None], (rows, cols))
        wtransform = window_transform(window, src.transform)
        tile_box = box(*window_bounds(window, src.transform))

        # Visual reference: dominant period in each 8×8 cell (≥50% deforested).
        r8, c8 = rows // DISPLAY_FACTOR * DISPLAY_FACTOR, cols // DISPLAY_FACTOR * DISPLAY_FACTOR
        if r8 and c8:
            g = group[:r8, :c8].reshape(r8 // DISPLAY_FACTOR, DISPLAY_FACTOR, c8 // DISPLAY_FACTOR, DISPLAY_FACTOR)
            counts = np.stack([(g == k).sum(axis=(1, 3)) for k in (1, 2, 3)], axis=0)
            valid = (g > 0).sum(axis=(1, 3))
            deforested = counts.sum(axis=0)
            cls = np.where((valid > 0) & (deforested * 2 >= valid), counts.argmax(axis=0) + 1, 0).astype(np.uint8)
            dr, dc = row_off // DISPLAY_FACTOR, col_off // DISPLAY_FACTOR
            display[dr:dr + cls.shape[0], dc:dc + cls.shape[1]] = cls

        mask = group > 0
        flat_group = group[mask].astype(np.int64)
        flat_area = area[mask]
        for name, frame, layer in unit_sets:
            candidates = index[name].query(tile_box, predicate="intersects")
            if candidates.size == 0:
                continue
            for lay in np.unique(layer[candidates]):
                sel = candidates[layer[candidates] == lay]
                owner = rasterize(((frame.geometry.iloc[i], int(i) + 1) for i in sel), out_shape=(rows, cols),
                                  transform=wtransform, fill=0, dtype="int32", all_touched=False)
                o = owner[mask].astype(np.int64)
                hit = o > 0
                if hit.any():
                    flat = np.bincount(o[hit] * NGROUP + flat_group[hit], weights=flat_area[hit],
                                       minlength=sums[name].size)
                    sums[name] += flat.reshape(sums[name].shape)
        if n % 25 == 0 or n == len(blocks):
            print(f"  blocos {n}/{len(blocks)}", flush=True)

    for name, frame, _ in unit_sets:
        id_field = "id_hex" if name == "hex" else "id_fazenda"
        out = pd.DataFrame({id_field: frame[id_field].astype(str).to_numpy()})
        for k, label in enumerate(GROUPS, start=1):
            out[f"prodes_{label}_ha"] = sums[name][1:, k].round(4)
        out["prodes_area_mapeada_ha"] = sums[name][1:, 1:].sum(axis=1).round(4)
        target = args.processed / ("prodes_raster_hex_stats.parquet" if name == "hex" else "prodes_raster_fazendas_stats.parquet")
        out.to_parquet(target, index=False)
        print(f"{target.name}: {len(out):,} unidades; com área mapeada: {(out.prodes_area_mapeada_ha > 0).sum():,}")

    profile = src.profile.copy()
    profile.update(width=dw, height=dh, transform=src.transform * src.transform.scale(DISPLAY_FACTOR, DISPLAY_FACTOR),
                   nodata=0, BIGTIFF="NO")
    with rasterio.open(ROOT / "data" / "interim" / "prodes_periodo_bahia_240m.tif", "w", **profile) as dst:
        dst.write(display, 1)
    src.close()


if __name__ == "__main__":
    main()

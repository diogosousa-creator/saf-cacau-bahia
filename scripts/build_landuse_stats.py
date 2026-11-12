"""Área por grupo de uso do solo (MapBiomas C11, 2025) em hexágonos e imóveis.

Mesmo método do PRODES em raster: leitura em blocos, área real de cada pixel
(elipsoide GRS80), atribuição pelo centro do pixel e imóveis sobrepostos em
camadas sem sobreposição (cada imóvel recebe todos os seus pixels).

Grupos (códigos da legenda oficial da Coleção 11)
------------------------------------------------
* pastagem: 15
* mosaico: 21 (mosaico de usos) e 14 (agropecuária sem detalhe)
* temporaria: 18, 19, 39 soja, 20 cana, 40 arroz, 62 algodão, 41 outras
* perene: 36, 46 café, 47 citrus, 35 dendê, 48 outras perenes, 9 silvicultura
* nativa: 1, 3, 4, 5, 6, 7, 49, 10, 11, 12, 77, 84, 50, 32
* nao_aproveitavel: 29 afloramento rochoso, 22–25 áreas não vegetadas,
  30 mineração, 75 fotovoltaica, 91 eólica, 26/33 água, 31 aquicultura

Saídas
------
* ``data/processed/uso_solo_hex_stats.parquet`` e ``uso_solo_fazendas_stats.parquet``
* ``data/interim/uso_solo_grupo_bahia_240m.tif``: grupo dominante em células de
  8×8 pixels, só para a camada de referência visual.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, bounds as window_bounds, transform as window_transform
from shapely.geometry import box

from build_prodes_raster_stats import layers_without_overlap, pixel_area_ha

ROOT = Path(__file__).resolve().parents[1]
RASTER = ROOT / "data" / "raw" / "mapbiomas_cobertura" / "mapbiomas_c11_cobertura_2025_bahia.tif"
PROCESSED = ROOT / "data" / "processed"
DISPLAY = ROOT / "data" / "interim" / "uso_solo_grupo_bahia_240m.tif"

GROUPS = ["pastagem", "mosaico", "temporaria", "perene", "nativa", "nao_aproveitavel"]
CODES = {
    "pastagem": [15],
    "mosaico": [21, 14],
    "temporaria": [18, 19, 39, 20, 40, 62, 41],
    "perene": [36, 46, 47, 35, 48, 9],
    "nativa": [1, 3, 4, 5, 6, 7, 49, 10, 11, 12, 77, 84, 50, 32],
    "nao_aproveitavel": [29, 22, 23, 24, 25, 30, 75, 91, 26, 33, 31],
}
LUT = np.zeros(256, dtype=np.uint8)            # 0 = sem dado / código fora da legenda
for number, group in enumerate(GROUPS, start=1):
    LUT[CODES[group]] = number
NGROUP = len(GROUPS) + 1
DISPLAY_FACTOR = 8


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", type=Path, default=RASTER)
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    parser.add_argument("--block", type=int, default=4096)
    args = parser.parse_args()

    src = rasterio.open(args.raster)
    unknown = set()
    hexes = gpd.read_file(args.processed / "bahia_hex_5km.geojson")[["id_hex", "geometry"]].to_crs(src.crs)
    farms = gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet", columns=["id_fazenda", "geometry"]).to_crs(src.crs)
    hexes["geometry"] = hexes.geometry.make_valid()
    farms["geometry"] = farms.geometry.make_valid()
    farm_layer = layers_without_overlap(farms)
    print(f"Hexágonos: {len(hexes):,} | imóveis: {len(farms):,} em {farm_layer.max()} camadas sem sobreposição", flush=True)

    unit_sets = [("hex", hexes, np.ones(len(hexes), dtype=np.int16)), ("farm", farms, farm_layer)]
    sums = {name: np.zeros((len(frame) + 1, NGROUP), dtype=np.float64) for name, frame, _ in unit_sets}
    index = {name: frame.sindex for name, frame, _ in unit_sets}
    dh, dw = int(np.ceil(src.height / DISPLAY_FACTOR)), int(np.ceil(src.width / DISPLAY_FACTOR))
    display = np.zeros((dh, dw), dtype=np.uint8)

    blocks = [(r, c) for r in range(0, src.height, args.block) for c in range(0, src.width, args.block)]
    for n, (row_off, col_off) in enumerate(blocks, 1):
        rows, cols = min(args.block, src.height - row_off), min(args.block, src.width - col_off)
        window = Window(col_off, row_off, cols, rows)
        data = src.read(1, window=window)
        group = LUT[data]
        stray = np.unique(data[(group == 0) & (data > 0)])
        unknown.update(int(v) for v in stray)
        if not group.any():
            continue
        area = np.broadcast_to(pixel_area_ha(src.transform, row_off, rows)[:, None], (rows, cols))
        wtransform = window_transform(window, src.transform)
        tile_box = box(*window_bounds(window, src.transform))

        # Visual reference: dominant group among valid pixels of each 8×8 cell.
        r8, c8 = rows // DISPLAY_FACTOR * DISPLAY_FACTOR, cols // DISPLAY_FACTOR * DISPLAY_FACTOR
        if r8 and c8:
            g = group[:r8, :c8].reshape(r8 // DISPLAY_FACTOR, DISPLAY_FACTOR, c8 // DISPLAY_FACTOR, DISPLAY_FACTOR)
            counts = np.stack([(g == k).sum(axis=(1, 3)) for k in range(1, NGROUP)], axis=0)
            cls = np.where(counts.sum(axis=0) > 0, counts.argmax(axis=0) + 1, 0).astype(np.uint8)
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
                    flat = np.bincount(o[hit] * NGROUP + flat_group[hit], weights=flat_area[hit], minlength=sums[name].size)
                    sums[name] += flat.reshape(sums[name].shape)
        if n % 25 == 0 or n == len(blocks):
            print(f"  blocos {n}/{len(blocks)}", flush=True)

    if unknown:
        print(f"Atenção: códigos fora da legenda ignorados: {sorted(unknown)}")
    for name, frame, _ in unit_sets:
        id_field = "id_hex" if name == "hex" else "id_fazenda"
        out = pd.DataFrame({id_field: frame[id_field].astype(str).to_numpy()})
        for k, label in enumerate(GROUPS, start=1):
            out[f"uso_{label}_ha"] = sums[name][1:, k].round(4)
        out["uso_area_mapeada_ha"] = sums[name][1:, 1:].sum(axis=1).round(4)
        target = args.processed / ("uso_solo_hex_stats.parquet" if name == "hex" else "uso_solo_fazendas_stats.parquet")
        out.to_parquet(target, index=False)
        print(f"{target.name}: {len(out):,} unidades; com área mapeada: {(out.uso_area_mapeada_ha > 0).sum():,}")

    profile = src.profile.copy()
    profile.update(width=dw, height=dh, transform=src.transform * src.transform.scale(DISPLAY_FACTOR, DISPLAY_FACTOR),
                   nodata=0, BIGTIFF="NO", compress="DEFLATE")
    with rasterio.open(DISPLAY, "w", **profile) as dst:
        dst.write(display, 1)
    src.close()


if __name__ == "__main__":
    main()

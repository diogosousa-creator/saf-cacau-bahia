"""Mosaico PRODES 30 m da Bahia a partir dos rasters oficiais por bioma.

Substitui a preparação vetorial anterior (WFS + Marco UE), cujo filtro por
``state = 'BA'`` devolveu um subconjunto incompleto do Cerrado e do
histórico acumulado.  Os rasters oficiais do INPE/TerraBrasilis trazem, em
cada pixel, o ano do desmatamento (d2000 = acumulado até 2000; d2001…;
r20xx = resíduo atribuído ao ano), vegetação nativa, hidrografia e nuvem.

Cada bioma usa sua própria legenda (arquivo .qml oficial).  Este script lê a
legenda, converte todos os biomas para um código unificado e grava um único
GeoTIFF da Bahia, em blocos, para leitura rápida:

    0–40   desmatamento no ano 2000 + código (d e r do mesmo ano)
    91     hidrografia
    99     não observado (nuvem / sem observação)
    100    vegetação nativa
    101    outras classes da legenda (ex.: não floresta)
    255    fora dos biomas mapeados
"""
from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, from_bounds

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "socioambiental" / "prodes_raster"
OUTPUT = ROOT / "data" / "interim" / "prodes_bahia_30m.tif"
BOUNDARY = ROOT / "data" / "processed" / "bahia_boundary.geojson"
NODATA = 255


def unified_lut(qml: Path) -> np.ndarray:
    """Map the biome's raster codes to the unified codes using its QML legend."""
    lut = np.full(256, NODATA, dtype=np.uint8)
    for entry in ET.parse(qml).getroot().iter("paletteEntry"):
        value = int(entry.get("value"))
        label = (entry.get("label") or "").lower()
        year = re.search(r"\b[dr](\d{4})\b", label)
        if year:
            lut[value] = int(year.group(1)) - 2000
        elif "hidrografia" in label:
            lut[value] = 91
        elif "nuvem" in label or "cloud" in label or "não observ" in label:
            lut[value] = 99
        elif "vegeta" in label:
            lut[value] = 100
        else:
            lut[value] = 101
    lut[NODATA] = NODATA
    return lut


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--rows", type=int, default=2048)
    args = parser.parse_args()

    sources = sorted(args.raw.glob("prodes_*.tif"))
    if not sources:
        raise FileNotFoundError(f"Nenhum raster PRODES em {args.raw}")
    with rasterio.open(sources[0]) as ref:
        res = ref.res[0]
        origin_x, origin_y = ref.transform.c, ref.transform.f
        crs = ref.crs
    west, south, east, north = gpd.read_file(BOUNDARY).to_crs(crs).total_bounds
    # Snap the Bahia extent to the official PRODES grid (no resampling).
    col0 = int(np.floor((west - origin_x) / res)) - 2
    row0 = int(np.floor((origin_y - north) / res)) - 2
    width = int(np.ceil((east - origin_x) / res)) + 2 - col0
    height = int(np.ceil((origin_y - south) / res)) + 2 - row0
    transform = Affine(res, 0, origin_x + col0 * res, 0, -res, origin_y - row0 * res)
    profile = dict(driver="GTiff", width=width, height=height, count=1, dtype="uint8", crs=crs,
                   transform=transform, nodata=NODATA, tiled=True, blockxsize=512, blockysize=512,
                   compress="deflate", BIGTIFF="YES")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Mosaico Bahia: {width:,} × {height:,} pixels ({res:.6f}°)", flush=True)

    conflicts = 0
    with rasterio.open(args.output, "w", **profile) as dst:
        handles = []
        for path in sources:
            src = rasterio.open(path)
            lut = unified_lut(path.with_suffix(".qml"))
            offset_x = (src.transform.c - transform.c) / res
            offset_y = (transform.f - src.transform.f) / res
            aligned = abs(src.res[0] - res) < 1e-12 and abs(offset_x - round(offset_x)) < 1e-6 and abs(offset_y - round(offset_y)) < 1e-6
            reader = src if aligned else WarpedVRT(src, crs=crs, transform=transform, width=width, height=height, resampling=0, nodata=NODATA)
            print(f"  {path.name}: {'grade alinhada' if aligned else 'reamostragem vizinho mais próximo'}", flush=True)
            handles.append((path.name, src, reader, lut, aligned))
        for top in range(0, height, args.rows):
            rows = min(args.rows, height - top)
            block = np.full((rows, width), NODATA, dtype=np.uint8)
            y_top = transform.f - top * res
            y_bottom = y_top - rows * res
            for name, src, reader, lut, aligned in handles:
                if aligned:
                    win = from_bounds(transform.c, y_bottom, transform.c + width * res, y_top, src.transform)
                    win = Window(round(win.col_off), round(win.row_off), width, rows)
                else:
                    win = Window(0, top, width, rows)
                data = reader.read(1, window=win, boundless=True, fill_value=NODATA)
                data = lut[data]
                valid = data != NODATA
                conflicts += int((valid & (block != NODATA)).sum())
                fill = valid & (block == NODATA)
                block[fill] = data[fill]
            dst.write(block, 1, window=Window(0, top, width, rows))
            print(f"  linhas {top + rows:,}/{height:,}", flush=True)
        for _, src, reader, _, aligned in handles:
            if not aligned:
                reader.close()
            src.close()
    print(f"Pixels com dois biomas (mantido o primeiro): {conflicts:,}")
    print(f"Saída: {args.output}")


if __name__ == "__main__":
    main()

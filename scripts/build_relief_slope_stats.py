"""Summarize the 30 m Copernicus slope raster by canonical WebGIS unit.

The input stores slope percentage multiplied by ten (Int16).  This script
never writes raster data to the app: it creates compact zonal statistics for
hexagons and certified properties separately.
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

from webgis.relief import SLOPE_MULTIPLIER


# Intervals in percentage: <=3, (3,8], (8,15], (15,25], (25,45], >45.
SLOPE_LABELS = ("0_3", "3_8", "8_15", "15_25", "25_45", "gt45")


def load_units(path: Path, id_field: str, crs: str) -> gpd.GeoDataFrame:
    units = gpd.read_parquet(path) if path.suffix.lower() in {".parquet", ".geoparquet"} else gpd.read_file(path)
    units = units[[id_field, "geometry"]].dropna(subset=["geometry"]).copy()
    units["geometry"] = units.geometry.make_valid()
    units = units[~units.geometry.is_empty].to_crs(crs).reset_index(drop=True)
    units["_row"] = np.arange(1, len(units) + 1, dtype=np.int32)
    return units


def summarize(raster_path: Path, units_path: Path, id_field: str, output: Path, tile_size: int, row_start: int = 0, row_end: int | None = None, partial_raw: bool = False) -> pd.DataFrame:
    with rasterio.open(raster_path) as source:
        units = load_units(units_path, id_field, str(source.crs))
        index = units.sindex
        n = len(units) + 1
        counts = np.zeros((n, 6), dtype=np.uint64)
        sums_x10 = np.zeros(n, dtype=np.uint64)
        max_x10 = np.zeros(n, dtype=np.int32)

        row_end = source.height if row_end is None else min(row_end, source.height)
        for row_off in range(row_start, row_end, tile_size):
            height = min(tile_size, source.height - row_off)
            for col_off in range(0, source.width, tile_size):
                width = min(tile_size, source.width - col_off)
                window = Window(col_off, row_off, width, height)
                candidates = list(index.query(box(*window_bounds(window, source.transform)), predicate="intersects"))
                if not candidates:
                    continue
                slope_x10 = source.read(1, window=window).astype(np.int32, copy=False)
                owner = rasterize(
                    ((units.geometry.iloc[pos], int(units._row.iloc[pos])) for pos in candidates),
                    out_shape=(height, width), transform=window_transform(window, source.transform),
                    fill=0, dtype="int32", all_touched=False,
                )
                inside = owner > 0
                if not inside.any():
                    continue
                ids = owner[inside]
                values = slope_x10[inside]
                sums_x10 += np.bincount(ids, weights=values, minlength=n).astype(np.uint64)
                for pos, mask in enumerate((values <= 30, (values > 30) & (values <= 80), (values > 80) & (values <= 150), (values > 150) & (values <= 250), (values > 250) & (values <= 450), values > 450)):
                    selected = ids[mask]
                    if selected.size:
                        counts[:, pos] += np.bincount(selected, minlength=n).astype(np.uint64)
                # maximum per unit inside this tile; candidates are few enough
                # that this avoids holding a full raster in memory.
                for unit in np.unique(ids):
                    unit_max = int(values[ids == unit].max())
                    if unit_max > max_x10[unit]:
                        max_x10[unit] = unit_max

    result = units[[id_field]].copy()
    if partial_raw:
        result["_valid_px"] = counts[1:].sum(axis=1)
        result["_sum_x10"] = sums_x10[1:]
        result["_max_x10"] = max_x10[1:]
        for pos, label in enumerate(SLOPE_LABELS):
            result[f"_count_{label}"] = counts[1:, pos]
        output.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(output, index=False)
        return result
    result["relevo_declividade_validos_px"] = counts[1:].sum(axis=1)
    valid = result["relevo_declividade_validos_px"].replace(0, np.nan)
    result["declividade_media_pct"] = (sums_x10[1:] / valid.to_numpy()) / 10
    result["declividade_max_pct"] = np.where(valid.notna(), max_x10[1:] / 10, np.nan)
    for pos, label in enumerate(SLOPE_LABELS):
        result[f"declividade_{label}_pct"] = counts[1:, pos] / valid.to_numpy() * 100
    result["relevo_declividade_score_preliminar"] = (counts[1:] / valid.to_numpy()[:, None] * SLOPE_MULTIPLIER).sum(axis=1) * 100
    result["relevo_declividade_critica_pct"] = result["declividade_gt45_pct"]
    result["fonte_relevo"] = "Copernicus DEM GLO-30 | declividade derivada por tile nativo | Int16 x10"
    result["versao_relevo"] = "COPDEM_GLO30_SLOPE_V3_INT16_X10"
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output, index=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", type=Path, required=True)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--id-field", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=1024)
    parser.add_argument("--row-start", type=int, default=0)
    parser.add_argument("--row-end", type=int, default=None)
    parser.add_argument("--partial-raw", action="store_true")
    args = parser.parse_args()
    result = summarize(args.raster, args.units, args.id_field, args.output, args.tile_size, args.row_start, args.row_end, args.partial_raw)
    if args.partial_raw:
        print(f"Faixa concluída: linhas {args.row_start} a {args.row_end or 'fim'}")
        return
    print(f"Unidades com pixels: {result.relevo_declividade_validos_px.gt(0).sum():,} de {len(result):,}")
    print(f"Nota preliminar média de declividade: {result.relevo_declividade_score_preliminar.mean():.1f}")


if __name__ == "__main__":
    main()

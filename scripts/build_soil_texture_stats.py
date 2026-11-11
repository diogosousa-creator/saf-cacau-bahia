"""Summarize MapBiomas Solo texture (0–30 cm) by WebGIS analysis unit.

The input is the categorical MapBiomas *grupamento textural* raster exported
from Earth Engine for Bahia.  This does not claim a complete soil aptitude:
it produces only the preliminary ``compatibilidade textural superficial``
criterion agreed for the first WebGIS iteration.

The operation is tile-based.  At each raster block, the vector units are
rasterized once and a class histogram is accumulated with NumPy.  This keeps
the source GeoTIFF local and avoids creating hundreds of millions of points
or sending raster data to the web application.
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


# Official MapBiomas Solo Collection 3 legend: grupamento textural.
TEXTURE = {
    1: ("Rocha / não solo", 0),
    2: ("Muito argilosa", 60),
    3: ("Argilosa", 80),
    4: ("Siltosa", 55),
    5: ("Arenosa", 40),
    6: ("Média", 75),
}
VALID_CODES = tuple(TEXTURE)


def _class_label(score: float | None) -> str:
    if score is None or np.isnan(score):
        return "Sem dados"
    if score < 20:
        return "Muito baixa"
    if score < 45:
        return "Baixa"
    if score < 65:
        return "Média"
    if score < 80:
        return "Alta"
    return "Muito alta"


def _read_units(path: Path, id_field: str, target_crs: str) -> gpd.GeoDataFrame:
    units = gpd.read_parquet(path) if path.suffix.lower() in {".parquet", ".geoparquet"} else gpd.read_file(path)
    if id_field not in units.columns:
        raise ValueError(f"Campo identificador inexistente: {id_field}")
    units = units[[id_field, "geometry"]].dropna(subset=["geometry"]).copy()
    units = units[~units.geometry.is_empty].to_crs(target_crs).reset_index(drop=True)
    units["_row"] = np.arange(1, len(units) + 1, dtype=np.int32)
    return units


def summarize(raster_path: Path, units_path: Path, id_field: str, output: Path, tile_size: int) -> pd.DataFrame:
    with rasterio.open(raster_path) as source:
        units = _read_units(units_path, id_field, str(source.crs))
        index = units.sindex
        counts = np.zeros((len(units) + 1, len(VALID_CODES)), dtype=np.uint64)

        for row_off in range(0, source.height, tile_size):
            height = min(tile_size, source.height - row_off)
            for col_off in range(0, source.width, tile_size):
                width = min(tile_size, source.width - col_off)
                window = Window(col_off, row_off, width, height)
                tile_bounds = window_bounds(window, source.transform)
                candidates = list(index.query(box(*tile_bounds), predicate="intersects"))
                if not candidates:
                    continue

                tile = source.read(1, window=window)
                if not np.isin(tile, VALID_CODES).any():
                    continue
                shapes = ((units.geometry.iloc[position], int(units._row.iloc[position])) for position in candidates)
                owner = rasterize(
                    shapes,
                    out_shape=(height, width),
                    transform=window_transform(window, source.transform),
                    fill=0,
                    dtype="int32",
                    all_touched=False,
                )
                for class_position, code in enumerate(VALID_CODES):
                    belonging = owner[tile == code]
                    if belonging.size:
                        counts[:, class_position] += np.bincount(belonging, minlength=len(units) + 1).astype(np.uint64)

        result = units[[id_field]].copy()
        for position, code in enumerate(VALID_CODES):
            result[f"solo_textura_{code}_px"] = counts[1:, position]
        result["solo_textura_validos_px"] = counts[1:].sum(axis=1)
        total = result["solo_textura_validos_px"].replace(0, np.nan)
        for code, (name, _) in TEXTURE.items():
            result[f"solo_textura_{code}_pct"] = result[f"solo_textura_{code}_px"] / total * 100
        score_weights = np.array([TEXTURE[code][1] for code in VALID_CODES], dtype=float)
        result["tipo_solo"] = (counts[1:] @ score_weights) / total.to_numpy()
        result["classe_tipo_solo"] = result["tipo_solo"].map(_class_label)
        result["solo_textura_dominante_codigo"] = np.where(
            result["solo_textura_validos_px"] > 0,
            np.array(VALID_CODES)[counts[1:].argmax(axis=1)],
            pd.NA,
        )
        result["solo_textura_dominante"] = result["solo_textura_dominante_codigo"].map(
            {code: name for code, (name, _) in TEXTURE.items()}
        )
        result["solo_textura_favoravel_pct"] = result["solo_textura_3_pct"].fillna(0) + result["solo_textura_6_pct"].fillna(0)
        result["solo_textura_restritiva_pct"] = result["solo_textura_1_pct"].fillna(0) + result["solo_textura_5_pct"].fillna(0)
        result["fonte_tipo_solo"] = "MapBiomas Solo Coleção 3 | grupamento textural 0–30 cm"
        result["versao_tipo_solo"] = "C3_v1"
        output.parent.mkdir(parents=True, exist_ok=True)
        result.drop(columns=["geometry"], errors="ignore").to_parquet(output, index=False)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", type=Path, required=True)
    parser.add_argument("--units", type=Path, required=True, help="GeoParquet dos hexágonos ou imóveis")
    parser.add_argument("--id-field", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=1024)
    args = parser.parse_args()
    summary = summarize(args.raster, args.units, args.id_field, args.output, args.tile_size)
    print(f"Unidades processadas: {len(summary):,}")
    print(f"Com pixels válidos: {(summary.solo_textura_validos_px > 0).sum():,}")
    print(f"Saída: {args.output}")


if __name__ == "__main__":
    main()

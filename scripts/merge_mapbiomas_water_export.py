"""Merge the sharded Earth Engine MapBiomas Água export without resampling."""
from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

import rasterio
from rasterio.merge import merge


DOWNLOADS = Path("/Users/diogosantossouza/Downloads")
DEFAULT_ZIP = DOWNLOADS / "drive-download-20261109T003302Z-1-001.zip"
DEFAULT_OUTPUT = Path("data/raw/hidrica/saf_cacau_bahia_mapbiomas_agua_c5_1985_2025.tif")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.zip.exists():
        raise FileNotFoundError(args.zip)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mapbiomas_agua_") as temp:
        root = Path(temp)
        with zipfile.ZipFile(args.zip) as archive:
            names = sorted(name for name in archive.namelist() if name.lower().endswith(".tif"))
            if len(names) < 2:
                raise ValueError("Exportação esperada em partes TIFF não encontrada.")
            archive.extractall(root, names)
        parts = [rasterio.open(root / name) for name in names]
        try:
            crs = parts[0].crs
            resolution = parts[0].res
            if any(src.crs != crs or src.res != resolution or src.count != 4 for src in parts):
                raise ValueError("As partes não têm o mesmo CRS, resolução ou quatro bandas.")
            mosaic, transform = merge(parts, method="first")  # no interpolation/resampling
            profile = parts[0].profile.copy()
            profile.update(
                driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
                tiled=True, blockxsize=512, blockysize=512, compress="LZW", predictor=2,
            )
            with rasterio.open(args.output, "w", **profile) as dst:
                dst.write(mosaic)
                descriptions = (
                    "water_frequency_1985_2025_pct",
                    "water_persistent_2021_2025",
                    "water_seasonal_2021_2025",
                    "water_recent_frequency_pct",
                )
                for index, description in enumerate(descriptions, start=1):
                    dst.set_band_description(index, description)
        finally:
            for src in parts:
                src.close()
    with rasterio.open(args.output) as output:
        print(f"Arquivo único: {args.output} | {output.count} bandas | resolução {output.res} | dimensões {output.width}×{output.height}")


if __name__ == "__main__":
    main()

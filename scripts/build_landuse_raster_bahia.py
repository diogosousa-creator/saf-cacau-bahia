"""Junta a exportação do MapBiomas Cobertura (Earth Engine) num único GeoTIFF.

A exportação de ``gee_export_mapbiomas_cobertura_bahia.js`` chega do Google
Drive como um .zip com uma ou mais partes.  Este script junta as partes sem
reamostrar e grava a **base original** com os códigos oficiais da legenda da
Coleção 11, para o índice e para mapas futuros (por exemplo, PDFs regionais):

    data/raw/mapbiomas_cobertura/mapbiomas_c11_cobertura_2025_bahia.tif

O arquivo original não é alterado por nenhuma etapa posterior.
"""
from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "raw" / "mapbiomas_cobertura" / "mapbiomas_c11_cobertura_2025_bahia.tif"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, help="Zip baixado do Google Drive (padrão: o mais recente em ~/Downloads com 'mapbiomas_c11').")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source = args.zip
    if source is None:
        candidates = sorted(Path.home().glob("Downloads/*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        source = next((p for p in candidates if any("mapbiomas_c11_cobertura" in n for n in zipfile.ZipFile(p).namelist())), None)
        if source is None:
            raise FileNotFoundError("Nenhum zip com 'mapbiomas_c11_cobertura' em ~/Downloads; use --zip.")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mapbiomas_cobertura_") as temp:
        root = Path(temp)
        with zipfile.ZipFile(source) as archive:
            names = sorted(n for n in archive.namelist() if n.lower().endswith(".tif") and "mapbiomas_c11_cobertura" in n)
            if not names:
                raise ValueError(f"{source.name}: nenhuma parte TIFF da exportação.")
            archive.extractall(root, names)
        parts = [rasterio.open(root / name) for name in names]
        try:
            crs, res = parts[0].crs, parts[0].res
            if any(p.crs != crs or p.res != res or p.count != 1 for p in parts):
                raise ValueError("As partes não têm o mesmo CRS, resolução ou banda única.")
            mosaic, transform = merge(parts, method="first", nodata=0)  # sem reamostragem
            profile = parts[0].profile.copy()
            profile.update(driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
                           dtype="uint8", nodata=0, tiled=True, blockxsize=512, blockysize=512,
                           compress="DEFLATE", predictor=2, BIGTIFF="IF_SAFER")
            with rasterio.open(args.output, "w", **profile) as dst:
                dst.write(mosaic.astype(np.uint8))
                dst.set_band_description(1, "MapBiomas Brasil Colecao 11 - cobertura e uso 2025 (codigos oficiais)")
                dst.update_tags(fonte="MapBiomas Brasil Colecao 11", ano="2025",
                                asset="projects/mapbiomas-public/assets/brazil/lulc/collection11/mapbiomas_brazil_collection11_coverage_v3",
                                legenda="https://brasil.mapbiomas.org/downloads/codigos-de-legenda/")
        finally:
            for p in parts:
                p.close()

    with rasterio.open(args.output) as out:
        sample = out.read(1, out_shape=(out.height // 20, out.width // 20))
        codes, counts = np.unique(sample[sample > 0], return_counts=True)
        top = ", ".join(f"{c}:{n * 100 / counts.sum():.1f}%" for c, n in sorted(zip(codes, counts), key=lambda x: -x[1])[:8])
        print(f"{args.output} | {out.width}×{out.height} px | resolução {out.res} | {len(names)} parte(s)")
        print(f"Classes mais frequentes (amostra): {top}")


if __name__ == "__main__":
    main()

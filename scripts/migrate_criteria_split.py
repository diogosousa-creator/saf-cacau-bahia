"""Add the two independent soil-terrain score fields to existing datasets."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


NEW_COLUMNS = ("tipo_solo", "relevo_drenagem")


def migrate_parquet(path: Path, geospatial: bool) -> None:
    if not path.exists():
        return
    frame = gpd.read_parquet(path) if geospatial else pd.read_parquet(path)
    for column in NEW_COLUMNS:
        if column not in frame:
            frame[column] = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    if geospatial:
        frame.to_parquet(path)
    else:
        frame.to_parquet(path, index=False)
    print(f"Migrado: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    migrate_parquet(args.processed / "assessments.parquet", geospatial=False)
    migrate_parquet(args.processed / "fazendas_georeferenciadas.geoparquet", geospatial=True)


if __name__ == "__main__":
    main()

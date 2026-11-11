"""Persist independent MapBiomas Solo summaries into the two analytical bases.

``hexagonos`` writes directly to ``assessments.parquet`` (join ``id`` ↔
``id_hex``). ``fazendas`` writes directly to the certified-properties
GeoParquet (join ``id_fazenda``). The joins never transfer values between
the two geometries: each result was calculated against its own polygon.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


SOIL_FIELDS = [
    "tipo_solo",
    "classe_tipo_solo",
    "solo_textura_dominante_codigo",
    "solo_textura_dominante",
    "solo_textura_validos_px",
    "solo_textura_favoravel_pct",
    "solo_textura_restritiva_pct",
    "fonte_tipo_solo",
    "versao_tipo_solo",
    *[f"solo_textura_{code}_pct" for code in range(1, 7)],
]


def update_table(base: pd.DataFrame, stats: pd.DataFrame, base_key: str, stats_key: str) -> pd.DataFrame:
    keep = [stats_key, *[column for column in SOIL_FIELDS if column in stats.columns]]
    incoming = stats[keep].rename(columns={stats_key: base_key})
    base = base.drop(columns=[column for column in incoming.columns if column != base_key and column in base.columns])
    return base.merge(incoming, on=base_key, how="left", validate="one_to_one")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("hexagonos", "fazendas"), required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--processed", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    stats = pd.read_parquet(args.stats)

    if args.kind == "hexagonos":
        path = args.processed / "assessments.parquet"
        updated = update_table(pd.read_parquet(path), stats, "id", "id_hex")
        updated.to_parquet(path, index=False)
    else:
        path = args.processed / "fazendas_georeferenciadas.geoparquet"
        farms = gpd.read_parquet(path)
        updated = update_table(farms, stats, "id_fazenda", "id_fazenda")
        gpd.GeoDataFrame(updated, geometry="geometry", crs=farms.crs).to_parquet(path, index=False)

    print(f"Base atualizada: {path}")


if __name__ == "__main__":
    main()

"""Merge partial slope summaries and ingest them into a canonical store."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from webgis.relief import RELIEF_METHOD_VERSION, SLOPE_MULTIPLIER


LABELS = ("0_3", "3_8", "8_15", "15_25", "25_45", "gt45")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", required=True, help="Glob de parquets parciais")
    parser.add_argument("--id-field", required=True)
    parser.add_argument("--store-id", default=None, help="Identificador correspondente na base canônica")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--geo", action="store_true", help="Store é GeoParquet")
    args = parser.parse_args()
    store_id = args.store_id or args.id_field

    paths = sorted(Path().glob(args.parts))
    if not paths:
        raise FileNotFoundError(args.parts)
    parts = [pd.read_parquet(p) for p in paths]
    raw = pd.concat(parts, ignore_index=True)
    count_cols = [f"_count_{label}" for label in LABELS]
    aggregate = raw.groupby(args.id_field, as_index=False)[["_valid_px", "_sum_x10", "_max_x10", *count_cols]].agg(
        {"_valid_px": "sum", "_sum_x10": "sum", "_max_x10": "max", **{c: "sum" for c in count_cols}}
    )
    valid = aggregate["_valid_px"].replace(0, np.nan)
    aggregate["relevo_declividade_validos_px"] = aggregate["_valid_px"]
    aggregate["declividade_media_pct"] = aggregate["_sum_x10"] / valid / 10
    aggregate["declividade_max_pct"] = aggregate["_max_x10"] / 10
    shares = []
    for label, column in zip(LABELS, count_cols):
        out = f"declividade_{label}_pct"
        aggregate[out] = aggregate[column] / valid * 100
        shares.append(aggregate[out].to_numpy())
    aggregate["relevo_declividade_score_preliminar"] = np.nansum(np.column_stack(shares) * SLOPE_MULTIPLIER, axis=1)
    aggregate["relevo_declividade_critica_pct"] = aggregate["declividade_gt45_pct"]
    aggregate["fonte_relevo"] = "Copernicus DEM GLO-30 | declividade por tile nativo | 30 m | Int16 x10"
    aggregate["versao_relevo"] = "COPDEM_GLO30_SLOPE_V3_INT16_X10"
    keep = [args.id_field, "relevo_declividade_validos_px", "declividade_media_pct", "declividade_max_pct", *[f"declividade_{x}_pct" for x in LABELS], "relevo_declividade_score_preliminar", "relevo_declividade_critica_pct", "fonte_relevo", "versao_relevo"]
    stats = aggregate[keep]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stats.to_parquet(args.output, index=False)

    store = gpd.read_parquet(args.store) if args.geo else pd.read_parquet(args.store)
    existing = [c for c in stats.columns if c != args.id_field and c in store.columns]
    store = store.drop(columns=existing).merge(stats, left_on=store_id, right_on=args.id_field, how="left", validate="one_to_one")
    if args.id_field != store_id:
        store = store.drop(columns=[args.id_field])
    available = store["relevo_declividade_validos_px"].fillna(0).gt(0)
    # This criterion deliberately scores only local slope for operational
    # handling/mechanisation and erosion exposure. Elevation is contextual;
    # using it again in the score would duplicate the same terrain signal.
    store["relevo_drenagem"] = store["relevo_declividade_score_preliminar"]
    store["relevo_drenagem_score"] = store["relevo_declividade_score_preliminar"]
    store["classe_relevo_drenagem"] = pd.cut(
        store["relevo_drenagem"], [-np.inf, 25, 45, 65, 80, np.inf],
        labels=["Muito restrita", "Restrita", "Condicionada", "Favorável", "Muito favorável"], right=False,
    ).astype("string")
    store["relevo_drenagem_coverage_pct"] = np.where(available, 100.0, pd.NA)
    store["relevo_drenagem_confidence"] = np.where(available, "high", "unknown")
    store["relevo_drenagem_status"] = np.where(available, "complete", "unknown")
    store["relevo_drenagem_source_key"] = np.where(available, "copernicus_dem_glo30_30m", pd.NA)
    store["relevo_drenagem_method_version"] = np.where(available, RELIEF_METHOD_VERSION, pd.NA)
    critical = store["relevo_declividade_critica_pct"].fillna(0).ge(10)
    store["relevo_drenagem_alert_level"] = np.where(critical, "warning", "none")
    store["relevo_drenagem_alert_flags"] = np.where(critical, '["declividade_critica_acima_10pct"]', "[]")
    store.to_parquet(args.store, index=False)
    print(f"{len(stats):,} unidades; declividade média geral {stats.declividade_media_pct.mean():.2f}%")


if __name__ == "__main__":
    main()

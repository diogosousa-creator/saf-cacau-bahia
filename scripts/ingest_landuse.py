"""Grava o critério "Uso atual do solo" nas bases de hexágonos e imóveis.

Nota = Σ (% da área aproveitável em cada grupo × nota do grupo), com

    pastagem 100 · mosaico de usos 75 · lavoura temporária 50 ·
    lavoura perene / silvicultura 25 · vegetação nativa 0

Água, área urbana, afloramento rochoso, mineração e demais áreas não
vegetadas ficam fora do denominador.  Unidades sem área aproveitável ficam
sem nota (nunca zero).  Fonte: MapBiomas Brasil Coleção 11, ano 2025.

Entradas: ``uso_solo_hex_stats.parquet`` e ``uso_solo_fazendas_stats.parquet``
(``build_landuse_stats.py``).  Cada unidade recebe o valor calculado sobre a
própria geometria.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

GROUP_SCORES = {"pastagem": 100, "mosaico": 75, "temporaria": 50, "perene": 25, "nativa": 0}
GROUP_LABELS = {"pastagem": "Pastagem", "mosaico": "Mosaico de usos", "temporaria": "Lavoura temporária",
                "perene": "Lavoura perene / silvicultura", "nativa": "Vegetação nativa"}
METHOD = "USO_SOLO_MAPBIOMAS_C11_2025_V1"


def score_class(value: float) -> str | None:
    if pd.isna(value):
        return None
    return ("Muito alta" if value >= 80 else "Alta" if value >= 65 else "Média" if value >= 50
            else "Baixa" if value >= 35 else "Muito baixa")


def landuse_fields(stats: pd.DataFrame) -> pd.DataFrame:
    out = stats.copy()
    useful = sum(out[f"uso_{g}_ha"] for g in GROUP_SCORES)
    out["uso_aproveitavel_ha"] = useful.round(4)
    for group in GROUP_SCORES:
        out[f"uso_{group}_pct"] = np.where(useful > 0, out[f"uso_{group}_ha"] / useful * 100, np.nan).round(2)
    weighted = sum(out[f"uso_{g}_ha"] * s for g, s in GROUP_SCORES.items())
    out["uso_solo"] = np.where(useful > 0, weighted / useful, np.nan).round(2)
    out["classe_uso_solo"] = out["uso_solo"].map(score_class)
    shares = out[[f"uso_{g}_ha" for g in GROUP_SCORES]].to_numpy()
    dominant = np.array(list(GROUP_SCORES))[shares.argmax(axis=1)]
    out["uso_grupo_dominante"] = np.where(useful > 0, pd.Series(dominant).map(GROUP_LABELS), None)
    out["uso_solo_method_version"] = METHOD
    out["uso_solo_source_key"] = "mapbiomas_brasil_c11_cobertura_2025"
    return out


def attach(base: pd.DataFrame, fields: pd.DataFrame, key: str) -> pd.DataFrame:
    new_columns = [c for c in fields.columns if c != key]
    base = base.drop(columns=[c for c in new_columns if c in base.columns])
    merged = base.merge(fields, on=key, how="left", validate="one_to_one")
    if len(merged) != len(base):
        raise RuntimeError("Junção alterou o número de unidades.")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).isoformat()

    hex_fields = landuse_fields(pd.read_parquet(args.processed / "uso_solo_hex_stats.parquet")).rename(columns={"id_hex": "id"})
    farm_fields = landuse_fields(pd.read_parquet(args.processed / "uso_solo_fazendas_stats.parquet"))
    for table in (hex_fields, farm_fields):
        table["uso_solo_processed_at_utc"] = stamp

    assessments = pd.read_parquet(args.processed / "assessments.parquet")
    assessments["id"] = assessments["id"].astype(str)
    hex_fields["id"] = hex_fields["id"].astype(str)
    if set(hex_fields["id"]) != set(assessments["id"]):
        raise RuntimeError("Estatística de hexágonos não corresponde a assessments.parquet.")
    attach(assessments, hex_fields, "id").to_parquet(args.processed / "assessments.parquet", index=False)

    farms = gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet")
    farm_fields["id_fazenda"] = farm_fields["id_fazenda"].astype(str)
    if set(farm_fields["id_fazenda"]) != set(farms["id_fazenda"].astype(str)):
        raise RuntimeError("Estatística de imóveis não corresponde à base-mãe.")
    out_farms = attach(farms, farm_fields, "id_fazenda")
    gpd.GeoDataFrame(out_farms, geometry="geometry", crs=farms.crs).to_parquet(
        args.processed / "fazendas_georeferenciadas.geoparquet", index=False)

    for name, table in (("hexágonos", hex_fields), ("imóveis", farm_fields)):
        valid = table["uso_solo"].notna()
        print(f"{name}: {valid.sum():,} com nota de {len(table):,} | média {table.loc[valid, 'uso_solo'].mean():.1f} | "
              f"classes {table['classe_uso_solo'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()

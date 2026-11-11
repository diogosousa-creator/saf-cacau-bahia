"""Create the lightweight browser layer for the preliminary soil criterion."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
COLORS = {
    1: [112, 112, 112, 185], 2: [168, 56, 0, 185], 3: [170, 134, 134, 185],
    4: [41, 130, 137, 185], 5: [255, 254, 115, 185], 6: [215, 197, 165, 185],
}


def score_color(score: object) -> list[int]:
    if pd.isna(score):
        return [196, 202, 208, 120]
    value = float(score)
    if value < 35: return [197, 52, 47, 155]
    if value < 50: return [235, 122, 47, 155]
    if value < 65: return [246, 204, 63, 155]
    if value < 80: return [152, 191, 83, 155]
    return [52, 139, 75, 155]


def main() -> None:
    assessments = pd.read_parquet(ROOT / "data/processed/assessments.parquet").set_index("id")
    geometry = json.loads((ROOT / "data/processed/bahia_hex_5km.geojson").read_text(encoding="utf-8"))
    for feature in geometry["features"]:
        props = feature["properties"]
        row = assessments.loc[props["id_hex"]]
        dominant = row["solo_textura_dominante"]
        code = row["solo_textura_dominante_codigo"]
        props.update({
            "nota_mapa": None if pd.isna(row["tipo_solo"]) else round(float(row["tipo_solo"]), 1),
            "classe_mapa": row["classe_tipo_solo"] if not pd.isna(row["classe_tipo_solo"]) else "Sem dados",
            "solo_textura_dominante": dominant if not pd.isna(dominant) else "Sem dados",
            "solo_textura_favoravel_pct": None if pd.isna(row["solo_textura_favoravel_pct"]) else round(float(row["solo_textura_favoravel_pct"]), 1),
            "fill_color": score_color(row["tipo_solo"]),
            "raw_fill_color": COLORS.get(int(code), [196, 202, 208, 120]) if not pd.isna(code) else [196, 202, 208, 120],
        })
    output = ROOT / "static/hex_soil_0_30cm.geojson"
    output.write_text(json.dumps(geometry, separators=(",", ":")), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

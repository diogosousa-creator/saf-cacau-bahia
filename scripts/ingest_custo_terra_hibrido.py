"""Build the auditable Bahia land-cost reference (VTN) for the WebGIS.

The model deliberately does *not* pretend to estimate sale price for a farm.
It stores a municipal/MRT VTN reference in R$/ha.  Receita Federal VTN, when
available, is retained exactly for its municipality; the INCRA/SIMET Atlas
provides the statewide MRT backbone.  Missing Receita observations can be
locally adjusted with neighbouring Receita residuals without overwriting an
official municipal value.

Inputs
------
* Atlas_do_Mercado_de_Terras_2025.pdf (official INCRA/SIMET)
* optional vtn_receita_2025.csv/xlsx (official Receita table, standardised)

The optional Receita table must contain a municipality column and a "lavoura
aptidao boa" / ``vtn_lavoura_boa_rs_ha`` column.  Keeping this import optional
lets the pipeline stay reproducible while the Receita endpoint is unavailable.
"""
from __future__ import annotations

import argparse
import math
import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pypdf import PdfReader
from shapely import make_valid


ATLAS_URL = (
    "https://www.gov.br/incra/pt-br/centrais-de-conteudos/publicacoes/"
    "Atlas_do_Mercado_de_Terras_2025.pdf/@@display-file/file"
)
DATE_BASE = "2025"
SIGMA0 = 0.55
SIGMA_SP = 0.35


def normal(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def br_number(value: object) -> float:
    if pd.isna(value):
        return np.nan
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def atlas_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def parse_atlas_bahia(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return municipality -> MRT and its agricultura/general VTN backbone."""
    text = atlas_text(path).replace("-\n", "").replace("\n", " ")
    matches = list(re.finditer(r"MRT BA-(5\d{2}):\s*([^\n]+?)\s+O mercado é composto pelos municípios de:\s*(.*?)\s+Figura", text))
    rows: list[dict[str, object]] = []
    markets: list[dict[str, object]] = []
    for i, match in enumerate(matches):
        code, label, names = match.groups()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[match.start():end]
        agricultural = re.search(r"\b1 Agrícola\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})", block)
        general = re.search(r"\b0 Geral\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})", block)
        source_level = "agricola" if agricultural else "geral_fallback"
        found = agricultural or general
        if not found:
            raise RuntimeError(f"VTN não encontrado no Atlas para MRT BA-{code}.")
        vtn = br_number(found.group(2))
        market_id = f"BA-{code}"
        markets.append({"mrt_id": market_id, "mrt_nome": label.strip(), "vtn_simet_mrt_rs_ha": vtn, "vtn_simet_nivel": source_level})
        for city in names.split(","):
            city = re.sub(r"\s+", " ", city).strip(" .")
            if city:
                rows.append({"municipio_norm": normal(city), "mrt_id": market_id})
    return pd.DataFrame(rows).drop_duplicates("municipio_norm"), pd.DataFrame(markets).drop_duplicates("mrt_id")


def find_receita_file(raw: Path) -> Path | None:
    choices = list(raw.glob("vtn_receita_2025.csv")) + list(raw.glob("vtn_receita_2025.xlsx"))
    return choices[0] if choices else None


def receita_table(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame(columns=["municipio_norm", "vtn_oficial_receita_rs_ha", "receita_qa"])
    data = pd.read_excel(path) if path.suffix.lower() == ".xlsx" else pd.read_csv(path)
    col_by_norm = {normal(c): c for c in data.columns}
    municipality = next((col_by_norm[key] for key in col_by_norm if key in {"MUNICIPIO", "NOMEMUNICIPIO"}), None)
    agricultural = next((col_by_norm[key] for key in col_by_norm if key in {"VTNLAVOURABOARSHA", "LAVOURAAPTIDAOBOA", "LAVOURAAPTIDAOBOARSHA"}), None)
    if not municipality or not agricultural:
        raise ValueError("A tabela Receita precisa de município e VTN de Lavoura Aptidão Boa.")
    result = pd.DataFrame({
        "municipio_norm": data[municipality].map(normal),
        "vtn_oficial_receita_rs_ha": data[agricultural].map(br_number),
    })
    result = result[result.vtn_oficial_receita_rs_ha.gt(0)].drop_duplicates("municipio_norm")
    result["receita_qa"] = 1.0
    return result


def neighbour_residuals(municipal: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Apply the residual model only where a direct Receita observation is absent."""
    g = municipal.to_crs(5880).copy()
    centers = g.geometry.centroid.to_crs(4326)
    coords = np.c_[centers.x.to_numpy(), centers.y.to_numpy()]
    direct = g.vtn_oficial_receita_rs_ha.notna() & g.vtn_oficial_receita_rs_ha.gt(0)
    g["vtn_origem_municipal"] = np.where(direct, "Receita_direta", "SIMET")
    g["vtn_hibrido_municipal_rs_ha"] = np.where(direct, g.vtn_oficial_receita_rs_ha, g.vtn_simet_mrt_rs_ha)
    g["vtn_model_var"] = np.where(direct, 0.0, SIGMA0**2)
    g["vtn_confianca_municipal"] = np.where(direct, "alta", "média")
    g["vtn_alertas_municipal"] = np.where(direct, "[]", '["referencia_simet_mrt"]')
    if not direct.any():
        return g.to_crs(municipal.crs)

    g["_resid"] = np.where(direct, np.log(g.vtn_oficial_receita_rs_ha) - np.log(g.vtn_simet_mrt_rs_ha), np.nan)
    # Official municipal figures remain immutable.  Only absent values receive
    # a spatially weighted correction to their own SIMET/MRT backbone.
    from pyproj import Geod
    geod = Geod(ellps="GRS80")
    ix = g.sindex
    for idx, row in g.loc[~direct].iterrows():
        candidate = list(ix.query(row.geometry.buffer(150_000), predicate="intersects"))
        near = g.iloc[candidate]
        near = near[near.vtn_oficial_receita_rs_ha.notna()]
        if near.empty:
            continue
        dists = []
        for nidx in near.index:
            _, _, dist = geod.inv(coords[idx, 0], coords[idx, 1], coords[nidx, 0], coords[nidx, 1])
            dists.append((nidx, max(dist / 1000, 0.1)))
        dists.sort(key=lambda item: item[1])
        dists = dists[:5]
        precisions = []
        residuals = []
        for nidx, km in dists:
            same = 1.5 if g.at[nidx, "mrt_id"] == row.mrt_id else 1.0
            weight = math.exp(-km / 75) * same
            sigma_d = 0.25
            precisions.append(weight / (sigma_d**2 + SIGMA_SP**2))
            residuals.append(g.at[nidx, "_resid"])
        total = min(float(sum(precisions)), 6.0)
        if total <= 0:
            continue
        # Scale capped precision proportionally so the stated cap holds.
        if sum(precisions) > total:
            precisions = [p * total / sum(precisions) for p in precisions]
        rhat = float(np.dot(precisions, residuals) / (1 / SIGMA0**2 + sum(precisions)))
        variance = 1 / (1 / SIGMA0**2 + sum(precisions))
        g.at[idx, "vtn_hibrido_municipal_rs_ha"] = math.exp(math.log(row.vtn_simet_mrt_rs_ha) + rhat)
        g.at[idx, "vtn_origem_municipal"] = "SIMET_vizinhos"
        g.at[idx, "vtn_model_var"] = variance
        confidence = 1 - variance / SIGMA0**2
        g.at[idx, "vtn_confianca_municipal"] = "média" if confidence >= .45 else "baixa"
        g.at[idx, "vtn_alertas_municipal"] = '["referencia_simet_ajustada_por_vizinhos"]'
    return g.to_crs(municipal.crs)


def weighted_to_units(units: gpd.GeoDataFrame, municipalities: gpd.GeoDataFrame, id_col: str) -> pd.DataFrame:
    left = units[[id_col, "geometry"]].to_crs(5880).copy()
    right = municipalities[["vtn_hibrido_municipal_rs_ha", "vtn_oficial_receita_rs_ha", "vtn_simet_mrt_rs_ha", "vtn_origem_municipal", "mrt_id", "vtn_confianca_municipal", "vtn_model_var", "geometry"]].to_crs(5880)
    # Official cadastral polygons can contain minute self-crossings.  Repairing
    # them before the area overlay avoids topology errors without generalising
    # or changing their analytical boundary.
    left["geometry"] = left.geometry.map(make_valid)
    right = right.copy()
    right["geometry"] = right.geometry.map(make_valid)
    pairs = gpd.sjoin(left, right, how="inner", predicate="intersects").rename(columns={"index_right": "_municipal_i"})
    right_geometry = right.geometry
    def safe_area(geometry, municipal_index):
        try:
            return geometry.intersection(right_geometry.loc[municipal_index]).area
        except Exception:
            return make_valid(geometry).intersection(make_valid(right_geometry.loc[municipal_index])).area
    pairs["_inter_area"] = [safe_area(a, b) for a, b in zip(pairs.geometry, pairs._municipal_i)]
    pairs = pairs[pairs._inter_area.gt(0)]
    grouped = []
    for unit_id, group in pairs.groupby(id_col, sort=False):
        weights = group._inter_area.to_numpy() / group._inter_area.sum()
        value = float(np.dot(weights, group.vtn_hibrido_municipal_rs_ha))
        official = group.vtn_oficial_receita_rs_ha.to_numpy(dtype=float)
        simet = group.vtn_simet_mrt_rs_ha.to_numpy(dtype=float)
        origins = group.vtn_origem_municipal.astype(str).tolist()
        confidence = "alta" if all(x == "alta" for x in group.vtn_confianca_municipal) else ("baixa" if any(x == "baixa" for x in group.vtn_confianca_municipal) else "média")
        grouped.append({
            id_col: unit_id,
            "vtn_agricola_hibrido_rs_ha": value,
            "vtn_oficial_receita_rs_ha": float(np.nansum(weights * np.nan_to_num(official, nan=0))) if np.isfinite(official).any() else np.nan,
            "vtn_simet_mrt_rs_ha": float(np.dot(weights, simet)),
            "vtn_origem": "Receita_direta" if all(x == "Receita_direta" for x in origins) else ("SIMET_vizinhos" if any(x == "SIMET_vizinhos" for x in origins) else "SIMET"),
            "vtn_ano_base": DATE_BASE,
            "mrt_id": ";".join(sorted(group.mrt_id.astype(str).unique())),
            "municipios_intersectados": int(len(group)),
            "vtn_confianca": confidence,
            "vtn_intervalo_80_pct": round((math.exp(1.282 * math.sqrt(float(np.dot(weights, group.vtn_model_var)))) - 1) * 100, 1),
            "vtn_alertas": "[]" if confidence == "alta" else '["referencia_regional_nao_avaliacao_predial"]',
        })
    return pd.DataFrame(grouped)


def update_master(table: pd.DataFrame, stats: pd.DataFrame, key: str) -> pd.DataFrame:
    drop = [c for c in stats.columns if c != key and c in table.columns]
    table = table.drop(columns=drop, errors="ignore").merge(stats, left_on=key, right_on=key, how="left")
    values = table.vtn_agricola_hibrido_rs_ha.dropna()
    lo, hi = values.quantile(.10), values.quantile(.90)
    table["custo_terra_score"] = (100 * (np.log(hi) - np.log(table.vtn_agricola_hibrido_rs_ha)) / (np.log(hi) - np.log(lo))).clip(0, 100)
    table["custo_terra"] = table.custo_terra_score
    table["custo_terra_coverage_pct"] = np.where(table.vtn_agricola_hibrido_rs_ha.notna(), 100.0, 0.0)
    table["custo_terra_confidence"] = table.vtn_confianca.fillna("unknown")
    table["custo_terra_status"] = np.where(table.vtn_agricola_hibrido_rs_ha.notna(), "complete", "unknown")
    table["custo_terra_source_key"] = "receita_vtn_2025+incra_simet_atlas_2025"
    table["custo_terra_method_version"] = "VTN_HIBRIDO_MUNICIPAL_v1"
    table["custo_terra_alert_level"] = np.where(table.vtn_confianca.eq("baixa"), "warning", "none")
    table["custo_terra_alert_flags"] = table.vtn_alertas.fillna("[]")
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", type=Path, default=Path("data/processed"))
    parser.add_argument("--raw", type=Path, default=Path("data/raw/custo_terra"))
    args = parser.parse_args()
    atlas = args.raw / "atlas_mercado_terras_2025.pdf"
    if not atlas.exists():
        raise FileNotFoundError(atlas)
    mapping, markets = parse_atlas_bahia(atlas)
    municipalities = gpd.read_file(args.raw.parent / "ibge_bahia_2024" / "BA_Municipios_2024.shp")
    name_col = next(c for c in municipalities.columns if normal(c) in {"NM_MUN", "NMMUN", "NOMEMUNICIPIO"})
    municipalities["municipio_norm"] = municipalities[name_col].map(normal)
    # Two spelling variants between the 2024 IBGE mesh and the Atlas are
    # normalised explicitly, rather than silently assigning a nearby market.
    atlas_alias = {"SANTATEREZINHA": "SANTATERESINHA", "MUQUEMDOSAOFRANCISCO": "MUQUEMDESAOFRANCISCO"}
    municipalities["municipio_norm"] = municipalities["municipio_norm"].replace(atlas_alias)
    municipalities = municipalities.merge(mapping, on="municipio_norm", how="left").merge(markets, on="mrt_id", how="left")
    missing = municipalities.mrt_id.isna()
    if missing.any():
        missing_names = municipalities.loc[missing, name_col].tolist()
        raise RuntimeError(f"{len(missing_names)} municípios sem MRT no Atlas: {missing_names[:12]}")
    receita = receita_table(find_receita_file(args.raw))
    municipalities = municipalities.merge(receita, on="municipio_norm", how="left")
    municipalities = neighbour_residuals(municipalities)
    municipal_out = args.processed / "custo_terra_municipios_hibrido_2025.geoparquet"
    municipalities.to_parquet(municipal_out, index=False)
    hexes = gpd.read_file(args.processed / "bahia_hex_5km.geojson").rename(columns={"id_hex": "id"})
    farms = gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet")
    hex_stats = weighted_to_units(hexes, municipalities, "id")
    farm_stats = weighted_to_units(farms, municipalities, "id_fazenda")
    hex_master = update_master(pd.read_parquet(args.processed / "assessments.parquet"), hex_stats, "id")
    farm_master = update_master(gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet"), farm_stats, "id_fazenda")
    hex_master.to_parquet(args.processed / "assessments.parquet", index=False)
    farm_master.to_parquet(args.processed / "fazendas_georeferenciadas.geoparquet", index=False)
    hex_stats.to_parquet(args.processed / "custo_terra_hex_stats.parquet", index=False)
    farm_stats.to_parquet(args.processed / "custo_terra_fazendas_stats.parquet", index=False)
    direct = int(municipalities.vtn_oficial_receita_rs_ha.notna().sum())
    print(f"Custo de terra concluído: {len(hex_stats):,} hexágonos; {len(farm_stats):,} imóveis; {direct} municípios Receita direta.")


if __name__ == "__main__":
    main()

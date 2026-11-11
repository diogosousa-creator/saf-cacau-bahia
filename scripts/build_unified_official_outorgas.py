"""Download, normalize and deduplicate public official georeferenced outorgas.

Scope: CNARH/SNIRH surface records already downloaded from the IG service,
current DADOSABERTOS state/federal surface layers, and state groundwater
outorgas.  INEMA/SEIA remains a documented primary-source reconciliation gap
until an official structured export is supplied.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "hidrica"
OUT = ROOT / "data" / "processed" / "outorgas_oficiais_bahia.geoparquet"
PAGE = 2_000
LAYERS = {
    "SNIRH dados abertos — estadual superficial": ("https://portal1.snirh.gov.br/arcgis/rest/services/DADOSABERTOS/outorgas_estaduais_superficial/FeatureServer/0", "superficial"),
    "SNIRH dados abertos — federal superficial": ("https://portal1.snirh.gov.br/arcgis/rest/services/DADOSABERTOS/outorgas_federais_superficial/FeatureServer/4", "superficial"),
    "SNIRH dados abertos — estadual subterrânea": ("https://portal1.snirh.gov.br/server/rest/services/Hosted/outorgas_estaduais_subterraneas/FeatureServer/0", "subterranea"),
}


def query_layer(url: str) -> gpd.GeoDataFrame:
    records: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": "ing_sg_ufmunicipio='BA' OR ING_SG_UFMUNICIPIO='BA'",
            "outFields": "*", "returnGeometry": "true", "outSR": "4326",
            "resultOffset": offset, "resultRecordCount": PAGE, "f": "geojson",
        }
        try:
            with urlopen(f"{url}/query?{urlencode(params)}", timeout=180) as response:
                payload = json.load(response)
        except Exception:
            # Some ArcGIS services only accept one case-sensitive field name.
            params["where"] = "ing_sg_ufmunicipio='BA'"
            with urlopen(f"{url}/query?{urlencode(params)}", timeout=180) as response:
                payload = json.load(response)
        batch = payload.get("features", [])
        records.extend(batch)
        if len(batch) < PAGE:
            break
        offset += len(batch)
    return gpd.GeoDataFrame.from_features(records, crs="EPSG:4326")


def val(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame:
            return frame[name]
    return pd.Series(pd.NA, index=frame.index)


def normalize(source: gpd.GeoDataFrame, source_name: str, water_type: str) -> gpd.GeoDataFrame:
    x = source.copy().to_crs("EPSG:4326")
    out = gpd.GeoDataFrame({
        "outorga_ato": val(x, "OUT_NU_ATO", "out_nu_ato").astype("string"),
        "codigo_cnarh": val(x, "INT_NU_CNARH", "int_nu_cnarh").astype("string"),
        "situacao_outorga": val(x, "TSP_DS", "tsp_ds").astype("string"),
        "situacao_interferencia": val(x, "TSF_DS", "tsf_ds").astype("string"),
        "tipo_interferencia": val(x, "TIN_DS", "tin_ds").astype("string"),
        "finalidade": val(x, "TFN_DS", "tfn_ds").astype("string"),
        "orgao_responsavel": val(x, "ORG_NM", "org_nm").astype("string"),
        "orgao_uf": val(x, "ORG_UF", "org_uf").astype("string"),
        "municipio": val(x, "ING_NM_MUNICIPIO", "ing_nm_municipio").astype("string"),
        "corpo_hidrico": val(x, "INT_NM_CORPOHIDRICO", "int_nm_corpohidrico").astype("string"),
        "ottobacia_trecho": val(x, "ING_CD_OTTOBACIA_TRECHO", "ing_cd_ottobacia_trecho").astype("string"),
        "vazao_max_m3_h": pd.to_numeric(val(x, "INT_QT_VAZAOMAXIMA", "int_qt_vazaomaxima"), errors="coerce"),
        "vazao_media_m3_h": pd.to_numeric(val(x, "INT_QT_VAZAOMEDIA", "int_qt_vazaomedia"), errors="coerce"),
        "volume_anual_m3": pd.to_numeric(val(x, "INT_QT_VOLUMEANUAL", "int_qt_volumeanual"), errors="coerce"),
        "validade_fonte": val(x, "OUTORGA_VALIDA", "outorga_valida").astype("string"),
        "data_inicio": val(x, "DT_OUTORGAINICIAL", "out_dt_outorgainicial").astype("string"),
        "data_vencimento": val(x, "DT_OUTORGAFINAL", "out_dt_outorgafinal").astype("string"),
        "tipo_agua": water_type,
        "fonte_oficial": source_name,
        "data_extracao": str(date.today()),
        "geometry": x.geometry,
    }, geometry="geometry", crs="EPSG:4326")
    expiry = pd.to_datetime(out["data_vencimento"], dayfirst=True, errors="coerce")
    valid_flag = out["validade_fonte"].fillna("1").astype(str).eq("1")
    status_ok = out["situacao_outorga"].fillna("").str.casefold().eq("outorgado")
    # The groundwater CNARH public view does not populate the operational
    # situation field.  Missing is therefore unknown/acceptable there, while
    # an explicit non-operational surface record remains inactive.
    operation = out["situacao_interferencia"].fillna("").str.casefold()
    operation_ok = operation.eq("") | operation.isin(["operação", "operacao"])
    # Null expiry is unknown, not automatically expired.  It is retained but
    # marked separately so it cannot silently become a high-confidence record.
    date_ok = expiry.isna() | expiry.ge(pd.Timestamp(date.today()))
    out["outorga_ativa"] = valid_flag & status_ok & operation_ok & date_ok
    out["geometria_valida"] = out.geometry.notna() & ~out.geometry.is_empty & out.geometry.is_valid
    out["status_validacao"] = "ativa"
    out.loc[~out["geometria_valida"], "status_validacao"] = "sem_geometria_valida"
    out.loc[out["geometria_valida"] & ~out["outorga_ativa"], "status_validacao"] = "inativa_ou_nao_confirmada"
    return out


def add_legacy_surface() -> gpd.GeoDataFrame:
    legacy = gpd.read_parquet(RAW / "outorgas_superficiais_bahia_cnarh.geoparquet")
    return normalize(legacy, "SNIRH/CNARH — IG estadual e federal superficial", "superficial")


def deduplicate(data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    x = data[data["geometria_valida"]].copy()
    x["coord_key"] = x.geometry.x.round(5).astype(str) + ":" + x.geometry.y.round(5).astype(str)
    identifier = x["outorga_ato"].fillna("").str.strip()
    identifier = identifier.mask(identifier.eq(""), x["codigo_cnarh"].fillna("").str.strip())
    # Act numbers can be reused by distinct state/federal or surface/groundwater
    # administrative workflows.  Water type is therefore part of the key.
    x["outorga_chave"] = x["tipo_agua"] + ":" + identifier
    x.loc[identifier.eq(""), "outorga_chave"] = x.loc[identifier.eq(""), "tipo_agua"] + ":" + x.loc[identifier.eq(""), "coord_key"]
    sources = x.groupby("outorga_chave")["fonte_oficial"].agg(lambda z: " | ".join(sorted(set(z)))).rename("fontes_oficiais")
    # Prefer the DADOSABERTOS record, then retain all provenance in one field.
    x["priority"] = x["fonte_oficial"].str.contains("dados abertos", case=False, na=False).astype(int)
    x = x.sort_values(["outorga_chave", "priority"], ascending=[True, False]).drop_duplicates("outorga_chave", keep="first")
    return x.merge(sources, on="outorga_chave", how="left").drop(columns=["priority", "coord_key"])


def main() -> None:
    frames = [add_legacy_surface()]
    for name, (url, water_type) in LAYERS.items():
        layer = query_layer(url)
        if not layer.empty:
            frames.append(normalize(layer, name, water_type))
    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    canonical = deduplicate(merged)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    canonical.to_parquet(OUT, index=False)
    print(f"Registros brutos: {len(merged):,}; outorgas canônicas georreferenciadas: {len(canonical):,}; ativas: {int(canonical.outorga_ativa.sum()):,}.")
    print(canonical.groupby(["tipo_agua", "outorga_ativa"]).size().to_dict())


if __name__ == "__main__":
    main()

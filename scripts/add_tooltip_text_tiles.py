"""Add a ready-made pt-BR tooltip (``tt``) to existing individual-map tiles.

pydeck accepts one tooltip template per map.  When a map stacks layers with
different attributes, or a unit lacks one field (e.g. hexagons with no OSM
route), the template prints raw placeholders such as ``{nota_mapa}``.  Each
feature therefore carries its finished pt-BR text split in lines ``t0…t6``
and every map uses the same template (``TOOLTIP_TEMPLATE``).

Attributes are never recomputed.  With ``--fix-hex-geometry`` each feature is
also redrawn with the true 5 km hexagon that contains it: the original tile
builders simplified with ~78 km / 2^z tolerance, which turned every hexagon
into a quadrilateral at zooms 4–5 and left gaps (a dark honeycomb over the
basemap).  The hexagon is matched by the centroid of the drawn fragment and
clipped to the tile, so values and classes stay exactly as published.
Without the flag, geometries are re-encoded unchanged.  Zero-area slivers,
which are invisible, are dropped by the encoder.

Usage::

    python scripts/add_tooltip_text_tiles.py --kind agua \
        --source static/hex_water_security_tiles_v8 --output static/hex_water_security_tiles_v9
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import shapely
from shapely.affinity import affine_transform
from shapely.geometry import box, shape

HEX_GRID = Path("data/processed/bahia_hex_5km.geojson")
# Only clipped border hexagons have detail to simplify; a regular hexagon
# keeps its 6 vertices with any tolerance below ~1 km.
HEX_TOLERANCE_DEG = {4: 0.004, 5: 0.002, 6: 0.001, 7: 0.0005}


TOOLTIP_LINES = 7
#: pydeck template shared by every individual map.  Each line is a separate
#: attribute so repeated values (titles, classes, sources, rounded numbers)
#: are stored once per tile instead of inside one long unique string.
TOOLTIP_TEMPLATE = "<b>{t0}</b>" + "".join(f"{{t{i}}}" for i in range(1, TOOLTIP_LINES))


def tooltip_properties(html: str) -> dict:
    """Split ``<b>Title</b><br/>line<br/>line`` into the fields t0…t6."""
    title, *lines = re.sub(r"</?b>", "", html).split("<br/>")
    if len(lines) > TOOLTIP_LINES - 1:
        raise ValueError(f"Tooltip com mais de {TOOLTIP_LINES - 1} linhas: {html}")
    fields = {"t0": title}
    for index in range(1, TOOLTIP_LINES):
        fields[f"t{index}"] = f"<br/>{lines[index - 1]}" if index <= len(lines) else ""
    return fields


def num(value: object, places: int = 1) -> str:
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return "sem dado"
    text = f"{float(value):,.{places}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def count(value: object) -> str:
    return num(value, 0)


CONFIDENCE = {"low": "baixa", "medium": "média", "high": "alta", "moderada": "moderada",
              "baixa": "baixa", "média": "média", "alta": "alta"}
ALERTS = {
    "unidade_conservacao": "unidade de conservação",
    "terra_indigena": "terra indígena",
    "embargo_ibama_poligonal": "embargo Ibama poligonal",
    "embargo_ibama_pontual": "embargo Ibama pontual",
    "RAIS_2024_PENDENTE": "RAIS 2024 ainda não incorporada",
    "CENSO_AGRO_2017_DEFASADO": "Censo Agropecuário de 2017",
}
CLIMATE_FIELDS = {
    "clima_p_mm": ("Precipitação anual", "mm/ano", 0),
    "clima_pe_mm": ("Precipitação efetiva", "mm/ano", 0),
    "clima_dryseq_months": ("Sequência seca", "meses", 1),
    "clima_tmean_c": ("Temperatura média", "°C", 1),
    "clima_tmax_p95_c": ("Temperatura máxima P95", "°C", 1),
    "clima_demanda_irrigacao_p80_mm": ("Déficit de irrigação P80", "mm/ano", 0),
}


def confidence(p: dict) -> str:
    return CONFIDENCE.get(str(p.get("confianca", "")), str(p.get("confianca") or "sem dado"))


def alerts(p: dict) -> str:
    try:
        items = json.loads(p.get("alertas") or "[]")
    except (TypeError, ValueError):
        items = []
    return "; ".join(ALERTS.get(item, item.replace("_", " ").lower()) for item in items) or "nenhum"


def score_line(p: dict, label: str = "Nota") -> str:
    if p.get("nota_mapa") is None:
        return f"{label}: sem nota · {p.get('classe_mapa') or 'sem dados'}"
    return f"{label}: {num(p['nota_mapa'])}/100 · {p.get('classe_mapa') or ''}".rstrip(" ·")


def tt_clima(p: dict) -> str:
    return ("<b>Clima e irrigação</b><br/>" + score_line(p)
            + f"<br/>Temperatura média: {num(p.get('clima_tmean_c'))} °C"
            + f"<br/>Temperatura máxima P95: {num(p.get('clima_tmax_p95_c'))} °C"
            + f"<br/>Déficit de irrigação P80: {num(p.get('clima_demanda_irrigacao_p80_mm'), 0)} mm/ano"
            + f"<br/>Sequência seca: {num(p.get('clima_dryseq_months'))} meses"
            + f"<br/>Precipitação efetiva: {num(p.get('clima_pe_mm'), 0)} mm/ano")


def tt_clima_ref(p: dict) -> str:
    label, unit, places = CLIMATE_FIELDS.get(p.get("campo"), (str(p.get("campo")), "", 1))
    return f"<b>Referência climática (CHIRPS/ERA5-Land)</b><br/>{label}: {num(p.get('valor'), places)} {unit}".rstrip()


def tt_agua(p: dict) -> str:
    surface = "sim" if p.get("outorga_ativa_no_imovel") else "não"
    return ("<b>Segurança hídrica — evidência preliminar</b><br/>" + score_line(p)
            + f"<br/>Captação superficial outorgada ativa na unidade: {surface} ({count(p.get('outorga_ativa_qtd'))})"
            + f"<br/>Outorgas subterrâneas ativas: {count(p.get('outorga_subterranea_ativa_qtd'))}"
            + f" · inativas ou não confirmadas: {count(p.get('outorga_subterranea_inativa_qtd'))}"
            + f"<br/>Poços ativos com teste: na unidade {count(p.get('pocos_ativos_testados_no_imovel'))}"
            + f" · até 2 km {count(p.get('pocos_ativos_testados_2km'))}"
            + f"<br/>Poços incertos, inativos ou sem teste: {count(p.get('pocos_incertos_no_imovel'))}"
            + f"<br/>Confiança: {confidence(p)}")


def tt_terra(p: dict) -> str:
    return ("<b>Referência regional de custo da terra</b>"
            + f"<br/>VTN agrícola: R$ {num(p.get('vtn_rs_ha'), 0)}/ha · {p.get('classe_mapa') or ''}"
            + f"<br/>Nota de custo: {num(p.get('nota_mapa'))}/100"
            + f"<br/>Região de referência: {p.get('mrt_id') or 'sem dado'} · fonte {p.get('origem') or 'sem dado'} {p.get('ano_base') or ''}".rstrip()
            + f"<br/>Confiança: {confidence(p)} · intervalo indicativo de 80%: ±{num(p.get('intervalo_80_pct'))}%")


def tt_logistica(p: dict) -> str:
    if p.get("distancia_rota_km") is None:
        distance = "sem rota na malha OSM (validação necessária)"
    else:
        distance = f"{num(p.get('distancia_rota_km'), 0)} km"
    return ("<b>Logística até Ilhéus</b><br/>" + score_line(p)
            + f"<br/>Distância rodoviária: {distance}"
            + f"<br/>Acesso até a estrada mais próxima: {num(p.get('distancia_snap_km'), 2)} km"
            + f"<br/>Confiança: {confidence(p)}"
            + f"<br/>Destino: {p.get('destino') or 'sem dado'}")


def tt_prodes(p: dict) -> str:
    return ("<b>PRODES e embargos</b>"
            + f"<br/>{p.get('classe_mapa') or 'sem classe'}"
            + f"<br/>PRODES até 2008: {num(p.get('prodes_ate_2008_ha'), 0)} ha · 2009–2014: {num(p.get('prodes_2009_2014_ha'), 0)} ha"
            + f"<br/>PRODES 2015–2020: {num(p.get('prodes_2015_2020_ha'), 0)} ha · a partir de 2021: {num(p.get('prodes_pos_2020_ha'), 0)} ha"
            + f"<br/>Embargo Ibama: {num(p.get('embargo_poligonal_ha'), 1)} ha em polígono · {count(p.get('embargo_pontual_qtd'))} pontual(is)"
            + f"<br/>UC/TI na unidade: {num(p.get('uc_pct'), 1)}% / {num(p.get('ti_pct'), 1)}% (alerta, não muda a cor)"
            + f"<br/>Alertas: {alerts(p)}")


def tt_mao_obra(p: dict) -> str:
    # ``classe_mapa`` belongs to the theme being drawn (índice, pessoal rural
    # or população), so only the index score is shown here.
    score = "sem nota" if p.get("nota_mapa") is None else f"{num(p['nota_mapa'])}/100"
    return ("<b>Contexto municipal de mão de obra</b><br/>" + f"Nota preliminar do índice: {score}"
            + f"<br/>Pessoal ocupado rural (Censo Agro 2017): {count(p.get('pessoal_rural_2017'))}"
            + f"<br/>População (Censo 2022): {count(p.get('populacao_2022'))}"
            + f"<br/>Município predominante: {p.get('municipio') or 'sem dado'} ({num(p.get('cobertura_pct'))}% da unidade)"
            + f"<br/>Confiança: {confidence(p)}"
            + f"<br/>Observações: {alerts(p)}")


KINDS = {"clima": tt_clima, "clima_ref": tt_clima_ref, "agua": tt_agua, "terra": tt_terra,
         "logistica": tt_logistica, "prodes": tt_prodes, "mao_obra": tt_mao_obra}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fix-hex-geometry", action="store_true",
                        help="Redesenha cada feição com o hexágono verdadeiro da grade de 5 km.")
    args = parser.parse_args()
    grid = index = None
    if args.fix_hex_geometry:
        grid = gpd.read_file(HEX_GRID).to_crs("EPSG:4326")
        grid["geometry"] = grid.geometry.make_valid()
        index = grid.sindex
    if args.output.resolve() == args.source.resolve():
        raise SystemExit("Use uma pasta de saída nova (nova versão dos tiles).")
    make = KINDS[args.kind]
    tiles = features = 0
    for path in sorted(args.source.rglob("*.pbf")):
        decoded = mapbox_vector_tile.decode(path.read_bytes())
        layers, extent = [], 4096
        z, x, y = (int(part) for part in (path.parts[-3], path.parts[-2], path.stem))
        bounds = mercantile.bounds(x, y, z)
        for name, layer in decoded.items():
            extent = layer.get("extent", extent)
            items = []
            for feature in layer["features"]:
                # Colours come from the category folder, so the rendered tile
                # needs only the finished tooltip; raw fields stay in the
                # analytical tables and in the previous tile version.
                properties = tooltip_properties(make(dict(feature["properties"])))
                geometry = shape(feature["geometry"])
                if grid is not None:
                    ext = layer.get("extent", 4096)
                    sx, sy = (bounds.east - bounds.west) / ext, (bounds.north - bounds.south) / ext
                    # Decoded tile coordinates are y-up in [0, extent].
                    geometry = affine_transform(geometry, [sx, 0, 0, sy, bounds.west, bounds.south])
                    hit = index.query(geometry.representative_point(), predicate="intersects")
                    if len(hit):
                        exact = shapely.intersection(grid.geometry.iloc[hit[0]], box(bounds.west, bounds.south, bounds.east, bounds.north))
                        exact = exact.simplify(HEX_TOLERANCE_DEG.get(z, 0.0005), preserve_topology=True)
                        if not exact.is_empty and exact.geom_type in ("Polygon", "MultiPolygon"):
                            geometry = exact
                item = {"geometry": geometry, "properties": properties}
                if feature.get("id") is not None:
                    item["id"] = feature["id"]
                items.append(item)
            features += len(items)
            layers.append({"name": name, "features": items})
        destination = args.output / path.relative_to(args.source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        options = {"extents": extent}
        if grid is not None:
            options["quantize_bounds"] = (bounds.west, bounds.south, bounds.east, bounds.north)
        destination.write_bytes(mapbox_vector_tile.encode(layers, default_options=options))
        tiles += 1
    print(f"{args.kind}: {tiles:,} tiles, {features:,} feições → {args.output}")


if __name__ == "__main__":
    main()

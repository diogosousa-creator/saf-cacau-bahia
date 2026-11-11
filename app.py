from __future__ import annotations

from html import escape
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import pydeck as pdk
import streamlit as st

from webgis.data import (
    has_georeferenced_farms,
    load_assessments,
    load_bahia_layers,
    load_farm_detail_lookup,
)
from webgis.parcel_map import parcel_map
from webgis.scoring import CRITERIA, TILE_SCORE_FIELDS, potential_class, weighted_score_available


st.set_page_config(page_title="SAF Cacau Bahia", page_icon="🍫", layout="wide", initial_sidebar_state="expanded")


def _streamlit_static_tile_url(tile_path: str) -> str:
    """Return a tile URL that survives Streamlit Cloud's ``/~/+/`` prefix.

    The main map is a custom component and already receives ``streamlitUrl``
    from the frontend.  The individual PyDeck maps run in Streamlit's own
    deck.gl worker, whose relative URLs are resolved from the worker bundle
    instead of the app document on Cloud.  Build an absolute app URL here so
    both renderers request the same immutable static tiles.
    """
    try:
        app_url = st.context.url
    except Exception:  # pragma: no cover - defensive for non-Streamlit tools
        app_url = None
    if not app_url:
        return tile_path

    parsed = urlsplit(app_url)
    app_path = parsed.path.rstrip("/")
    # Streamlit Community Cloud serves the app document under /~/+/. Local
    # Streamlit deliberately has no such prefix.
    if parsed.netloc.endswith(".streamlit.app") and not app_path.endswith("/~/+"):
        app_path = f"{app_path}/~/+"
    base = urlunsplit((parsed.scheme, parsed.netloc, app_path, "", "")).rstrip("/")
    return f"{base}/{tile_path.lstrip('/')}"


_PYDECK_LAYER = pdk.Layer


def _webgis_layer(layer_type: str, data: object = None, *args: object, **kwargs: object) -> object:
    """Normalize only MVT static paths used by the individual thematic maps."""
    if layer_type == "MVTLayer" and isinstance(data, str) and data.startswith("app/static/"):
        data = _streamlit_static_tile_url(data)
    return _PYDECK_LAYER(layer_type, data, *args, **kwargs)


# Keep all existing layer declarations compact while applying the URL fix to
# every individual-map source (soil, relief, climate, water, logistics, etc.).
pdk.Layer = _webgis_layer


def virtual_fill_expression(weights: dict[str, int], alpha: int = 185) -> str:
    """Compact deck.gl expression evaluated against each MVT feature.

    ``s0`` … ``s7`` are immutable criterion scores kept in the tile.  The
    expression changes with the sidebar weights; the geometry tile itself does
    not. ``-1`` means no source data for that criterion.
    """
    terms = []
    denominators = []
    # Os campos compactos s0…s7 têm ordem física estável nos MVTs.  A ordem
    # visual de CRITERIA é independente e pode acompanhar o fluxo de análise.
    for index, key in enumerate(TILE_SCORE_FIELDS):
        weight = int(weights[key])
        score = f"properties.s{index}"
        terms.append(f"({score} >= 0 ? {weight} * {score} : 0)")
        denominators.append(f"({score} >= 0 ? {weight} : 0)")
    numerator = " + ".join(terms)
    denominator = " + ".join(denominators)
    score = f"(({numerator}) / ({denominator}))"
    return (
        f"({denominator}) <= 0 ? [196,202,208,{alpha}] : "
        f"{score} < 35 ? [197,52,47,{alpha}] : "
        f"{score} < 50 ? [235,122,47,{alpha}] : "
        f"{score} < 65 ? [246,204,63,{alpha}] : "
        f"{score} < 80 ? [152,191,83,{alpha}] : [52,139,75,{alpha}]"
    )


def _selected_feature_properties(event: object) -> dict[str, object]:
    """Read a Pydeck selected object without depending on layer internals."""
    if not event:
        return {}
    selection = event.get("selection", {}) if hasattr(event, "get") else {}
    objects = selection.get("objects", {}) if isinstance(selection, dict) else {}
    if not isinstance(objects, dict):
        return {}
    for picked in objects.values():
        if isinstance(picked, list):
            picked = picked[0] if picked else {}
        if not isinstance(picked, dict):
            continue
        feature = picked.get("object", picked)
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties", feature)
        if isinstance(properties, dict):
            return properties
    return {}


def _farm_detail_html(farm_id: str | None) -> str:
    """Build the small property-side panel from the master, non-spatial lookup."""
    if not farm_id:
        return (
            '<div class="property-detail property-detail-empty">'
            '<strong>Imóvel certificado</strong><p>Clique em um polígono SIGEF/SNCI '
            'para consultar a ficha técnica da parcela.</p>'
            '<small>A consulta é feita somente após o clique.</small></div>'
        )

    lookup = load_farm_detail_lookup()
    if farm_id not in lookup.index:
        return (
            '<div class="property-detail property-detail-empty"><strong>Seleção territorial</strong>'
            '<p>Amplie o mapa até visualizar um imóvel certificado e selecione seu polígono.</p></div>'
        )

    farm = lookup.loc[farm_id]
    raw_reference = farm.get("referencia_exibicao")
    reference = escape(farm_id if pd.isna(raw_reference) or not str(raw_reference).strip() else str(raw_reference))
    area = pd.to_numeric(pd.Series([farm.get("area_ha")]), errors="coerce").iloc[0]
    area_text = f"{float(area):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if pd.notna(area) else "Sem dado"
    rows = []
    for field, label in CRITERIA.items():
        value = pd.to_numeric(pd.Series([farm.get(field)]), errors="coerce").iloc[0]
        if pd.isna(value):
            rows.append(f'<div class="criterion-row"><span>{escape(label)}</span><b>—</b><i class="criterion-bar missing"></i></div>')
            continue
        score = max(0.0, min(100.0, float(value)))
        color = "#348b4b" if score >= 80 else "#98bf53" if score >= 65 else "#f6cc3f" if score >= 50 else "#eb7a2f" if score >= 35 else "#c5342f"
        rows.append(
            f'<div class="criterion-row"><span>{escape(label)}</span><b>{score:.0f}</b>'
            f'<i class="criterion-bar"><em style="width:{score:.1f}%;background:{color}"></em></i></div>'
        )
    return (
        '<div class="property-detail">'
        '<span class="detail-eyebrow">IMÓVEL CERTIFICADO</span>'
        f'<h3>{reference}</h3><p class="detail-area">{area_text} ha</p>'
        '<div class="detail-rule"></div><strong class="detail-heading">Perfil por critério</strong>'
        f'<div class="criteria-list">{"".join(rows)}</div></div>'
    )


def _farm_search_options(query: str) -> dict[str, str]:
    """Return at most 20 matching identifiers, only after the analyst searches."""
    normalized = query.strip().casefold()
    if len(normalized) < 3:
        return {}
    lookup = load_farm_detail_lookup()
    reference = lookup["referencia_exibicao"].fillna("").astype(str)
    mask = reference.str.casefold().str.contains(normalized, regex=False) | lookup.index.to_series().astype(str).str.casefold().str.contains(normalized, regex=False)
    matches = lookup.loc[mask, ["referencia_exibicao", "area_ha"]].head(20)
    options: dict[str, str] = {}
    for identifier, row in matches.iterrows():
        label = str(row["referencia_exibicao"]) if pd.notna(row["referencia_exibicao"]) else str(identifier)
        options[str(identifier)] = label
    return options


def apply_hf_layout() -> None:
    st.markdown(
        """
        <style>
          :root { --hf-blue:#0d2743; --hf-green:#4dc270; --hf-wash:#e4f6ea; --hf-paper:#f9f9f9; --hf-line:#e3e3e3; --hf-ink:#0d0d0d; --hf-soft:#4a4a4a; }
          .stApp { background:#fff; color:var(--hf-ink); font-family:Inter,Aptos,"Segoe UI",sans-serif; }
          header[data-testid="stHeader"] { background:transparent; }
          .main .block-container { max-width:1440px; padding:1.25rem 3rem 4.5rem; }
          section[data-testid="stSidebar"] { background:var(--hf-paper); border-right:1px solid var(--hf-line); }
          /* Streamlit keeps the sidebar DOM mounted during its collapse animation.
             Without these rules its wide child controls can briefly reflow into a
             narrow strip (letters one below another).  In the collapsed state the
             content must be fully removed from layout; the native reopen button is
             outside this content node and remains available. */
          section[data-testid="stSidebar"][aria-expanded="false"] {
            width:0 !important; min-width:0 !important; max-width:0 !important;
            flex:0 0 0 !important; overflow:hidden !important; border-right:0 !important;
          }
          section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarContent"],
          section[data-testid="stSidebar"][aria-expanded="false"] > div:first-child {
            display:none !important; width:0 !important; min-width:0 !important;
            max-width:0 !important; margin:0 !important; padding:0 !important;
            overflow:hidden !important;
          }
          section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 { color:var(--hf-blue); }
          section[data-testid="stSidebar"] label,
          section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
          section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] span,
          section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
          section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
            color:var(--hf-ink) !important;
            opacity:1 !important;
          }
          section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
          section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color:var(--hf-soft) !important; }
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] {
            margin:.5rem 0 .35rem; padding:.72rem .8rem; border:1px solid #d8e1e9;
            border-radius:14px; background:#fff; box-shadow:0 4px 12px rgba(13,39,67,.04);
          }
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] label,
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] span,
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] p { color:var(--hf-blue) !important; opacity:1 !important; font-weight:650 !important; }
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] button[role="switch"] {
            min-width:2.6rem !important; height:1.45rem !important; margin-right:.6rem;
            border:0 !important; border-radius:999px !important; background:#cbd7e4 !important; box-shadow:none !important;
          }
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] button[role="switch"][aria-checked="true"] { background:var(--hf-green) !important; }
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] button[role="switch"] > div { background:#fff !important; box-shadow:0 1px 3px rgba(13,39,67,.22) !important; }
          section[data-testid="stSidebar"] [data-testid="stCheckbox"] label:has(input:checked) > div:first-of-type {
            background:var(--hf-green) !important; border-color:var(--hf-green) !important;
          }
          h1,h2,h3 { color:var(--hf-blue); letter-spacing:-.025em; font-weight:650; }
          h2 { margin-top:2.8rem; }
          div[data-testid="stMetric"] { min-height:116px; padding:1.1rem 1.25rem; border:1px solid var(--hf-line); border-radius:16px; background:#f4f4f4; box-shadow:0 12px 32px rgba(13,39,67,.05); }
          div[data-testid="stMetricLabel"] p { color:var(--hf-soft); font-size:.88rem; font-weight:600; }
          div[data-testid="stMetricValue"] { color:var(--hf-blue); font-size:2rem; font-weight:650; }
          div[data-testid="stAlert"] { border-radius:14px; border:1px solid #d8e8b0; background:#f3f7dd; color:var(--hf-ink); }
          .hf-eyebrow { display:block; margin:0 0 .5rem; padding:0; color:#607386; font-size:.82rem; font-weight:650; letter-spacing:.075em; text-transform:uppercase; }
          .hf-heading { margin:0; color:var(--hf-blue); font-size:clamp(3rem,5vw,5.2rem); line-height:.98; letter-spacing:-.055em; font-weight:680; }
          /* Mantém a hierarquia nova de tamanhos, mas cada nome conserva sua
             identidade original: análise institucional espaçada; produto forte. */
          .hf-territorial-title { color:#607386; font-weight:650; letter-spacing:.075em; text-transform:uppercase; }
          .hf-project-name { margin:.82rem 0 0; color:var(--hf-ink); font-size:clamp(1.45rem,2.5vw,2.35rem); font-weight:680; letter-spacing:-.055em; line-height:1.05; text-transform:none; }
          .hf-subtitle { max-width:760px; margin:.9rem 0 2.55rem; color:var(--hf-soft); font-size:1rem; line-height:1.58; }
          .hf-map-caption { margin:-.4rem 0 1rem; color:var(--hf-soft); font-size:.92rem; }
          div.stElementContainer:has(#map-legend) { position:relative; z-index:5; margin-top:-3.9rem; margin-left:1rem; margin-bottom:2rem; pointer-events:none; }
          #map-legend { display:flex; align-items:center; gap:.75rem; width:max-content; max-width:calc(100vw - 4rem); padding:.62rem .78rem; border:1px solid rgba(13,39,67,.12); border-radius:12px; background:rgba(255,255,255,.94); box-shadow:0 8px 22px rgba(13,39,67,.12); backdrop-filter:blur(8px); color:var(--hf-blue); font-size:.76rem; font-weight:600; }
          #map-legend strong { margin-right:.15rem; font-size:.78rem; }
          #map-legend .legend-item { display:inline-flex; align-items:center; gap:.34rem; white-space:nowrap; }
          #map-legend i { display:inline-block; width:.7rem; height:.7rem; border-radius:3px; border:1px solid rgba(13,39,67,.18); }
          #map-legend .very-low { background:#c5342f; } #map-legend .low { background:#eb7a2f; } #map-legend .medium { background:#f6cc3f; } #map-legend .high { background:#98bf53; } #map-legend .very-high { background:#348b4b; }
          div.stElementContainer:has(#soil-legend) { position:relative; z-index:5; margin-top:-3.9rem; margin-left:1rem; margin-bottom:2rem; pointer-events:none; }
          #soil-legend { display:flex; align-items:center; gap:.6rem; width:max-content; max-width:calc(100vw - 4rem); padding:.55rem .72rem; border:1px solid rgba(13,39,67,.12); border-radius:12px; background:rgba(255,255,255,.94); box-shadow:0 8px 22px rgba(13,39,67,.12); backdrop-filter:blur(8px); color:var(--hf-blue); font-size:.72rem; font-weight:600; }
          #soil-legend strong { margin-right:.1rem; } #soil-legend .legend-item { display:inline-flex; align-items:center; gap:.3rem; white-space:nowrap; } #soil-legend i { display:inline-block; width:.68rem; height:.68rem; border-radius:3px; border:1px solid rgba(13,39,67,.18); }
          #soil-legend .rock { background:#707070; } #soil-legend .very-clay { background:#a83800; } #soil-legend .clay { background:#aa8686; } #soil-legend .silt { background:#298289; } #soil-legend .sand { background:#fffe73; } #soil-legend .medium-texture { background:#d7c5a5; }
          #soil-legend .very-low { background:#c5342f; } #soil-legend .low { background:#eb7a2f; } #soil-legend .medium { background:#f6cc3f; } #soil-legend .high { background:#98bf53; } #soil-legend .very-high { background:#348b4b; }
          #source-legend { display:flex; align-items:center; flex-wrap:wrap; gap:.48rem .72rem; max-width:100%; margin:.45rem 0 1rem; padding:.52rem .7rem; border-left:2px solid #d8e1e9; color:#40556c; font-size:.72rem; line-height:1.35; }
          #source-legend strong { color:var(--hf-blue); margin-right:.18rem; } #source-legend .legend-item { display:inline-flex; align-items:center; gap:.28rem; white-space:nowrap; } #source-legend i { display:inline-block; width:.64rem; height:.64rem; border-radius:3px; border:1px solid rgba(13,39,67,.18); }
          #source-legend .rock { background:#707070; } #source-legend .very-clay { background:#a83800; } #source-legend .clay { background:#aa8686; } #source-legend .silt { background:#298289; } #source-legend .sand { background:#fffe73; } #source-legend .medium-texture { background:#d7c5a5; }
          #source-legend .latossolo { background:#f7d1a6; } #source-legend .argissolo { background:#eca59d; } #source-legend .cambissolo { background:#be9f7f; } #source-legend .neossolo { background:#ded5bb; } #source-legend .gleissolo { background:#92bcd9; } #source-legend .other-pedology { background:#bebebe; }
          #source-legend .slope-flat { background:#348b4b; } #source-legend .slope-soft { background:#98bf53; } #source-legend .slope-mid { background:#f6cc3f; } #source-legend .slope-steep { background:#eb7a2f; } #source-legend .slope-very-steep { background:#c5342f; }
          #source-legend .rain-1 { background:#f7fbff; } #source-legend .rain-2 { background:#c6dbef; } #source-legend .rain-3 { background:#6baed6; } #source-legend .rain-4 { background:#3182bd; } #source-legend .rain-5 { background:#08519c; }
          #source-legend .heat-1 { background:#ffffcc; } #source-legend .heat-2 { background:#fed976; } #source-legend .heat-3 { background:#fd8d3c; } #source-legend .heat-4 { background:#e31a1c; } #source-legend .heat-5 { background:#800026; }
          #source-legend .demand-1 { background:#f2f0f7; } #source-legend .demand-2 { background:#cbc9e2; } #source-legend .demand-3 { background:#9e9ac8; } #source-legend .demand-4 { background:#756bb1; } #source-legend .demand-5 { background:#54278f; }
          #source-legend .water-seasonal { background:#53a8bf; } #source-legend .water-persistent { background:#15699d; } #source-legend .bho { background:#206f9e; } #source-legend .well { background:#145682; border-radius:50%; } #source-legend .prodes-old { background:#348b4b; } #source-legend .prodes-mid { background:#98bf53; } #source-legend .prodes-new { background:#f6cc3f; } #source-legend .embargo { background:#80202b; } #source-legend .uc { background:#435c7d; } #source-legend .ti { background:#63488b; }
          .soil-description { margin:.1rem 0 1rem; color:#657383; font-size:1rem; line-height:1.5; }
          .soil-methodology, .soil-methodology * { max-width:980px; margin:.1rem 0 1.4rem; color:#111 !important; font-size:.66rem !important; line-height:1.42; }
          @media (max-width: 760px) { #map-legend { gap:.45rem; padding:.5rem .6rem; font-size:.66rem; } #map-legend strong { display:none; } div.stElementContainer:has(#map-legend) { margin-top:-3.55rem; } }
          section[data-testid="stSidebar"] .stMultiSelect div[data-baseweb="select"] > div,
          section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div { border-radius:12px !important; border:1px solid #d8e1e9 !important; background:#fff !important; color:var(--hf-blue) !important; }
          section[data-testid="stSidebar"] span[data-baseweb="tag"] { background:var(--hf-blue) !important; border-radius:8px !important; color:#fff !important; }
          section[data-testid="stSidebar"] span[data-baseweb="tag"] *,
          section[data-testid="stSidebar"] span[data-baseweb="tag"] svg { color:#fff !important; fill:#fff !important; }
          section[data-testid="stSidebar"] [data-baseweb="slider"] div[role="slider"] { background:var(--hf-blue) !important; border-color:#fff !important; }
          section[data-testid="stSidebar"] [data-baseweb="slider"] > div > div > div { background-color:#d8e1e9 !important; }
          section[data-testid="stSidebar"] [data-baseweb="slider"] > div > div > div:first-child { background-color:var(--hf-blue) !important; }
          section[data-testid="stSidebar"] [data-baseweb="slider"] * { color:var(--hf-blue) !important; }
          .stDataFrame { border:1px solid var(--hf-line); border-radius:16px; overflow:hidden; }
          div[data-testid="stExpander"] { margin-top:1.5rem; border:1px solid #d8e1e9; border-radius:14px; background:#fff; box-shadow:none; }
          div[data-testid="stExpander"] summary { color:var(--hf-blue); font-weight:650; }
          .individual-map-intro { margin:.1rem 0 .8rem; color:var(--hf-soft); font-size:.92rem; }
          .individual-map-row { display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:.78rem 0; border-top:1px solid #e6ebf0; color:var(--hf-blue); font-weight:600; }
          .individual-map-row:first-of-type { border-top:0; }
          .individual-map-row small { color:#788899; font-size:.78rem; font-weight:500; }
          .property-detail { min-height:520px; box-sizing:border-box; padding:.55rem .15rem 0; color:var(--hf-blue); }
          .property-detail-empty { display:flex; flex-direction:column; justify-content:center; color:#607386; }
          .property-detail-empty strong { color:var(--hf-blue); font-size:1rem; }
          .property-detail-empty p { margin:.55rem 0; font-size:.88rem; line-height:1.45; }
          .property-detail-empty small { font-size:.72rem; line-height:1.35; }
          .detail-eyebrow { display:block; margin-bottom:.45rem; color:#607386; font-size:.63rem; font-weight:750; letter-spacing:.08em; }
          .property-detail h3 { margin:0; overflow-wrap:anywhere; font-size:.98rem; line-height:1.25; letter-spacing:-.015em; }
          .detail-area { margin:.35rem 0 .85rem; color:#526577; font-size:.88rem; font-weight:600; }
          .detail-rule { height:1px; margin:.8rem 0; background:#e3e9ef; }
          .detail-heading { display:block; margin-bottom:.7rem; font-size:.82rem; }
          .criteria-list { display:grid; gap:.46rem; }
          .criterion-row { display:grid; grid-template-columns:minmax(0,1fr) 25px 58px; align-items:center; gap:.4rem; font-size:.72rem; }
          .criterion-row span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#40566d; }
          .criterion-row b { color:var(--hf-blue); font-size:.72rem; text-align:right; }
          .criterion-bar { display:block; overflow:hidden; height:5px; border-radius:5px; background:#e6ebf0; }
          .criterion-bar em { display:block; height:100%; border-radius:5px; }
          .criterion-bar.missing { background:repeating-linear-gradient(135deg,#e6ebf0 0,#e6ebf0 4px,#f7f8f9 4px,#f7f8f9 8px); }
          @media (max-width: 900px) { .main .block-container { padding:1rem 1.1rem 3rem; } .hf-heading { font-size:2.8rem; } .hf-project-name { font-size:1.35rem; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_hf_layout()
st.markdown('<h1 class="hf-heading hf-territorial-title">Análise territorial</h1>', unsafe_allow_html=True)
st.markdown('<span class="hf-eyebrow hf-project-name">SAF Cacau Bahia</span>', unsafe_allow_html=True)
st.markdown('<p class="hf-subtitle">WebGIS para priorizar áreas, imóveis certificados e microrregiões para o desenvolvimento de sistemas agroflorestais com cacau irrigado.</p>', unsafe_allow_html=True)

data, is_demo = load_assessments()
bahia_boundary, bahia_mask = load_bahia_layers()
farms_available = has_georeferenced_farms()
has_soil = not is_demo and data["tipo_solo"].notna().any()
has_climate = not is_demo and data["clima_irrigacao"].notna().any()
has_water_security = not is_demo and data["seguranca_hidrica"].notna().any()
has_socioenvironmental = not is_demo and data["risco_socioambiental"].notna().any()
has_logistics = not is_demo and data["logistica_ilheus"].notna().any()
has_land_cost = not is_demo and data["custo_terra"].notna().any()
if has_logistics:
    _log_min_km = float(data["logistica_ilheus_min_referencia_km"].dropna().iloc[0])
    _log_max_km = float(data["logistica_ilheus_max_referencia_km"].dropna().iloc[0])
    _log_span_km = _log_max_km - _log_min_km
    # These km limits are derived from the very same 80/65/50/35 score cuts
    # used in the main aptitude palette. This keeps both logistics views
    # colour-consistent while each feature retains its continuous score.
    _log_cut_80 = _log_max_km - .80 * _log_span_km
    _log_cut_65 = _log_max_km - .65 * _log_span_km
    _log_cut_50 = _log_max_km - .50 * _log_span_km
    _log_cut_35 = _log_max_km - .35 * _log_span_km

st.sidebar.header("Pesos do índice")
st.sidebar.caption("Ajuste de 0 a 10. Peso zero exclui o critério do cálculo.")
weights = {
    key: st.sidebar.slider(label, min_value=0, max_value=10, value=5, key=f"weight_{key}")
    for key, label in CRITERIA.items()
}
scored = data.copy()
# Campo virtual: cada unidade usa somente critérios com dados e peso acima de
# zero. Os valores originais permanecem inalterados na base analítica.
scored["nota_mapa"], scored["cobertura_pesos_pct"] = weighted_score_available(scored, weights)
scored["nota_mapa"] = scored["nota_mapa"].round(1)
scored["classe_mapa"] = scored["nota_mapa"].map(
    lambda value: potential_class(float(value)) if pd.notna(value) else "Sem dados"
)
map_title = "Aptidão geral SAF Cacau"


def suitability_color(score: float | None, alpha: int) -> list[int]:
    """Five-class potential scale: red, orange, yellow, light green, green."""
    if score is None or pd.isna(score):
        return [196, 202, 208, 120]
    if score < 35:
        return [197, 52, 47, alpha]      # muito baixo
    if score < 50:
        return [235, 122, 47, alpha]     # baixo
    if score < 65:
        return [246, 204, 63, alpha]     # médio
    if score < 80:
        return [152, 191, 83, alpha]     # alto
    return [52, 139, 75, alpha]          # muito alto


scored["fill_color"] = scored["nota_mapa"].map(lambda score: suitability_color(score, 155))
scored["line_color"] = scored["nota_mapa"].map(lambda score: suitability_color(score, 245))

TEXTURE_COLORS = {
    1: [112, 112, 112, 185], 2: [168, 56, 0, 185], 3: [170, 134, 134, 185],
    4: [41, 130, 137, 185], 5: [255, 254, 115, 185], 6: [215, 197, 165, 185],
}
SOIL_SCORE_LAYERS = [
    (1, [197, 52, 47, 155]), (2, [235, 122, 47, 155]), (3, [246, 204, 63, 155]),
    (4, [152, 191, 83, 155]), (5, [52, 139, 75, 155]),
]


# Sem filtros na interface: a leitura principal apresenta toda a Bahia e a
# camada certificada fica ativa automaticamente quando seus tiles existem.
show_farms = farms_available
filtered = scored

if filtered.empty:
    st.info("Nenhuma unidade atende aos filtros selecionados.")
else:
    hexagons = filtered[filtered["tipo"] == "Hexágono 5 km"]
    active_farm_id = st.session_state.get("active_farm_id")
    map_column, detail_column = st.columns([3.25, 1], gap="small")
    with map_column:
        selection_event = parcel_map(
            weights=weights,
            active_id=active_farm_id,
            view_state=st.session_state.get("mapa_aptidao_view_state"),
            bahia_mask=bahia_mask,
            bahia_boundary=bahia_boundary,
            key="mapa_aptidao_browser",
        )
        if isinstance(selection_event, dict):
            selected_id = str(selection_event.get("id") or "").strip()
            if selected_id:
                st.session_state["active_farm_id"] = selected_id
            selected_view = selection_event.get("view_state")
            if isinstance(selected_view, dict):
                st.session_state["mapa_aptidao_view_state"] = selected_view
        active_farm_id = st.session_state.get("active_farm_id")
    with detail_column:
        with st.container(height=640, border=True):
            search_query = st.text_input("Buscar parcela", placeholder="Código da parcela ou SIGEF", key="farm_search_query")
            search_options = _farm_search_options(search_query)
            if search_options:
                search_choice = st.selectbox(
                    "Resultados da busca",
                    options=[""] + list(search_options),
                    format_func=lambda value: "Selecionar parcela" if not value else search_options[value],
                    key="farm_search_match",
                )
                if search_choice and search_choice != active_farm_id:
                    st.session_state["active_farm_id"] = search_choice
                    st.rerun()
            elif len(search_query.strip()) >= 3:
                st.caption("Nenhuma parcela encontrada.")
            st.markdown(_farm_detail_html(active_farm_id), unsafe_allow_html=True)
    st.markdown(
        f'''<div id="map-legend"><strong>{map_title}</strong>
        <span class="legend-item"><i class="very-low"></i>Muito baixo</span>
        <span class="legend-item"><i class="low"></i>Baixo</span>
        <span class="legend-item"><i class="medium"></i>Médio</span>
        <span class="legend-item"><i class="high"></i>Alto</span>
        <span class="legend-item"><i class="very-high"></i>Muito alto</span></div>''',
        unsafe_allow_html=True,
    )

with st.expander("Mapas individualizados", expanded=False):
    st.markdown(
        '<p class="individual-map-intro">Camadas temáticas para leitura isolada de cada atributo do índice.</p>',
        unsafe_allow_html=True,
    )
    # Streamlit executa o conteúdo de expanders fechados.  Construir os oito
    # pydeck maps de uma vez fazia o navegador instanciar fontes e tiles de
    # temas que o usuário não pediu para ver.  A seleção preserva cada mapa e
    # seus dados, mas materializa somente o tema aberto nesta visualização.
    selected_individual_map = st.selectbox(
        "Selecionar mapa temático",
        list(CRITERIA.values()),
        key="selected_individual_map",
    )
    for map_title in (selected_individual_map,):
        if map_title == "Tipo de solo" and has_soil:
            with st.expander("Qualidade física do solo", expanded=False):
                st.markdown('<p class="soil-description">Textura superficial e contexto físico regional para triagem de SAF Cacau.</p>', unsafe_allow_html=True)
                controls = st.columns(3)
                with controls[0]:
                    show_soil_score = st.toggle("Qualidade física do solo", value=True, key="soil_show_score")
                with controls[1]:
                    show_soil_texture = st.toggle("Textura MapBiomas Solo", value=False, key="soil_ref_texture")
                with controls[2]:
                    show_soil_pedology = st.toggle("Pedologia IBGE", value=False, key="soil_ref_pedology")
                st.caption("Referências opcionais: textura dominante MapBiomas Solo (0–30 cm, resumida na unidade de análise) e contexto físico Pedologia IBGE 1:250.000. Não alteram a nota já calculada.")
                soil_layers = []
                if show_soil_score:
                    soil_layers.extend(
                        pdk.Layer(
                            "MVTLayer", f"app/static/hex_soil_tiles_v6/score/{category}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=4, max_zoom=7, filled=True, stroked=False, wireframe=False,
                            get_fill_color=color, get_line_color=[0, 0, 0, 0], get_line_width=0,
                            line_width_min_pixels=0, line_width_max_pixels=0, pickable=True,
                        )
                        for category, color in SOIL_SCORE_LAYERS
                    )
                if show_soil_texture:
                    # MapBiomas Solo Coleção 3 — grupamento textural 0–30 cm:
                    # palette published with the official legend, not the
                    # green-to-red suitability palette.
                    texture_colors = {
                        1: [112, 112, 112, 210], 2: [168, 56, 0, 210], 3: [170, 134, 134, 210],
                        4: [41, 130, 137, 210], 5: [255, 254, 115, 210], 6: [215, 197, 165, 210],
                    }
                    for category, color in texture_colors.items():
                        soil_layers.append(pdk.Layer(
                            "MVTLayer", f"app/static/hex_soil_tiles_v6/raw/{category}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=4, max_zoom=7, filled=True, stroked=False, get_fill_color=color, pickable=True,
                        ))
                if show_soil_pedology:
                    # Convenção cartográfica do Manual Técnico de Pedologia
                    # do IBGE.  Cores representam classes, não aptidão.
                    pedology_colors = {
                        "latossolo_amarelo": [254, 243, 175, 205],
                        "latossolo_vermelho": [244, 185, 128, 205],
                        "latossolo_vermelho_amarelo": [247, 209, 166, 205],
                        "latossolo_bruno": [236, 213, 131, 205],
                        "argissolo_amarelo": [253, 241, 240, 205],
                        "argissolo_vermelho": [240, 165, 157, 205],
                        "argissolo_vermelho_amarelo": [236, 193, 180, 205],
                        "argissolo_outro": [222, 193, 202, 205],
                        "cambissolo": [190, 159, 127, 205], "neossolo": [222, 213, 187, 205],
                        "luvissolo": [221, 202, 133, 205], "gleissolo": [146, 188, 217, 205],
                        "planossolo": [181, 199, 211, 205], "vertissolo": [138, 151, 126, 205],
                        "chernossolo": [93, 78, 63, 205], "espodossolo": [194, 174, 206, 205],
                        "organossolo": [103, 125, 102, 205], "outras_unidades": [190, 190, 190, 195],
                    }
                    for group, color in pedology_colors.items():
                        soil_layers.append(pdk.Layer(
                            "MVTLayer", f"app/static/solo_relevo_referencias_tiles_v1/pedologia_ibge/{group}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=5, max_zoom=7, filled=True, stroked=True,
                            get_fill_color=color, get_line_color=[90, 93, 92, 185],
                            line_width_units="pixels", line_width_min_pixels=0.45, pickable=True,
                        ))
                if bahia_mask:
                    soil_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    soil_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(
                    pdk.Deck(
                        layers=soil_layers,
                        initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15),
                        tooltip={"html": "<b>Qualidade física do solo</b><br/>Nota: {nota_mapa}/100<br/>Classe: {classe_mapa}<br/>Textura dominante: {solo_textura_dominante}"},
                    ),
                    height=500,
                )
                if show_soil_score:
                    st.markdown(
                        '''<div id="soil-legend"><strong>Qualidade física do solo — nota</strong>
                        <span class="legend-item"><i class="very-low"></i>Muito baixa</span>
                        <span class="legend-item"><i class="low"></i>Baixa</span>
                        <span class="legend-item"><i class="medium"></i>Média</span>
                        <span class="legend-item"><i class="high"></i>Alta</span>
                        <span class="legend-item"><i class="very-high"></i>Muito alta</span></div>''',
                        unsafe_allow_html=True,
                    )
                source_legend = []
                if show_soil_texture:
                    source_legend.extend([
                        '<span class="legend-item"><i class="rock"></i>Rocha/não solo</span>',
                        '<span class="legend-item"><i class="very-clay"></i>Muito argilosa</span>',
                        '<span class="legend-item"><i class="clay"></i>Argilosa</span>',
                        '<span class="legend-item"><i class="silt"></i>Siltosa</span>',
                        '<span class="legend-item"><i class="sand"></i>Arenosa</span>',
                        '<span class="legend-item"><i class="medium-texture"></i>Média</span>',
                    ])
                if show_soil_pedology:
                    source_legend.extend([
                        '<span class="legend-item"><i class="latossolo"></i>Latossolos</span>',
                        '<span class="legend-item"><i class="argissolo"></i>Argissolos</span>',
                        '<span class="legend-item"><i class="cambissolo"></i>Cambissolos</span>',
                        '<span class="legend-item"><i class="neossolo"></i>Neossolos</span>',
                        '<span class="legend-item"><i class="gleissolo"></i>Gleissolos</span>',
                        '<span class="legend-item"><i class="other-pedology"></i>Outras ordens</span>',
                    ])
                if source_legend:
                    st.markdown(f'<div id="source-legend"><strong>Referências ligadas</strong>{"".join(source_legend)}</div>', unsafe_allow_html=True)
                st.markdown(
                    '''<p class="soil-methodology"><b>Metodologia:</b> Nota base combina, em partes iguais, a textura superficial MapBiomas Solo (0–30 cm) e o contexto físico da Pedologia IBGE 1:250.000. Rocha, escarpa e hidromorfia reduzem somente a fração de área correspondente; a penalidade é limitada a 25 pontos e áreas críticas acima de 10% são sinalizadas para validação.<br/><b>Fórmula:</b> Nota = máx(20, [0,50 × S<sub>textura</sub> + 0,50 × S<sub>IBGE</sub>] − mín(25, %rocha + %escarpa + %hidromorfia)); S<sub>textura</sub> = Σ(%classe × peso textural) e S<sub>IBGE</sub> = 100 × multiplicador físico IBGE.<br/><b>Leitura das cores:</b> vermelho indica predomínio de limitação física, como rocha, escarpa, hidromorfia ou textura muito desfavorável; laranja indica limitações relevantes que exigem correção e validação; amarelo indica condição intermediária; verde-claro indica solo predominantemente compatível, com limitações manejáveis; verde-escuro indica contexto físico favorável, sem restrição crítica expressiva. A cor resume a nota final e não substitui sondagem de campo.</p>''',
                    unsafe_allow_html=True,
                )
                if show_soil_texture:
                    st.caption("Paleta oficial MapBiomas Solo C3: cinza = rocha/não solo; ferrugem = muito argilosa; rosado = argilosa; azul-petróleo = siltosa; amarelo = arenosa; bege = média. As cores são categóricas e não representam aptidão.")
                if show_soil_pedology:
                    st.caption("Convenção IBGE: Latossolos em amarelos, laranjas e vermelho-amarelos; Argissolos em tons claros rosados; Gleissolos em azul; Cambissolos e Neossolos em tons terrosos. Consulte o tooltip para a unidade, textura, pedregosidade, rochosidade e relevo.")
        elif map_title == "Declividade e Relevo":
            with st.expander("Declividade e Relevo", expanded=False):
                st.markdown('<p class="soil-description">Condição topográfica para implantação, manejo e conservação do solo em SAF Cacau.</p>', unsafe_allow_html=True)
                controls = st.columns(2)
                with controls[0]:
                    show_relief_score = st.toggle("Declividade e Relevo", value=True, key="relief_show_score")
                with controls[1]:
                    show_relief_source = st.toggle("Declividade COPDEM GLO-30", value=False, key="relief_ref_slope")
                st.caption("Referência opcional: declividade média derivada do Copernicus DEM GLO-30 (30 m), resumida para cada unidade de análise. Não altera a nota já calculada.")
                relief_layers = []
                if show_relief_score:
                    relief_layers.extend(
                        pdk.Layer(
                            "MVTLayer", f"app/static/hex_relief_tiles_v1/score/{category}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=4, max_zoom=7, filled=True, stroked=False,
                            get_fill_color=color, pickable=True,
                        )
                        for category, color in SOIL_SCORE_LAYERS
                    )
                if show_relief_source:
                    slope_colors = {
                        1: [52, 139, 75, 190], 2: [152, 191, 83, 190], 3: [246, 204, 63, 190],
                        4: [235, 122, 47, 190], 5: [197, 52, 47, 190],
                    }
                    for category, color in slope_colors.items():
                        relief_layers.append(pdk.Layer(
                            "MVTLayer", f"app/static/solo_relevo_referencias_tiles_v1/declividade_media/{category}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=4, max_zoom=6, filled=True, stroked=False, get_fill_color=color, pickable=True,
                        ))
                if bahia_mask:
                    relief_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    relief_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(pdk.Deck(
                    layers=relief_layers,
                    initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15),
                    tooltip={"html": "<b>Declividade e Relevo</b><br/>Nota: {nota_mapa}/100<br/>Classe: {classe_mapa}"},
                ), height=500)
                if show_relief_score:
                    st.markdown('''<div id="soil-legend"><strong>Condição topográfica — nota</strong>
                    <span class="legend-item"><i class="very-low"></i>Muito restrita</span>
                    <span class="legend-item"><i class="low"></i>Restrita</span>
                    <span class="legend-item"><i class="medium"></i>Condicionada</span>
                    <span class="legend-item"><i class="high"></i>Favorável</span>
                    <span class="legend-item"><i class="very-high"></i>Muito favorável</span></div>''', unsafe_allow_html=True)
                if show_relief_source:
                    st.markdown('''<div id="source-legend"><strong>Declividade média COPDEM</strong>
                    <span class="legend-item"><i class="slope-flat"></i>≤3%</span><span class="legend-item"><i class="slope-soft"></i>&gt;3–8%</span><span class="legend-item"><i class="slope-mid"></i>&gt;8–15%</span><span class="legend-item"><i class="slope-steep"></i>&gt;15–25%</span><span class="legend-item"><i class="slope-very-steep"></i>&gt;25%</span></div>''', unsafe_allow_html=True)
                st.markdown('''<p class="soil-methodology"><b>Metodologia:</b> média ponderada pela área das faixas de declividade de 30 m. Pesos por pixel combinam manejo (45%), exposição topográfica à erosão (40%) e mecanização (15%); frações íngremes geram alertas, sem zerar toda a unidade.<br/><b>Fórmula:</b> Nota = Σ(% da área em cada faixa × peso da faixa), em que os pesos para 0–3%, 3–8%, 8–15%, 15–25%, 25–45% e &gt;45% são, respectivamente, 0,8875; 1,0000; 0,8025; 0,5400; 0,2325; e 0,0225.<br/><b>Leitura das cores:</b> vermelho indica terreno dominado por encostas muito íngremes — especialmente forte presença de 25–45% e/ou &gt;45% — com alto risco de erosão e operação difícil; laranja indica restrições importantes de acesso, mecanização e conservação; amarelo indica relevo misto, que exige manejo conservacionista; verde-claro indica predomínio de rampas suaves a moderadas, geralmente manejáveis; verde-escuro indica terreno majoritariamente plano a suave, sobretudo nas faixas de 0–8%, mais favorável à implantação. A cor resume a composição de toda a área, não uma única declividade.</p>''', unsafe_allow_html=True)
                if show_relief_source:
                    st.caption("Declividade média COPDEM: verde-escuro ≤3%; verde-claro >3–8%; amarelo >8–15%; laranja >15–25%; vermelho >25%. O tooltip também mostra a declividade máxima e as frações da unidade entre 25–45% e acima de 45%.")
        elif map_title == "Clima e irrigação" and has_climate:
            with st.expander("Clima e irrigação", expanded=False):
                st.markdown('<p class="soil-description">Compatibilidade climática e pressão de irrigação para SAF Cacau irrigado.</p>', unsafe_allow_html=True)
                first_controls = st.columns(3)
                with first_controls[0]: show_climate_score = st.toggle("Clima e irrigação", value=True, key="climate_show_score")
                with first_controls[1]: show_climate_p = st.toggle("Precipitação anual — CHIRPS", value=False, key="climate_ref_p")
                with first_controls[2]: show_climate_pe = st.toggle("Precipitação efetiva — CHIRPS", value=False, key="climate_ref_pe")
                second_controls = st.columns(3)
                with second_controls[0]: show_climate_dry = st.toggle("Sequência seca — CHIRPS", value=False, key="climate_ref_dry")
                with second_controls[1]: show_climate_tmean = st.toggle("Temperatura média — ERA5-Land", value=False, key="climate_ref_tmean")
                with second_controls[2]: show_climate_tmax = st.toggle("Tmax P95 — ERA5-Land", value=False, key="climate_ref_tmax")
                with st.columns(3)[0]: show_climate_demand = st.toggle("Demanda hídrica P80", value=False, key="climate_ref_demand")
                st.caption("Referências opcionais: climatologia 1996–2025, resumida para cada hexágono de 5 km. São os componentes da nota e não alteram o cálculo já armazenado.")
                climate_layers = []
                if show_climate_score:
                    climate_layers.extend(
                        pdk.Layer(
                            "MVTLayer", f"app/static/hex_climate_tiles_v1/score/{category}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=4, max_zoom=7, filled=True, stroked=False,
                            get_fill_color=color, pickable=True,
                        )
                        for category, color in SOIL_SCORE_LAYERS
                    )
                climate_sources = [
                    (show_climate_p, "precipitacao_anual", [[247,251,255,210], [198,219,239,210], [107,174,214,210], [49,130,189,210], [8,81,156,210]]),
                    (show_climate_pe, "precipitacao_efetiva", [[247,251,255,210], [198,219,239,210], [107,174,214,210], [49,130,189,210], [8,81,156,210]]),
                    (show_climate_dry, "sequencia_seca", [[255,255,204,210], [254,217,118,210], [253,141,60,210], [227,26,28,210], [128,0,38,210]]),
                    (show_climate_tmean, "temperatura_media", [[255,255,204,210], [254,217,118,210], [253,141,60,210], [227,26,28,210], [128,0,38,210]]),
                    (show_climate_tmax, "tmax_p95", [[255,255,204,210], [254,217,118,210], [253,141,60,210], [227,26,28,210], [128,0,38,210]]),
                    (show_climate_demand, "demanda_p80", [[242,240,247,210], [203,201,226,210], [158,154,200,210], [117,107,177,210], [84,39,143,210]]),
                ]
                for enabled, source, colors in climate_sources:
                    if enabled:
                        for category, color in enumerate(colors, start=1):
                            climate_layers.append(pdk.Layer(
                                "MVTLayer", f"app/static/hex_climate_referencias_tiles_v1/{source}/{category}/{{z}}/{{x}}/{{y}}.pbf",
                                min_zoom=4, max_zoom=6, filled=True, stroked=False, get_fill_color=color, pickable=True,
                            ))
                if bahia_mask:
                    climate_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    climate_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                climate_reference_active = any((show_climate_p, show_climate_pe, show_climate_dry, show_climate_tmean, show_climate_tmax, show_climate_demand))
                climate_tooltip = (
                    {"html": "<b>Camada climática de referência</b><br/>Valor: {valor}"}
                    if climate_reference_active else
                    {"html": "<b>Clima e irrigação</b><br/>Nota: {nota_mapa}/100<br/>Temperatura média: {clima_tmean_c} °C<br/>Tmax P95: {clima_tmax_p95_c} °C<br/>Demanda P80: {clima_demanda_irrigacao_p80_mm} mm/ano<br/>Sequência seca: {clima_dryseq_months} meses<br/>Precipitação efetiva: {clima_pe_mm} mm/ano"}
                )
                st.pydeck_chart(pdk.Deck(
                    layers=climate_layers,
                    initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15),
                    tooltip=climate_tooltip,
                ), height=500)
                if show_climate_score:
                    st.markdown('''<div id="soil-legend"><strong>Clima e irrigação — nota</strong>
                    <span class="legend-item"><i class="very-low"></i>Muito baixa</span>
                    <span class="legend-item"><i class="low"></i>Baixa</span>
                    <span class="legend-item"><i class="medium"></i>Média</span>
                    <span class="legend-item"><i class="high"></i>Alta</span>
                    <span class="legend-item"><i class="very-high"></i>Muito alta</span></div>''', unsafe_allow_html=True)
                climate_legend = []
                def climate_items(title, tone, labels):
                    return f'<strong>{title}</strong>' + ''.join(f'<span class="legend-item"><i class="{tone}-{index}"></i>{label}</span>' for index, label in enumerate(labels, start=1))
                if show_climate_p: climate_legend.append(climate_items("Precipitação anual", "rain", ["≤600", "600–800", "800–1.000", "1.000–1.300", ">1.300 mm/ano"]))
                if show_climate_pe: climate_legend.append(climate_items("Precipitação efetiva", "rain", ["≤450", "450–600", "600–750", "750–900", ">900 mm/ano"]))
                if show_climate_dry: climate_legend.append(climate_items("Sequência seca", "heat", ["≤3", "3–6", "6–8", "8–10", ">10 meses"]))
                if show_climate_tmean: climate_legend.append(climate_items("Temperatura média", "heat", ["≤21", "21–23", "23–25", "25–27", ">27 °C"]))
                if show_climate_tmax: climate_legend.append(climate_items("Tmax P95", "heat", ["≤30", "30–32", "32–34", "34–36", ">36 °C"]))
                if show_climate_demand: climate_legend.append(climate_items("Demanda P80", "demand", ["≤700", "700–1.100", "1.100–1.450", "1.450–1.800", ">1.800 mm/ano"]))
                if climate_legend:
                    st.markdown('<div id="source-legend">' + '<br/>'.join(climate_legend) + '</div>', unsafe_allow_html=True)
                st.markdown('''<p class="soil-methodology"><b>Metodologia:</b> climatologia de 30 anos (1996–2025), usando precipitação CHIRPS e temperatura/evapotranspiração ERA5-Land. A nota avalia conforto térmico, calor extremo, demanda de irrigação P80, sequência seca e precipitação efetiva; não representa disponibilidade hídrica, outorga ou captação.<br/><b>Fórmula:</b> Nota = 0,45 × S<sub>Tmédia</sub> + 0,20 × S<sub>Tmax P95</sub> + 0,20 × S<sub>demanda P80</sub> + 0,10 × S<sub>seca</sub> + 0,05 × S<sub>PE</sub>. <span>(S<sub>Tmédia</sub>: temperatura média anual; S<sub>Tmax P95</sub>: percentil 95 das temperaturas máximas diárias; S<sub>demanda P80</sub>: déficit anual de irrigação com 80% de segurança; S<sub>seca</sub>: maior sequência anual de meses com chuva &lt;60 mm; S<sub>PE</sub>: precipitação efetiva anual.)</span><br/><b>Leitura das cores:</b> vermelho indica combinação de calor extremo recorrente, maior demanda de irrigação e/ou seca prolongada; laranja indica pressão climática alta; amarelo indica condição intermediária; verde-claro indica condição climática favorável com irrigação planejada; verde-escuro indica maior compatibilidade térmica e menor pressão climática relativa. A demanda P80 é a diferença anual entre ETo FAO-56 e precipitação efetiva, com 80% de segurança.</p>''', unsafe_allow_html=True)
        elif map_title == "Segurança hídrica" and has_water_security:
            with st.expander("Segurança hídrica", expanded=False):
                st.markdown('<p class="soil-description">Evidência regional de fonte superficial, rede hidrográfica e contexto de poços para triagem de captação.</p>', unsafe_allow_html=True)
                controls = st.columns(4)
                with controls[0]:
                    show_water_hexagons = st.toggle("Hexágonos de evidência", value=True, key="water_show_hexagons")
                with controls[1]:
                    show_surface_water = st.toggle("Água superficial", value=True, key="water_ref_surface")
                with controls[2]:
                    show_bho6 = st.toggle("Rede BHO6/ANA", value=True, key="water_ref_bho6")
                with controls[3]:
                    show_siagas = st.toggle("Poços SIAGAS/SGB", value=False, key="water_ref_siagas")
                st.caption("Camadas de referência vetoriais: MapBiomas Água (30 m, 2021–2025), BHO6/ANA e SIAGAS/SGB. Elas não alteram a nota já calculada.")
                water_layers = []
                if show_water_hexagons:
                    water_layers.extend(
                    pdk.Layer("MVTLayer", f"app/static/hex_water_security_tiles_v8/score/{category}/{{z}}/{{x}}/{{y}}.pbf",
                            min_zoom=4, max_zoom=7, filled=True, stroked=False, get_fill_color=color, pickable=True)
                        for category, color in SOIL_SCORE_LAYERS
                    )
                # Reference geometry is purposefully isolated in this individual
                # map.  The main aptitude map continues to render only its two
                # scored master layers (hexagons and certified properties).
                if show_surface_water:
                    water_layers.extend([
                        pdk.Layer("MVTLayer", "app/static/hidrica_referencias_tiles_v3/mapbiomas_sazonal/{z}/{x}/{y}.pbf",
                            min_zoom=5, max_zoom=9, filled=True, stroked=False, get_fill_color=[83, 168, 191, 90], pickable=False),
                        pdk.Layer("MVTLayer", "app/static/hidrica_referencias_tiles_v3/mapbiomas_persistente/{z}/{x}/{y}.pbf",
                            min_zoom=5, max_zoom=9, filled=True, stroked=False, get_fill_color=[21, 105, 157, 165], pickable=False),
                    ])
                if show_bho6:
                    water_layers.append(pdk.Layer("MVTLayer", "app/static/hidrica_referencias_tiles_v3/bho6/{z}/{x}/{y}.pbf",
                        min_zoom=5, max_zoom=9, filled=False, stroked=True, get_line_color=[32, 111, 158, 210],
                        get_line_width="properties.largura_px", line_width_units="pixels", line_width_min_pixels=0.6, pickable=False))
                if show_siagas:
                    water_layers.append(pdk.Layer("MVTLayer", "app/static/hidrica_referencias_tiles_v3/siagas/{z}/{x}/{y}.pbf",
                        min_zoom=7, max_zoom=9, point_type="circle", filled=True, stroked=True,
                        get_fill_color=[20, 86, 130, 205], get_line_color=[255, 255, 255, 220], line_width_min_pixels=0.7,
                        get_radius="properties.ponto_tamanho", radius_units="pixels", radius_min_pixels=2.5, radius_max_pixels=13, pickable=False))
                if bahia_mask:
                    water_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    water_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(pdk.Deck(layers=water_layers, initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15), tooltip={"html": "<b>Segurança hídrica — evidência preliminar</b><br/>Nota: {nota_mapa}/100<br/>Captação superficial outorgada ativa no imóvel: {outorga_ativa_no_imovel} ({outorga_ativa_qtd})<br/>Outorga subterrânea ativa no imóvel: {outorga_subterranea_ativa_qtd}<br/>Outorga subterrânea inativa/não confirmada no imóvel: {outorga_subterranea_inativa_qtd}<br/>Poços ativos com teste: no imóvel {pocos_ativos_testados_no_imovel}; até 2 km {pocos_ativos_testados_2km}<br/>Poços incertos/inativos/sem teste no imóvel: {pocos_incertos_no_imovel}<br/>Confiança: {confianca}"}), height=500)
                if show_water_hexagons:
                    st.markdown('''<div id="soil-legend"><strong>Evidência hídrica regional</strong>
                    <span class="legend-item"><i class="very-low"></i>Muito baixa</span><span class="legend-item"><i class="low"></i>Baixa</span><span class="legend-item"><i class="medium"></i>Média</span><span class="legend-item"><i class="high"></i>Alta</span><span class="legend-item"><i class="very-high"></i>Muito alta</span></div>''', unsafe_allow_html=True)
                water_reference_legend = []
                if show_surface_water:
                    water_reference_legend.extend(['<span class="legend-item"><i class="water-seasonal"></i>Água sazonal</span>', '<span class="legend-item"><i class="water-persistent"></i>Água persistente</span>'])
                if show_bho6:
                    water_reference_legend.append('<span class="legend-item"><i class="bho"></i>Rede BHO6/ANA</span>')
                if show_siagas:
                    water_reference_legend.append('<span class="legend-item"><i class="well"></i>Poço SIAGAS/SGB</span>')
                if water_reference_legend:
                    st.markdown(f'<div id="source-legend"><strong>Referências ligadas</strong>{"".join(water_reference_legend)}</div>', unsafe_allow_html=True)
                st.markdown('''<p class="soil-methodology"><b>Metodologia:</b> verde-escuro é reservado exclusivamente para captação superficial com outorga ativa dentro da unidade. Outorga subterrânea ativa dentro da unidade recebe verde-claro; outorga subterrânea inativa/não confirmada, ou poço SIAGAS Bombeando/Equipado com teste positivo, recebe amarelo. Registros secos, parados, abandonados, obstruídos, sem situação ou sem teste são contexto laranja. BHO6 e MapBiomas Água permanecem como referência visual e não elevam a nota.<br/><b>Fórmula:</b> Nota = 100 com outorga superficial ativa; caso contrário, 75 com outorga subterrânea ativa; 55 com outorga subterrânea inativa/não confirmada, poço ativo testado no imóvel ou ≥3 em 2 km; 40 somente com registro incerto/inativo/sem teste; 20 sem evidência. <br/><b>Leitura das cores:</b> verde-escuro = outorga superficial ativa; verde-claro = outorga subterrânea ativa; amarelo = evidência subterrânea parcial; laranja = registro sem confirmação operacional/produtiva; vermelho = sem evidência. A cor não equivale a vazão garantida, titularidade, nova outorga ou autorização de captação.</p>''', unsafe_allow_html=True)
        elif map_title == "Custo da terra" and has_land_cost:
            with st.expander("Custo da terra", expanded=False):
                st.markdown('<p class="soil-description">Referência regional de Valor da Terra Nua (VTN) agrícola em R$/ha. Não é preço de anúncio, laudo ou avaliação de fazenda.</p>', unsafe_allow_html=True)
                land_layers = [
                    pdk.Layer(
                        "MVTLayer", f"app/static/hex_custo_terra_tiles_v1/score/{category}/{{z}}/{{x}}/{{y}}.pbf",
                        min_zoom=4, max_zoom=7, filled=True, stroked=False,
                        get_fill_color=color, pickable=True,
                    )
                    for category, color in SOIL_SCORE_LAYERS
                ]
                if bahia_mask:
                    land_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    land_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(pdk.Deck(
                    layers=land_layers,
                    initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15),
                    tooltip={"html": "<b>Referência regional de custo da terra</b><br/>VTN agrícola: R$ {vtn_rs_ha}/ha<br/>Classe: {classe_mapa}<br/>Nota de custo: {nota_mapa}/100<br/>MRT: {mrt_id}<br/>Origem: {origem}<br/>Ano-base: {ano_base}<br/>Confiança: {confianca}<br/>Intervalo indicativo 80%: ±{intervalo_80_pct}%"},
                ), height=500)
                st.markdown('''<div id="soil-legend"><strong>Referência regional de custo da terra</strong>
                <span class="legend-item"><i class="very-low"></i>Muito alto</span>
                <span class="legend-item"><i class="low"></i>Alto</span>
                <span class="legend-item"><i class="medium"></i>Intermediário</span>
                <span class="legend-item"><i class="high"></i>Baixo</span>
                <span class="legend-item"><i class="very-high"></i>Muito baixo</span></div>''', unsafe_allow_html=True)
                st.markdown('''<p class="soil-methodology"><b>Fonte e escopo:</b> Atlas do Mercado de Terras 2025 — INCRA/SIMET, VTN agrícola por Mercado Regional de Terras (MRT), agregado por interseção de área entre município e unidade analisada. A estrutura mantém campo para VTN municipal oficial da Receita Federal, quando sua planilha for disponibilizada e validada.<br/><b>Normalização usada no índice geral:</b> Nota = 100 × [ln(Q90) − ln(VTN da unidade)] / [ln(Q90) − ln(Q10)], limitada entre 0 e 100. Q10 e Q90 são os percentis da distribuição estadual de referência; menor VTN recebe maior nota comercial. <b>Leitura das cores:</b> vermelho = maior referência regional de custo; laranja = custo alto; amarelo = intermediário; verde-claro = custo baixo; verde-escuro = menor custo relativo. O intervalo 80% representa incerteza da referência regional, não margem de negociação.</p>''', unsafe_allow_html=True)
        elif map_title == "Logística até Ilhéus" and has_logistics:
            with st.expander("Logística até Ilhéus", expanded=False):
                st.markdown('<p class="soil-description">Distância rodoviária real pela rede OpenStreetMap até o ponto operacional provisório da Cargill em Ilhéus.</p>', unsafe_allow_html=True)
                logistics_layers = [
                    pdk.Layer(
                        "MVTLayer", "app/static/hex_logistica_ilheus_tiles_v4/distance/0/{z}/{x}/{y}.pbf",
                        min_zoom=4, max_zoom=7, filled=True, stroked=False,
                        get_fill_color=[196, 202, 208, 175], pickable=True,
                    ),
                ] + [
                    pdk.Layer(
                        "MVTLayer", f"app/static/hex_logistica_ilheus_tiles_v4/distance/{category}/{{z}}/{{x}}/{{y}}.pbf",
                        min_zoom=4, max_zoom=7, filled=True, stroked=False,
                        get_fill_color=color, pickable=True,
                    )
                    for category, color in SOIL_SCORE_LAYERS
                ]
                if bahia_mask:
                    logistics_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    logistics_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(pdk.Deck(
                    layers=logistics_layers,
                    initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15),
                    tooltip={"html": "<b>Logística até Ilhéus</b><br/>Distância rodoviária: {distancia_rota_km} km<br/>Classe: {classe_mapa}<br/>Nota normalizada: {nota_mapa}/100<br/>Conexão à rede: {distancia_snap_km} km<br/>Confiança: {confianca}<br/>Destino: {destino}"},
                ), height=500)
                st.markdown(f'''<div id="soil-legend"><strong>Distância por rota até Ilhéus</strong>
                <span class="legend-item"><i class="very-low"></i>&gt;{_log_cut_35:.0f} km</span>
                <span class="legend-item"><i class="low"></i>{_log_cut_50:.0f}–{_log_cut_35:.0f} km</span>
                <span class="legend-item"><i class="medium"></i>{_log_cut_65:.0f}–{_log_cut_50:.0f} km</span>
                <span class="legend-item"><i class="high"></i>{_log_cut_80:.0f}–{_log_cut_65:.0f} km</span>
                <span class="legend-item"><i class="very-high"></i>≤{_log_cut_80:.0f} km</span></div>''', unsafe_allow_html=True)
                st.markdown(f'''<p class="soil-methodology"><b>Metodologia:</b> rota rodoviária pelo centroide de cada hexágono até o ponto operacional provisório da Cargill Cacau, Distrito de Iguape, Ilhéus/BA, calculada sobre a malha viária OpenStreetMap/Geofabrik Nordeste com Dijkstra reverso. A cor representa a distância real de rota, não frete, tempo, condição de pavimento, pedágio ou acesso interno da fazenda.<br/><b>Normalização usada no índice geral:</b> Nota = 100 × ({_log_max_km:.1f} − distância em km) / ({_log_max_km:.1f} − {_log_min_km:.1f}), limitada entre 0 e 100. Os limites são a menor e a maior rota válida da malha da Bahia; a cor apenas agrupa essa nota contínua nas faixas gerais 80, 65, 50 e 35. Sem rota OSM ou com conexão à rede superior a 10 km, a nota permanece sem dado e exige diligência.</p>''', unsafe_allow_html=True)
        elif map_title == "PRODES e embargos" and has_socioenvironmental:
            with st.expander("PRODES e embargos", expanded=False):
                st.markdown('<p class="soil-description">Triagem territorial de supressão PRODES e alertas oficiais de embargo, UC e Terra Indígena.</p>', unsafe_allow_html=True)
                controls = st.columns(4)
                with controls[0]:
                    show_socio_hex = st.toggle("Classificação territorial", value=True, key="socio_hex")
                with controls[1]:
                    show_prodes = st.toggle("PRODES por período", value=False, key="socio_prodes")
                with controls[2]:
                    show_embargos = st.toggle("Embargos Ibama vigentes", value=False, key="socio_embargos")
                with controls[3]:
                    show_territories = st.toggle("UC e Terras Indígenas", value=False, key="socio_territories")
                st.caption("A classificação é uma triagem de risco e não comprova conformidade legal, EUDR, titularidade ou autorização de uso.")
                socio_layers = []
                if show_socio_hex:
                    socio_layers.extend(
                        pdk.Layer("MVTLayer", f"app/static/hex_prodes_embargos_tiles_v1/score/{category}/{{z}}/{{x}}/{{y}}.pbf", min_zoom=4, max_zoom=7, filled=True, stroked=False, get_fill_color=color, pickable=True)
                        for category, color in SOIL_SCORE_LAYERS
                    )
                if show_prodes:
                    socio_layers.extend([
                        pdk.Layer("MVTLayer", "app/static/socioambiental_referencias_tiles_v1/prodes_ate_2008/{z}/{x}/{y}.pbf", min_zoom=7, max_zoom=7, filled=True, stroked=False, get_fill_color=[50, 139, 75, 50], pickable=False),
                        pdk.Layer("MVTLayer", "app/static/socioambiental_referencias_tiles_v1/prodes_2009_2020/{z}/{x}/{y}.pbf", min_zoom=7, max_zoom=7, filled=True, stroked=False, get_fill_color=[152, 191, 83, 65], pickable=False),
                        pdk.Layer("MVTLayer", "app/static/socioambiental_referencias_tiles_v1/prodes_pos_2020/{z}/{x}/{y}.pbf", min_zoom=7, max_zoom=7, filled=True, stroked=False, get_fill_color=[246, 204, 63, 85], pickable=False),
                    ])
                if show_embargos:
                    socio_layers.append(pdk.Layer("MVTLayer", "app/static/socioambiental_referencias_tiles_v1/embargos_ibama/{z}/{x}/{y}.pbf", min_zoom=7, max_zoom=7, filled=True, stroked=True, get_fill_color=[128, 32, 43, 75], get_line_color=[128, 32, 43, 220], line_width_min_pixels=1, pickable=False))
                if show_territories:
                    socio_layers.extend([
                        pdk.Layer("MVTLayer", "app/static/socioambiental_referencias_tiles_v1/unidades_conservacao/{z}/{x}/{y}.pbf", min_zoom=7, max_zoom=7, filled=False, stroked=True, get_line_color=[67, 92, 125, 220], line_width_min_pixels=1, pickable=False),
                        pdk.Layer("MVTLayer", "app/static/socioambiental_referencias_tiles_v1/terras_indigenas/{z}/{x}/{y}.pbf", min_zoom=7, max_zoom=7, filled=False, stroked=True, get_line_color=[99, 72, 139, 220], line_width_min_pixels=1, pickable=False),
                    ])
                if bahia_mask:
                    socio_layers.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238, 240, 242, 205], pickable=False))
                if bahia_boundary:
                    socio_layers.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13, 39, 67, 255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(pdk.Deck(layers=socio_layers, initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15), tooltip={"html": "<b>PRODES e embargos</b><br/>Classe: {classe_mapa}<br/>Até 2008: {prodes_ate_2008_ha} ha<br/>2009–2020: {prodes_2009_2020_ha} ha<br/>Após 2020: {prodes_pos_2020_ha} ha<br/>Restrição territorial unida: {restricao_unida_pct}%<br/>Alertas: {alertas}"}), height=500)
                if show_socio_hex:
                    st.markdown('''<div id="soil-legend"><strong>Triagem socioambiental</strong><span class="legend-item"><i class="very-low"></i>Restrição crítica</span><span class="legend-item"><i class="low"></i>Diligência</span><span class="legend-item"><i class="medium"></i>Após 2020 predominante</span><span class="legend-item"><i class="high"></i>2009–2020 predominante</span><span class="legend-item"><i class="very-high"></i>Até 2008 predominante</span></div>''', unsafe_allow_html=True)
                socio_reference_legend = []
                if show_prodes:
                    socio_reference_legend.extend(['<span class="legend-item"><i class="prodes-old"></i>PRODES até 2008</span>', '<span class="legend-item"><i class="prodes-mid"></i>PRODES 2009–2020</span>', '<span class="legend-item"><i class="prodes-new"></i>PRODES após 2020</span>'])
                if show_embargos:
                    socio_reference_legend.append('<span class="legend-item"><i class="embargo"></i>Embargo Ibama vigente</span>')
                if show_territories:
                    socio_reference_legend.extend(['<span class="legend-item"><i class="uc"></i>Unidade de Conservação</span>', '<span class="legend-item"><i class="ti"></i>Terra Indígena</span>'])
                if socio_reference_legend:
                    st.markdown(f'<div id="source-legend"><strong>Referências ligadas</strong>{"".join(socio_reference_legend)}</div>', unsafe_allow_html=True)
                st.markdown('''<p class="soil-methodology"><b>Metodologia:</b> A≤2008 é o Marco UE PRODES até 31/12/2020 menos os incrementos de 2009–2020; A2009–2020 e A≥2021 vêm de incrementos anuais PRODES, sem dupla contagem. Em hexágonos, embargo federal poligonal vigente, UC ou TI em união ≥50% gera vermelho; qualquer contato menor ou embargo pontual gera laranja. Nos imóveis certificados, embargo poligonal vigente em qualquer toque gera vermelho; embargo pontual interno gera laranja. Sem restrição prioritária, prevalece o período de maior área. <b>Fontes:</b> INPE/TerraBrasilis PRODES, Ibama/AIE, CNUC/MMA e Funai. Esta leitura exige validação documental e não substitui diligência legal ou ambiental.</p>''', unsafe_allow_html=True)
        elif map_title == "Mão de obra e infraestrutura" and not is_demo and data["mao_obra_infra"].notna().any():
            with st.expander("Mão de obra e infraestrutura", expanded=False):
                st.markdown('<p class="soil-description">Contexto municipal preliminar de mão de obra agropecuária. Não comprova trabalhador disponível para contratação.</p>', unsafe_allow_html=True)
                controls = st.columns(3)
                with controls[0]: show_labor_index = st.toggle("Índice composto", value=True, key="labor_index")
                with controls[1]: show_labor_agro = st.toggle("Pessoal ocupado rural — Censo Agro", value=False, key="labor_agro")
                with controls[2]: show_labor_pop = st.toggle("População municipal — Censo 2022", value=False, key="labor_pop")
                layers_labor = []
                for active_layer, theme in ((show_labor_index, "indice"), (show_labor_agro, "pessoal_rural"), (show_labor_pop, "populacao")):
                    if active_layer:
                        layers_labor.extend(pdk.Layer("MVTLayer", f"app/static/hex_mao_obra_tiles_v1/{theme}/{category}/{{z}}/{{x}}/{{y}}.pbf", min_zoom=4, max_zoom=7, filled=True, stroked=False, get_fill_color=color, pickable=True) for category, color in SOIL_SCORE_LAYERS)
                if bahia_mask: layers_labor.insert(0, pdk.Layer("GeoJsonLayer", bahia_mask, filled=True, stroked=False, get_fill_color=[238,240,242,205], pickable=False))
                if bahia_boundary: layers_labor.append(pdk.Layer("GeoJsonLayer", bahia_boundary, filled=False, stroked=True, get_line_color=[13,39,67,255], line_width_min_pixels=2.5, pickable=False))
                st.pydeck_chart(pdk.Deck(layers=layers_labor, initial_view_state=pdk.ViewState(latitude=-12.6, longitude=-41.7, zoom=5.15), tooltip={"html":"<b>Contexto municipal de mão de obra</b><br/>Nota preliminar: {nota_mapa}/100<br/>Pessoal ocupado rural (2017): {pessoal_rural_2017}<br/>População (2022): {populacao_2022}<br/>Município dominante: {municipio}<br/>Cobertura municipal: {cobertura_pct}%<br/>Confiança: {confianca}<br/>Alertas: {alertas}"}), height=500)
                if show_labor_index:
                    st.markdown('''<div id="soil-legend"><strong>Contexto municipal de mão de obra — índice</strong><span class="legend-item"><i class="very-low"></i>Muito baixo</span><span class="legend-item"><i class="low"></i>Baixo</span><span class="legend-item"><i class="medium"></i>Médio</span><span class="legend-item"><i class="high"></i>Alto</span><span class="legend-item"><i class="very-high"></i>Muito alto</span></div>''', unsafe_allow_html=True)
                if show_labor_agro or show_labor_pop:
                    labels = []
                    if show_labor_agro: labels.append("Pessoal ocupado rural")
                    if show_labor_pop: labels.append("População municipal")
                    st.markdown(f'''<div id="source-legend"><strong>{" + ".join(labels)} — referência relativa estadual</strong><span class="legend-item"><i class="very-low"></i>Menor</span><span class="legend-item"><i class="low"></i>Baixo</span><span class="legend-item"><i class="medium"></i>Intermediário</span><span class="legend-item"><i class="high"></i>Alto</span><span class="legend-item"><i class="very-high"></i>Maior</span></div>''', unsafe_allow_html=True)
                st.markdown('''<p class="soil-methodology"><b>Versão preliminar:</b> 0,45 × pessoal ocupado rural do Censo Agro 2017 + 0,20 × escala populacional do Censo 2022, renormalizada enquanto a RAIS agro 2024 não for integrada. Os valores são calculados por município e transferidos por interseção de área; não usam centróide. A RAIS será incorporada com peso de 0,35 após controle de qualidade. <b>Leitura:</b> as cores representam contexto relativo no estado, e não trabalhadores livres, qualificação para cacau, salário, sazonalidade ou capacidade de contratação.</p>''', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="individual-map-row"><span>{map_title}</span><small>Em preparação</small></div>', unsafe_allow_html=True)

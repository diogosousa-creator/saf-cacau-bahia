"""Build immutable thematic-score MVTs for the virtual suitability map."""
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
import mapbox_vector_tile, mercantile, pandas as pd
from shapely.geometry import box, mapping

SCORES=["tipo_solo","relevo_drenagem","clima_irrigacao","seguranca_hidrica","custo_terra","mao_obra_infra","risco_socioambiental","logistica_ilheus","uso_solo"]

def main():
 p=argparse.ArgumentParser(); p.add_argument('--units',type=Path,required=True);p.add_argument('--scores',type=Path,required=True);p.add_argument('--id-field',required=True);p.add_argument('--score-id',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--minzoom',type=int,required=True);p.add_argument('--maxzoom',type=int,required=True);p.add_argument('--tile-x-min',type=int);p.add_argument('--tile-x-max',type=int);p.add_argument('--max-tolerance',type=float,help='Limite de simplificacao em metros (use 150 para hexagonos).');args=p.parse_args()
 g=gpd.read_parquet(args.units) if args.units.suffix.endswith('parquet') else gpd.read_file(args.units)
 s=pd.read_parquet(args.scores)
 fields=[args.score_id,*[x for x in SCORES if x in s.columns]]
 # The property GeoParquet is both the geometry and the score source. Remove
 # duplicated score columns before merging so the canonical score names remain
 # available as ``s0…s8`` in the output tile.
 g=g.drop(columns=[field for field in SCORES if field in g.columns], errors='ignore')
 g=g.merge(s[fields],left_on=args.id_field,right_on=args.score_id,how='left').to_crs(4326); g['geometry']=g.geometry.make_valid()
 g['_row']=range(len(g)); ix=g.sindex; args.output.mkdir(parents=True,exist_ok=True)
 for z in range(args.minzoom,args.maxzoom+1):
  # Preserve the same progressive geometry fidelity already adopted for the
  # certified-property layer: light at regional scale and near-original when
  # an analyst zooms into a small group of farms.
  tol={4:1800,5:900,6:400,7:80,8:35,9:12}.get(z,8)
  if args.max_tolerance is not None:
   # 5 km hexagons lose vertices (and leave gaps) above ~1 km tolerance.
   tol=min(tol,args.max_tolerance)
  for t in mercantile.tiles(*g.total_bounds,zooms=[z]):
   if args.tile_x_min is not None and t.x < args.tile_x_min: continue
   if args.tile_x_max is not None and t.x > args.tile_x_max: continue
   b=mercantile.bounds(t); cand=list(ix.query(box(*b),predicate='intersects'))
   if not cand: continue
   x=g.iloc[cand].copy();x['feature_uid']=x['_row']+1;x['geometry']=x.geometry.intersection(box(*b));x=x[(~x.geometry.is_empty) & x.geometry.geom_type.isin(['Polygon','MultiPolygon'])].to_crs(3857);x['geometry']=x.geometry.simplify(tol,preserve_topology=True);x=x.to_crs(4326)
   fs=[]
   for r in x.itertuples(index=False):
    # ``id`` remains the canonical textual ID used by the app. ``farm_id`` is
    # an explicit stable identity for deck.gl across MVT fragments, while the
    # numeric feature ID is encoded in the MVT protobuf itself.  The latter
    # prevents clients from falling back to a tile-local feature position.
    canonical_id=str(getattr(r,args.id_field))
    props={'id':canonical_id}
    if args.id_field == 'id_fazenda':
     props['farm_id']=canonical_id
    if hasattr(r, 'referencia_exibicao'):
     props['referencia_exibicao']=str(getattr(r,'referencia_exibicao'))
    elif args.id_field == 'id_hex':
     props['referencia_exibicao']='Análise territorial'
    if hasattr(r, 'area_ha') and pd.notna(getattr(r,'area_ha')):
     props['area_ha']=round(float(getattr(r,'area_ha')),2)
    if hasattr(r, 'fonte_geometria'):
     props['fonte_geometria']=str(getattr(r,'fonte_geometria'))
    for i,k in enumerate(SCORES):
     # Keep a numeric sentinel for missing values.  The browser-side virtual
     # expression can then exclude unavailable criteria from its denominator
     # without receiving a null/undefined accessor.
     v=getattr(r,k,None)
     props[f's{i}']=round(float(v),2) if pd.notna(v) else -1
    native_id=int(getattr(r, 'feature_uid'))
    fs.append({'id':native_id,'geometry':mapping(r.geometry),'properties':props})
   data=mapbox_vector_tile.encode({'name':'base','features':fs},default_options={'quantize_bounds':(b.west,b.south,b.east,b.north)})
   d=args.output/str(z)/str(t.x);d.mkdir(parents=True,exist_ok=True);(d/f'{t.y}.pbf').write_bytes(data)
if __name__=='__main__': main()

// MapBiomas Brasil — Coleção 11, Cobertura e Uso da Terra, ano 2025 — Bahia.
//
// Como usar: cole este script no Google Earth Engine Code Editor
// (https://code.earthengine.google.com), clique em "Run" e depois, na aba
// "Tasks", em "RUN" na tarefa mapbiomas_c11_cobertura_2025_bahia.
// O arquivo vai para a pasta "saf_cacau" do seu Google Drive (pode vir
// dividido em partes). Baixe a pasta como .zip e avise: o projeto junta as
// partes sem reamostrar (scripts/build_landuse_raster_bahia.py).
//
// O raster é exportado na grade nativa do MapBiomas (30 m, EPSG:4326),
// com os códigos oficiais da legenda da Coleção 11. Fora da Bahia = 0.

var ano = 2025;
var asset = 'projects/mapbiomas-public/assets/brazil/lulc/collection11/mapbiomas_brazil_collection11_coverage_v3';

// Limite estadual (FAO GAUL) com folga de 2 km: o recorte fino pelo limite
// IBGE é feito depois, no projeto, igual às demais camadas.
var bahia = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filter(ee.Filter.eq('ADM0_NAME', 'Brazil'))
  .filter(ee.Filter.eq('ADM1_NAME', 'Bahia'))
  .geometry()
  .buffer(2000);

var cobertura = ee.Image(asset).select('classification_' + ano);
var projecao = cobertura.projection().getInfo();

var imagem = cobertura.clip(bahia).unmask(0).toUint8();

Map.centerObject(bahia, 6);
Map.addLayer(imagem.selfMask().randomVisualizer(), {}, 'MapBiomas ' + ano);

Export.image.toDrive({
  image: imagem,
  description: 'mapbiomas_c11_cobertura_' + ano + '_bahia',
  folder: 'saf_cacau',
  fileNamePrefix: 'mapbiomas_c11_cobertura_' + ano + '_bahia',
  region: bahia.bounds(),
  crs: projecao.crs,
  crsTransform: projecao.transform,
  maxPixels: 1e11,
  fileFormat: 'GeoTIFF',
  formatOptions: {cloudOptimized: true}
});

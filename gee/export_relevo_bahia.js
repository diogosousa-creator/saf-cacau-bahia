var bahia = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filter(ee.Filter.eq('ADM0_NAME', 'Brazil'))
  .filter(ee.Filter.eq('ADM1_NAME', 'Bahia'))
  .geometry();

// GLO-30 é composto por tiles nativos. A declividade deve ser derivada em
// cada tile ANTES do mosaico; calcular Terrain.slope após mosaic() faz o GEE
// herdar uma projeção-padrão grosseira e produz blocos artificiais.
var glo30 = ee.ImageCollection('COPERNICUS/DEM/GLO30').select('DEM');
var dem = glo30.mosaic().clip(bahia);
var slope = glo30.map(function (image) {
  return ee.Terrain.slope(image)
    .multiply(Math.PI / 180).tan().multiply(100)
    .rename('slope_pct')
    .copyProperties(image, image.propertyNames());
}).mosaic().clip(bahia);

// Armazena décimos de porcentagem em Int16: 82 equivale a 8,2%. Isso mantém
// precisão muito superior aos limiares agronômicos e reduz o volume pela metade
// em relação ao Float32 original.
var slopeExport = slope.multiply(10).round().toInt16().rename('slope_pct_x10');
Export.image.toDrive({image:dem.rename('elevation_m'),description:'copdem_glo30_bahia_elevation',folder:'Relatorio cacau',region:bahia,scale:30,crs:'EPSG:4326',maxPixels:2e11,fileFormat:'GeoTIFF'});
Export.image.toDrive({image:slopeExport,description:'copdem_glo30_bahia_slope_pct_v3_int16_x10',folder:'Relatorio cacau',region:bahia,scale:30,crs:'EPSG:4326',maxPixels:2e11,fileFormat:'GeoTIFF',formatOptions:{cloudOptimized:true}});

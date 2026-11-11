/*
 * SAF CACAU BAHIA — EVIDÊNCIA DE ÁGUA SUPERFICIAL
 * Fonte: MapBiomas Água, Coleção 5, 1985–2025, 30 m.
 *
 * Exporta UMA imagem multibanda já consolidada para a Bahia. A análise de
 * hexágonos e imóveis é feita localmente, preservando o pixel original de 30m.
 *
 * Bandas de saída:
 * - water_frequency_1985_2025_pct: frequência histórica de água (0–100%).
 * - water_persistent_2021_2025: água em todos os cinco anos recentes (0/1).
 * - water_seasonal_2021_2025: água em 1–4 dos cinco anos recentes (0/1).
 * - water_recent_frequency_pct: frequência recente 2021–2025 (0–100%).
 *
 * A camada é EVIDÊNCIA de superfície de água. Ela não mede vazão, volume
 * captável, direito de uso ou disponibilidade garantida para irrigação.
 */

var EXPORT_FOLDER = 'Relatorio cacau';
var DESCRIPTION = 'saf_cacau_bahia_mapbiomas_agua_c5_1985_2025';
var START_YEAR = 1985;
var END_YEAR = 2025;
var RECENT_START = 2021;
var RECENT_END = 2025;
var SCALE_M = 30;
var CRS = 'EPSG:4326';

var bahia = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filter(ee.Filter.eq('ADM0_NAME', 'Brazil'))
  .filter(ee.Filter.eq('ADM1_NAME', 'Bahia'))
  .geometry();

var waterBodies = ee.Image(
  'projects/mapbiomas-public/assets/brazil/water/collection5/' +
  'mapbiomas_brazil_collection5_water_bodies_v4'
);

function waterMask(year) {
  // Esta coleção é de corpos hídricos: valores positivos representam classes
  // de água; pixels sem corpo d'água são mascarados. A reclassificação binária
  // evita atribuir peso diferente a rio natural, reservatório ou aquicultura.
  return waterBodies.select(ee.String('classification_').cat(ee.Number(year).format('%.0f')))
    .unmask(0).gt(0)
    // A coleção precisa de uma banda com o mesmo nome em todos os anos para
    // que mean() e sum() sejam homogêneos no Earth Engine.
    .rename('water')
    .toUint8();
}

function masksForYears(start, end) {
  return ee.ImageCollection.fromImages(ee.List.sequence(start, end).map(function(year) {
    return waterMask(ee.Number(year));
  }));
}

var allYears = masksForYears(START_YEAR, END_YEAR);
var recentYears = masksForYears(RECENT_START, RECENT_END);
var historicalFrequency = allYears.mean().multiply(100)
  .round().toUint8().rename('water_frequency_1985_2025_pct');
var recentFrequency = recentYears.mean().multiply(100)
  .round().toUint8().rename('water_recent_frequency_pct');
var persistent = recentYears.sum().eq(RECENT_END - RECENT_START + 1)
  .rename('water_persistent_2021_2025').toUint8();
var seasonal = recentYears.sum().gt(0).and(recentYears.sum().lt(RECENT_END - RECENT_START + 1))
  .rename('water_seasonal_2021_2025').toUint8();

var output = ee.Image.cat([
  historicalFrequency,
  persistent,
  seasonal,
  recentFrequency
]).clip(bahia).set({
  source: 'MapBiomas Água Coleção 5',
  source_asset: 'projects/mapbiomas-public/assets/brazil/water/collection5/mapbiomas_brazil_collection5_water_bodies_v4',
  period_historical: START_YEAR + '-' + END_YEAR,
  period_recent: RECENT_START + '-' + RECENT_END,
  method_version: 'SAF_CACAU_AGUA_SUPERFICIAL_V1'
});

print('Bandas de origem', waterBodies.bandNames());
print('Bandas exportadas', output.bandNames());
print('Área Bahia', bahia);
Map.centerObject(bahia, 6);
Map.addLayer(recentFrequency, {min: 0, max: 100, palette: ['ffffff', '93c5fd', '1d4ed8']}, 'Frequência de água 2021–2025');
Map.addLayer(persistent.selfMask(), {palette: ['0b3d91']}, 'Água persistente 2021–2025');

Export.image.toDrive({
  image: output,
  description: DESCRIPTION,
  folder: EXPORT_FOLDER,
  fileNamePrefix: DESCRIPTION,
  region: bahia,
  scale: SCALE_M,
  crs: CRS,
  maxPixels: 3e11,
  fileFormat: 'GeoTIFF',
  formatOptions: {cloudOptimized: true}
});

// Builds src/assets/countries-110m.json, the listens map's country shapes, from Natural
// Earth's public-domain 1:110m countries: keeps only the ISO code, continent and name,
// then simplifies and quantises it as TopoJSON.
//
//   node scripts/world-map.mjs <ne_110m_admin_0_countries.geojson>
//
// The GeoJSON is at
// https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson
import { readFileSync, writeFileSync } from 'node:fs'
import { quantize } from 'topojson-client'
import { topology } from 'topojson-server'
import { presimplify, simplify } from 'topojson-simplify'

const CONTINENTS = {
  Africa: 'AF',
  Antarctica: 'AN',
  Asia: 'AS',
  Europe: 'EU',
  'North America': 'NA',
  Oceania: 'OC',
  'South America': 'SA',
  'Seven seas (open ocean)': 'AF', // the French Southern Lands
}
// Areas Natural Earth has no ISO code for: counted with their country.
const NO_CODE = { 'N. Cyprus': 'CY', Somaliland: 'SO' }

const [source] = process.argv.slice(2)
if (!source) throw new Error('usage: node scripts/world-map.mjs <countries.geojson>')
const input = JSON.parse(readFileSync(source, 'utf8'))
const features = input.features.map((f) => {
  const p = f.properties
  const code = /^[A-Z]{2}$/.test(p.ISO_A2_EH) ? p.ISO_A2_EH : NO_CODE[p.NAME]
  if (!code) throw new Error(`no ISO code for ${p.NAME}`)
  return { type: 'Feature', geometry: f.geometry, properties: { a2: code, c: CONTINENTS[p.CONTINENT], n: p.NAME } }
})

let topo = topology({ countries: { type: 'FeatureCollection', features } })
topo = simplify(presimplify(topo), 1e-7)
topo = quantize(topo, 1e5)
const out = JSON.stringify(topo)
writeFileSync(new URL('../src/assets/countries-110m.json', import.meta.url), out)
console.log(`countries-110m.json: ${features.length} countries, ${Math.round(out.length / 1024)} KB`)

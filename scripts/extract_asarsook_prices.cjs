// Pulls "unit item price شيكل" lines out of posts.jsonl (scraped from t.me/s/asarsook)
// and matches them to Masroufi slugs. Writes asarsook_prices.csv.
//
//   node extract.cjs <path to masroufi checkout>
//
// Every row keeps the original line and a link to its post, so any match can be
// checked by hand. Rows the parser is unsure about carry flags instead of being
// silently dropped or silently trusted.
const fs = require('fs')
const M = process.argv[2]

const norm = (s) => s.replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
  .replace(/[ً-ْـ]/g, '').replace(/\s+/g, ' ').trim()
const csv = (l) => { const o = []; let c = '', q = false
  for (const ch of l) { if (ch === '"') q = !q; else if (ch === ',' && !q) { o.push(c); c = '' } else c += ch }
  o.push(c); return o }

// --- Masroufi names and roles ---------------------------------------------
const names = [] // [normalised Arabic name, slug]
const ROLE = {}
const cat = fs.readFileSync(`${M}/app/kitchen/catalogue.py`, 'utf8')
for (const m of cat.matchAll(/\("([a-z_]+)",\s*"[^"]*",\s*"([^"]+)"[^\n]*ItemRole\.([A-Z_]+)/g)) {
  names.push([norm(m[2]), m[1]]); ROLE[m[1]] = m[3].toLowerCase()
}
for (const f of fs.readdirSync(`${M}/data/raw`).filter((f) => f.endsWith('_items.csv'))) {
  for (const l of fs.readFileSync(`${M}/data/raw/${f}`, 'utf8').split(/\r?\n/).slice(1)) {
    if (!l) continue
    const r = csv(l)
    for (const n of [r[2], ...(r[3] || '').split('|')]) if (n) names.push([norm(n), r[0]])
    ROLE[r[0]] ??= r[7]
  }
}
// Gaza colloquial names seen in the channel, mapped to existing slugs only.
for (const [n, s] of [['سيرج', 'veg_oil'], ['بتنجان', 'aubergine'], ['باذنجان', 'aubergine'],
  ['تومه', 'garlic'], ['ثومه', 'garlic'], ['فليفله', 'pepper'], ['لحمه عجل', 'beef'], ['لحمه', 'beef'],
  ['بندوره', 'tomato'], ['صلصه', 'tomato_paste'], ['جراده', 'carrot'], ['بقدونس', 'parsley'], ['رز مصري', 'rice']])
  names.push([norm(n), s])
names.sort((a, b) => b[0].length - a[0].length) // longest name wins

// Produce, meat and fish are quoted per kilo by convention when no unit is given.
const LOOSE = /^(veg|fruit|meat|fish|poultry|chicken|protein)/

const UNITS = { 'كيلو': ['kg', 1000], 'كيلوغرام': ['kg', 1000], 'كغم': ['kg', 1000], 'نص كيلو': ['g', 500],
  'نصف كيلو': ['g', 500], 'وقيه': ['g', 250], 'لتر': ['l', 1000], 'طبق': ['tray', 30], 'راس': ['head', 1],
  'حبه': ['piece', 1], 'علبه': ['tin', 1], 'كرتونه': ['carton', 1], 'ربطه': ['bunch', 1], 'باكو': ['pack', 1],
  'كيس': ['bag', 1], 'جالون': ['jerrycan', 1], 'شوال': ['sack', 1] }
const unitKeys = Object.keys(UNITS).sort((a, b) => b.length - a.length)

const region = (t) => /شمال/.test(t) ? 'north' : /(جنوب|خانيونس|خان يونس|رفح)/.test(t) ? 'south'
  : /(وسط|دير البلح|النصيرات|المغازي|البريج|الزوايده)/.test(t) ? 'middle' : /غزه|غزة/.test(t) ? 'gaza_city' : ''

// --- parse ----------------------------------------------------------------
const rows = [], miss = {}
for (const p of fs.readFileSync('posts.jsonl', 'utf8').trim().split('\n').map(JSON.parse)) {
  if (!/(شيكل|شيقل|₪)/.test(p.text)) continue
  const reg = region(p.text.split('\n').slice(0, 3).join(' '))
  for (const line of p.text.split('\n')) {
    const m = line.match(/^(.+?)[\s:=\-]*(\d+(?:[.,]\d+)?)\s*(شيكل|شيقل|₪)/)
    if (!m) continue
    let item = norm(m[1].replace(/[^؀-ۿ0-9a-zA-Z()\s]/g, ' '))
    let unit = '', grams = null, assumed = false
    const pu = item.match(/\((ال)?(كيلو|لتر|حبه|وقيه|ربطه)\)/)
    if (pu) { [unit, grams] = UNITS[pu[2]]; item = item.replace(pu[0], '').trim() }
    for (const u of unitKeys) if (!unit && item.startsWith(u + ' ')) { [unit, grams] = UNITS[u]; item = item.slice(u.length).trim() }
    item = item.replace(/\d+\s*(غ|جرام|غرام|مل|كغ)(?=\s|$)/g, '').trim()
    item = item.split(' ').map((w) => w.replace(/^ال(?=..)/, '')).join(' ')
    const hit = names.find(([n]) => n.length > 1 && (item === n || item.startsWith(n + ' ') || (' ' + item + ' ').includes(' ' + n + ' ')))
    if (!hit) { miss[item] = (miss[item] || 0) + 1; continue }
    const tail = line.slice(line.indexOf(m[3]))
    if (!unit && /كيلو/.test(tail)) { unit = 'kg'; grams = 1000 }
    if (!unit) { if (LOOSE.test(ROLE[hit[1]] || '')) { unit = 'kg'; grams = 1000; assumed = true } else unit = 'unknown' }
    const flags = [unit === 'unknown' ? 'unit_unknown' : '', assumed ? 'unit_assumed_kg' : '',
      /[؟?]/.test(line) ? 'question_not_quote' : '', /(متوفر ب|بدل|كان)/.test(line) ? 'two_prices_in_line' : '']
      .filter(Boolean).join('|')
    rows.push({ observed_on: p.date.slice(0, 10), slug: hit[1], price_ils: +m[2].replace(',', '.'), unit,
      unit_grams: grams ?? '', region: reg, flags, post: `https://t.me/asarsook/${p.id}`, line: line.trim() })
  }
}

const q = (s) => /[",\n]/.test(String(s)) ? '"' + String(s).replace(/"/g, '""') + '"' : s
const cols = Object.keys(rows[0])
rows.sort((a, b) => (a.observed_on < b.observed_on ? -1 : 1))
fs.writeFileSync('asarsook_prices.csv', [cols.join(','), ...rows.map((r) => cols.map((c) => q(r[c])).join(','))].join('\n') + '\n')

const flags = {}
for (const r of rows) for (const x of r.flags.split('|').filter(Boolean)) flags[x] = (flags[x] || 0) + 1
console.log('matched', rows.length, 'slugs', new Set(rows.map((r) => r.slug)).size,
  'unmatched', Object.values(miss).reduce((a, b) => a + b, 0), 'clean', rows.filter((r) => !r.flags).length, JSON.stringify(flags))

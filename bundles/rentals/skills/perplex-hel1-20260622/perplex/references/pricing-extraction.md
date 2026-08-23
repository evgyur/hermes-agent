# Exact pricing extraction pattern

Use this when the user asks for concrete current prices, tariffs, cloud/VPS costs, or corrects “estimates” into “exact prices”.

## Rule

Do not rely on Perplexity summaries, SEO comparison posts, or mental estimates for prices. Use them only to discover candidate official URLs. Final numbers must come from official vendor pages, calculators, or public pricing APIs.

## Workflow

1. Discover official pages with Perplexity/search.
2. Fetch official pages directly with `requests`/`curl` and a browser-like User-Agent.
3. Extract visible tariff cards by searching for `₽`, `руб`, `/мес`, resource names (`vCPU`, `RAM`, `NVMe`, `ГБ`).
4. If the page is JS-rendered, inspect:
   - embedded JSON/data attributes (`data-tariffs`, JSON-LD, `__NEXT_DATA__`, `window.__NUXT__`)
   - linked state bundles (`/static/.../state.js`)
   - public pricing endpoints (`https://api.vendor/.../prices?...`)
5. Compute totals only from official unit prices, and show the formula/source.
6. Include timestamp and source URLs in the final answer.
7. Label ranges/estimates explicitly; do not mix them with exact official prices.

## Useful probes

```bash
python3 - <<'PY'
import requests, re
url='https://example.com/pricing'
html=requests.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'}).text
for pat in [r'.{0,120}(?:₽|руб|/мес|месяц).{0,120}', r'.{0,120}(?:vCPU|CPU|RAM|NVMe|GB|ГБ|ядр).{0,120}']:
    for m in re.findall(pat, html, flags=re.I|re.S)[:50]:
        print(re.sub(r'\s+',' ',re.sub(r'<[^>]*>',' ',m)).strip())
PY
```

Nuxt state extraction pattern:

```bash
node - <<'NODE'
const https=require('https'), vm=require('vm');
const url='https://cdn.vendor/static/.../state.js';
https.get(url,{headers:{'User-Agent':'Mozilla/5.0'}},res=>{let data='';res.on('data',c=>data+=c);res.on('end',()=>{
  const sandbox={window:{}};
  vm.runInNewContext(data,sandbox,{timeout:5000});
  console.log(Object.keys(sandbox.window.__NUXT__ || {}));
});});
NODE
```

## Session examples captured

- Timeweb Cloud server tariffs were extractable from the official HTML by searching `Cloud MSK` + `₽/мес`.
- Timeweb S3 tariffs were extractable from the official S3 page (`1GB/10GB/100GB/250GB` preset cards).
- Selectel cloud server prices were available through `https://api.selectel.ru/prices/vpc?currency=rub`; RAM was returned per MB, so convert to GB with `*1024` before calculating monthly totals.
- Selectel page state was also available through a Nuxt `state.js` bundle containing `window.__NUXT__.ssrRefs['/prices/vpc?currency=rub']`.
- FirstVDS embedded tariff JSON in `data-tariffs` attributes on the homepage; decode HTML entities and parse JSON.
- HOSTKEY VPS cards were extractable from official HTML by scanning names like `vm.v2-medium`, `vds.ryzen-16`, then reading nearby resources and price.
- RUVDS promo pages expose both visible tariff text and order links with query params (`cpu`, `ram`, `systemHardDriveCapacity`, `paymentPeriod`).

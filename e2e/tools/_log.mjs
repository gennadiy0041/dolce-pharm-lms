const targets = await (await fetch('http://localhost:9222/json')).json()
const t = targets.find((x) => x.type === 'page' && x.url.startsWith('https://192.168.137.1:8443'))
const ws = new WebSocket(t.webSocketDebuggerUrl)
await new Promise((r) => ws.addEventListener('open', r, { once: true }))
const expr = `(async () => { const names = (await indexedDB.databases()).map((d) => d.name).filter((n) => n && n.startsWith('dolce-lms-u-')); const out = []; for (const name of names) { const db = await new Promise((res, rej) => { const r = indexedDB.open(name); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error) }); const rows = await new Promise((res) => { const r = db.transaction('log').objectStore('log').getAll(); r.onsuccess = () => res(r.result); r.onerror = () => res([]) }); out.push(...rows.slice(-12).map((x) => x.at + ' ' + x.kind + ' ' + x.text)); db.close() } return JSON.stringify(out, null, 1) })()`
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id === 1) { console.log(m.result?.result?.value ?? JSON.stringify(m)); ws.close() } })
ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, returnByValue: true, awaitPromise: true } }))

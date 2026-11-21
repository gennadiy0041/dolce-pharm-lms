const targets = await (await fetch('http://localhost:9222/json')).json()
for (const t of targets) {
  if (!t.url.startsWith('https://192.168.137.1:8443')) continue
  if (t.type !== 'page') { console.log('==', t.type, t.url); continue }
  const ws = new WebSocket(t.webSocketDebuggerUrl)
  await new Promise((r, j) => { ws.addEventListener('open', r, { once: true }); ws.addEventListener('error', j, { once: true }) })
  const expr = `JSON.stringify({ vis: document.visibilityState, standalone: matchMedia('(display-mode: standalone)').matches, text: document.body.innerText.split(String.fromCharCode(10)).filter(Boolean).join(" | ").slice(0, 500) })`
  const res = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve('нет ответа за 10 с'), 10000)
    ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id === 1) { clearTimeout(timer); resolve(m.result?.result?.value ?? JSON.stringify(m)) } })
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, returnByValue: true } }))
  })
  console.log('== page', t.id, res)
  ws.close()
}

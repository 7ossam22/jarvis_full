// ── AI Provider Abstraction ──────────────────────────────────────────────────
// Claude  → direct fetch to api.anthropic.com
// Local   → routed through Electron IPC (Node.js http, no CORS) when in Electron
//           falls back to direct fetch in web/browser mode

export const PROVIDERS = {
  CLAUDE: 'claude',
  LOCAL:  'local',
}

export const PROVIDER_LABELS = {
  [PROVIDERS.CLAUDE]: 'Claude AI',
  [PROVIDERS.LOCAL]:  'Local AI',
}

const CLAUDE_API_URL = 'https://api.anthropic.com/v1/messages'

const SYSTEM_PROMPT = `You are J.A.R.V.I.S (Just A Rather Very Intelligent System), Tony Stark's highly sophisticated AI assistant. You are:
- Sophisticated, eloquent, and witty with a British-influenced manner of speech
- Always address the user as "Sir" or occasionally "Boss"
- Concise but thorough — never ramble, always deliver value
- Capable of light humor but always professional
- Knowledgeable across all domains

Keep responses under 3 sentences unless complexity demands more. Begin responses naturally without filler phrases.`

const isElectron = () => typeof window !== 'undefined' && !!window.electronAPI?.localAiChat

// ── Claude ────────────────────────────────────────────────────────────────────
async function callClaude(history, apiKey) {
  if (!apiKey) throw new Error('No Anthropic API key configured. Add VITE_ANTHROPIC_API_KEY to your .env file.')

  const res = await fetch(CLAUDE_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-calls': 'true',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 512,
      system: SYSTEM_PROMPT,
      messages: history.slice(-20),
    }),
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error?.message || `Claude API error — HTTP ${res.status}`)
  }
  const data = await res.json()
  return data.content?.[0]?.text ?? 'No response from Claude, Sir.'
}

// ── Local AI (LM Studio) ──────────────────────────────────────────────────────
async function callLocalAI(history, { localAiUrl, localAiModel }) {
  const baseUrl = (localAiUrl || 'http://192.168.1.108:1234').replace(/\/$/, '')
  const model   = localAiModel || 'local-model'

  const messages = [
    { role: 'system', content: SYSTEM_PROMPT },
    ...history.slice(-20),
  ]
  const body = { model, messages, max_tokens: 512, temperature: 0.75, stream: false }

  // ── Electron: go through main process (no CORS) ───────────────────────────
  if (isElectron()) {
    const result = await window.electronAPI.localAiChat(baseUrl, body)
    if (!result.ok) throw new Error(result.error || 'Local AI returned an error')
    if (!result.text) throw new Error('Empty response from local model')
    return result.text
  }

  // ── Browser / web mode: direct fetch ─────────────────────────────────────
  const res = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).error?.message || detail } catch {}
    throw new Error(`Local AI error — ${detail}`)
  }
  const data = await res.json()
  const text = data.choices?.[0]?.message?.content
  if (!text) throw new Error('Empty response from local model')
  return text
}

// ── Public API ────────────────────────────────────────────────────────────────
export async function callAI(history, provider, settings = {}) {
  const apiKey = import.meta.env.VITE_ANTHROPIC_API_KEY
  switch (provider) {
    case PROVIDERS.CLAUDE: return callClaude(history, apiKey)
    case PROVIDERS.LOCAL:  return callLocalAI(history, settings)
    default: throw new Error(`Unknown AI provider: "${provider}"`)
  }
}

export async function pingLocalAI(url) {
  const baseUrl = (url || 'http://192.168.1.108:1234').replace(/\/$/, '')

  // ── Electron: IPC proxy (no CORS) ────────────────────────────────────────
  if (isElectron()) {
    const result = await window.electronAPI.localAiPing(baseUrl)
    return { online: result.online, models: result.models || [], error: result.error }
  }

  // ── Browser: direct fetch ─────────────────────────────────────────────────
  try {
    const res = await fetch(`${baseUrl}/v1/models`, { signal: AbortSignal.timeout(5000) })
    if (!res.ok) return { online: false, models: [] }
    const data = await res.json()
    return { online: true, models: (data.data || []).map(m => m.id) }
  } catch {
    return { online: false, models: [] }
  }
}

// ── AI Provider Abstraction ──────────────────────────────────────────────────
// Supports: Claude (Anthropic API) and Local AI (LM Studio / OpenAI-compatible)

export const PROVIDERS = {
  CLAUDE: 'claude',
  LOCAL: 'local',
}

export const PROVIDER_LABELS = {
  [PROVIDERS.CLAUDE]: 'Claude AI',
  [PROVIDERS.LOCAL]: 'Local AI',
}

const CLAUDE_API_URL = 'https://api.anthropic.com/v1/messages'
const LOCAL_API_URL = import.meta.env.VITE_LOCAL_AI_URL || 'http://192.168.1.108:1234'
const LOCAL_MODEL = import.meta.env.VITE_LOCAL_AI_MODEL || 'local-model'

const SYSTEM_PROMPT = `You are J.A.R.V.I.S (Just A Rather Very Intelligent System), Tony Stark's highly sophisticated AI assistant. You are:
- Sophisticated, eloquent, and witty with a British-influenced manner of speech
- Always address the user as "Sir" or occasionally "Boss"
- Concise but thorough — never ramble, always deliver value
- Capable of light humor but always professional
- Knowledgeable across all domains

Keep responses under 3 sentences unless complexity demands more. Begin responses naturally without filler phrases.`

// ── Claude (Anthropic) ────────────────────────────────────────────────────────
async function callClaude(history, apiKey) {
  if (!apiKey) {
    throw new Error('No Anthropic API key configured. Add VITE_ANTHROPIC_API_KEY to your .env file.')
  }

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
  return data.content?.[0]?.text ?? 'No response received from Claude, Sir.'
}

// ── Local AI (LM Studio / OpenAI-compatible) ──────────────────────────────────
async function callLocalAI(history) {
  const messages = [
    { role: 'system', content: SYSTEM_PROMPT },
    ...history.slice(-20),
  ]

  const res = await fetch(`${LOCAL_API_URL}/v1/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: LOCAL_MODEL,
      messages,
      max_tokens: 512,
      temperature: 0.75,
      stream: false,
    }),
  })

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).error?.message || detail } catch {}
    throw new Error(`Local AI error — ${detail}`)
  }

  const data = await res.json()
  const text = data.choices?.[0]?.message?.content
  if (!text) throw new Error('Empty response from local model, Sir.')
  return text
}

// ── Public interface ──────────────────────────────────────────────────────────
export async function callAI(history, provider) {
  const apiKey = import.meta.env.VITE_ANTHROPIC_API_KEY

  switch (provider) {
    case PROVIDERS.CLAUDE:
      return callClaude(history, apiKey)
    case PROVIDERS.LOCAL:
      return callLocalAI(history)
    default:
      throw new Error(`Unknown AI provider: "${provider}"`)
  }
}

export async function pingLocalAI() {
  try {
    const res = await fetch(`${LOCAL_API_URL}/v1/models`, {
      signal: AbortSignal.timeout(3000),
    })
    if (!res.ok) return { online: false, models: [] }
    const data = await res.json()
    const models = (data.data || []).map(m => m.id)
    return { online: true, models }
  } catch {
    return { online: false, models: [] }
  }
}

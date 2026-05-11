const MAX_ENTRIES = 200

const LOG_LEVELS = { DEBUG: 'DEBUG', INFO: 'INFO', WARN: 'WARN', ERROR: 'ERROR' }

const entries = []
const subscribers = []

function addEntry(level, category, message, data) {
  const entry = {
    id: Date.now() + Math.random(),
    timestamp: new Date().toISOString(),
    level,
    category,
    message,
    data: data ? JSON.stringify(data, null, 2) : null,
  }
  entries.push(entry)
  if (entries.length > MAX_ENTRIES) entries.shift()
  subscribers.forEach(fn => fn([...entries]))
}

export const logger = {
  debug: (category, message, data) => addEntry(LOG_LEVELS.DEBUG, category, message, data),
  info:  (category, message, data) => addEntry(LOG_LEVELS.INFO,  category, message, data),
  warn:  (category, message, data) => addEntry(LOG_LEVELS.WARN,  category, message, data),
  error: (category, message, data) => addEntry(LOG_LEVELS.ERROR, category, message, data),
  subscribe: (fn) => { subscribers.push(fn); return () => { const i = subscribers.indexOf(fn); if (i >= 0) subscribers.splice(i, 1) } },
  getEntries: () => [...entries],
  clear: () => { entries.length = 0; subscribers.forEach(fn => fn([])) },
}

export { LOG_LEVELS }

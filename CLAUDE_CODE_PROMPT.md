# JARVIS Desktop Application - Complete Build Instructions for Claude Code

## Project Overview
Build a production-ready JARVIS desktop application using Electron + React that looks exactly like the provided reference image. This is an advanced AI assistant with voice control, system integrations, and a stunning cyberpunk UI.

## Reference Design
The UI should match this exact layout:
- Left sidebar: System stats, weather, camera feed, uptime
- Center: Large animated orb with voice input bar below
- Right sidebar: Conversation history with messages
- Top: Custom title bar with window controls
- Color scheme: Dark blue/black background with cyan (#00d4ff) accents and glows
- Fonts: Orbitron for headings, Inter for body text
- Heavy use of glass morphism, glows, and animations

## Tech Stack
- **Electron**: Desktop app framework
- **React**: UI framework  
- **Vite**: Build tool
- **Framer Motion**: Animations
- **Node.js APIs**: System integration

## Core Features to Implement

### 1. Voice Control (CRITICAL)
- Continuous speech recognition always listening for wake word "Jarvis"
- Wake phrases: "Jarvis", "Hey Jarvis", "Okay Jarvis", "Hello Jarvis"
- After wake word detected, listen for command
- Text-to-speech for all JARVIS responses
- Visual feedback when listening (pulsing orb)

### 2. System Integrations
- **Camera Access**: Display live camera feed, take photos
- **Microphone**: Real-time voice input
- **Spotify Control**: Open Spotify app, play/pause, skip
- **Google Search**: Open searches in default browser
- **Weather**: Display current weather and forecast
- **System Info**: CPU, RAM, uptime, network stats

### 3. Claude API Integration
- Connect to Claude API for AI responses
- System prompt: "You are JARVIS, Tony Stark's AI assistant. Sophisticated, witty, British-influenced. Keep responses concise. Address user as 'Sir'."
- Handle special commands before sending to API:
  - "open spotify" → launch Spotify
  - "search for X" → Google search
  - "turn on/off camera" → toggle camera
  - "what's the weather" → show weather

### 4. UI Components to Build

#### TitleBar.jsx
- Custom frameless window bar
- App title "J.A.R.V.I.S" in Orbitron font with cyan glow
- Minimize, maximize, close buttons
- Draggable region

#### CentralOrb.jsx  
- Large animated circular orb in center (300px diameter)
- Pulsing glow effect with cyan rings
- Animation intensifies when listening
- Particle effects around edges
- Use Framer Motion for smooth animations

#### InputBar.jsx
- Text input field at bottom center
- Microphone button (always-on indicator when listening)
- Send button
- Glass morphism background
- Cyan border glow on focus

#### Conversation.jsx
- Right sidebar message list
- User messages: right-aligned, cyan background
- Assistant messages: left-aligned, dark background
- Timestamps
- Auto-scroll to bottom
- Smooth message entrance animations

#### SystemStats.jsx
- CPU usage (fake random 20-60% if real API unavailable)
- RAM usage
- Network status
- Display as circular progress bars with cyan fills

#### Weather.jsx
- Current temperature and conditions
- Location name
- Weather icon
- 3-day forecast
- Use wttr.in API or OpenWeather API

#### Camera.jsx
- Video feed from webcam
- Toggle on/off button
- "Camera Active" indicator
- 16:9 aspect ratio container

#### SystemUptime.jsx
- Display system uptime in HH:MM:SS
- Small panel at bottom of left sidebar

### 5. Styling (App.css)

```css
/* Dark theme with cyan accents */
:root {
  --bg-primary: #0a0e1a;
  --bg-secondary: #0f1419;
  --cyan-primary: #00d4ff;
  --cyan-glow: rgba(0, 212, 255, 0.3);
}

/* Glass morphism panels */
.panel {
  background: rgba(20, 25, 34, 0.6);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(0, 212, 255, 0.2);
  border-radius: 12px;
}

/* Cyan glow effect */
.glow {
  box-shadow: 0 0 20px var(--cyan-glow);
}

/* Grid background animation */
.background-grid {
  background-image: 
    linear-gradient(rgba(0, 212, 255, 0.1) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 212, 255, 0.1) 1px, transparent 1px);
  background-size: 50px 50px;
  animation: grid-move 20s linear infinite;
}
```

### 6. File Structure
```
jarvis-desktop/
├── package.json
├── vite.config.js
├── index.html
├── electron/
│   ├── main.js         (Electron main process)
│   └── preload.js      (IPC bridge)
├── src/
│   ├── main.jsx        (React entry)
│   ├── App.jsx         (Main component)
│   ├── App.css         (Main styles)
│   ├── index.css       (Global styles)
│   └── components/
│       ├── TitleBar.jsx
│       ├── CentralOrb.jsx
│       ├── InputBar.jsx
│       ├── Conversation.jsx
│       ├── SystemStats.jsx
│       ├── Weather.jsx
│       ├── Camera.jsx
│       └── SystemUptime.jsx
└── assets/
    └── icon.png
```

## Implementation Steps

### Step 1: Project Setup
```bash
npm init -y
npm install electron react react-dom framer-motion axios
npm install -D vite @vitejs/plugin-react electron-builder concurrently wait-on
```

### Step 2: Electron Configuration
- Set up main.js with IPC handlers for all system integrations
- Create preload.js to expose APIs to renderer
- Configure frameless window with transparency

### Step 3: React Components
Build components in this order:
1. TitleBar (basic window controls)
2. CentralOrb (animated orb)
3. InputBar (text + voice input)
4. Conversation (message display)
5. SystemStats, Weather, Camera, SystemUptime

### Step 4: Voice Integration
- Initialize Web Speech API in App.jsx
- Set up continuous recognition with auto-restart
- Implement wake word detection algorithm
- Add text-to-speech for responses

### Step 5: Claude API Integration
- Create API call function with proper error handling
- Parse commands before sending to API
- Stream responses if possible
- Display loading state during API calls

### Step 6: Styling & Animations
- Apply glass morphism to all panels
- Add cyan glows to interactive elements
- Animate orb with Framer Motion
- Add entrance animations to messages
- Create pulsing effects for listening state

### Step 7: System Integration Testing
- Test camera access permissions
- Test microphone permissions
- Verify Spotify launching works
- Test Google search opens correctly
- Validate weather API calls

## Key Code Snippets

### Wake Word Detection (in App.jsx)
```javascript
recognition.onresult = (event) => {
  const transcript = event.results[event.results.length - 1][0].transcript.toLowerCase();
  
  if (transcript.includes('jarvis')) {
    playWakeSound();
    // Extract command after wake word
    const command = transcript.split('jarvis')[1].trim();
    if (command) {
      handleVoiceCommand(command);
    }
  }
};
```

### Claude API Call
```javascript
const response = await fetch('https://api.anthropic.com/v1/messages', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 1024,
    messages: [{ role: 'user', content: message }],
    system: 'You are JARVIS...'
  })
});
```

### Animated Orb (CentralOrb.jsx)
```javascript
<motion.div
  className="orb"
  animate={{
    scale: isListening ? [1, 1.1, 1] : 1,
    boxShadow: isListening 
      ? ['0 0 40px rgba(0,212,255,0.4)', '0 0 80px rgba(0,212,255,0.8)', '0 0 40px rgba(0,212,255,0.4)']
      : '0 0 40px rgba(0,212,255,0.3)'
  }}
  transition={{ duration: 2, repeat: Infinity }}
/>
```

## Testing Checklist
- [ ] Window controls (minimize, maximize, close) work
- [ ] Voice wake word detection triggers correctly
- [ ] Text-to-speech responds for all messages
- [ ] Camera feed displays when activated
- [ ] Spotify opens on command
- [ ] Google search works
- [ ] Weather displays correctly
- [ ] System stats update in real-time
- [ ] Messages scroll properly
- [ ] All animations are smooth
- [ ] UI matches reference design

## Performance Requirements
- App startup < 3 seconds
- Voice response latency < 500ms
- Smooth 60fps animations
- Memory usage < 500MB

## Notes
- Use environment variables for API keys
- Add error handling for all API calls
- Implement graceful fallbacks when features unavailable
- Make sure speech recognition auto-restarts if it stops
- Camera should request permissions on first use

## Final Result
A fully functional JARVIS desktop app that:
- Looks exactly like the reference image
- Responds to voice commands with "Jarvis" wake word
- Speaks all responses aloud
- Controls Spotify, camera, Google search
- Displays real-time system info and weather
- Has smooth, polished animations
- Works as a standalone desktop application

Build this step-by-step, testing each component as you go. The UI design and voice control are the most critical parts - make them perfect!

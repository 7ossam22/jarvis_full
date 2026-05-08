import React, { useState, useEffect } from 'react'

const WEATHER_ICONS = {
  Clear: '☀️', Sunny: '☀️',
  'Partly cloudy': '⛅', Cloudy: '☁️', Overcast: '☁️',
  Rain: '🌧️', 'Light rain': '🌦️', Drizzle: '🌦️',
  Snow: '❄️', 'Light snow': '🌨️',
  Thunder: '⛈️', Thunderstorm: '⛈️',
  Fog: '🌫️', Mist: '🌫️', Haze: '🌫️',
  Windy: '💨', Blizzard: '🌨️',
}

function getWeatherIcon(condition = '') {
  for (const [key, icon] of Object.entries(WEATHER_ICONS)) {
    if (condition.toLowerCase().includes(key.toLowerCase())) return icon
  }
  return '🌡️'
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const MOCK_WEATHER = {
  temp: 22,
  condition: 'Partly cloudy',
  location: 'Cairo, EG',
  forecast: [
    { day: '', temp: 24, condition: 'Sunny' },
    { day: '', temp: 20, condition: 'Cloudy' },
    { day: '', temp: 18, condition: 'Rain' },
  ],
}

export default function Weather() {
  const [weather, setWeather] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchWeather = async () => {
      try {
        const res = await fetch('https://wttr.in/?format=j1', {
          signal: AbortSignal.timeout(6000),
        })
        if (!res.ok) throw new Error('fetch failed')
        const data = await res.json()

        const current = data.current_condition?.[0]
        const area = data.nearest_area?.[0]
        const location = area
          ? `${area.areaName?.[0]?.value ?? ''}, ${area.country?.[0]?.value ?? ''}`
          : 'Unknown'

        const today = new Date()
        const forecast = (data.weather || []).slice(0, 3).map((day, i) => {
          const d = new Date(today)
          d.setDate(today.getDate() + i)
          return {
            day: i === 0 ? 'Today' : DAYS[d.getDay()],
            temp: Math.round((parseInt(day.maxtempC) + parseInt(day.mintempC)) / 2),
            condition: day.hourly?.[4]?.weatherDesc?.[0]?.value ?? 'Clear',
          }
        })

        setWeather({
          temp: parseInt(current?.temp_C ?? '20'),
          condition: current?.weatherDesc?.[0]?.value ?? 'Clear',
          location,
          forecast,
        })
      } catch {
        // Use mock data with correct day labels
        const today = new Date()
        setWeather({
          ...MOCK_WEATHER,
          forecast: MOCK_WEATHER.forecast.map((f, i) => {
            const d = new Date(today)
            d.setDate(today.getDate() + i)
            return { ...f, day: i === 0 ? 'Today' : DAYS[d.getDay()] }
          }),
        })
      } finally {
        setLoading(false)
      }
    }

    fetchWeather()
    const interval = setInterval(fetchWeather, 10 * 60 * 1000) // refresh every 10 min
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="weather-panel panel" style={{ flexShrink: 0 }}>
      <div className="panel-header">
        <span className="panel-title">Weather</span>
        <span className="panel-indicator" />
      </div>

      {loading ? (
        <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontFamily: "'Share Tech Mono', monospace", fontSize: '10px', letterSpacing: '1px' }}>
          FETCHING DATA...
        </div>
      ) : weather && (
        <>
          <div className="weather-main">
            <div className="weather-location">
              📍 {weather.location}
            </div>
            <div className="weather-current">
              <div className="weather-temp">{weather.temp}°C</div>
              <div className="weather-icon-condition">
                <div className="weather-icon">{getWeatherIcon(weather.condition)}</div>
                <div className="weather-condition">{weather.condition.toUpperCase()}</div>
              </div>
            </div>
          </div>

          <div className="weather-forecast">
            {weather.forecast.map((day, i) => (
              <div className="forecast-day" key={i}>
                <span className="forecast-day-name">{day.day.toUpperCase().slice(0, 3)}</span>
                <span className="forecast-icon">{getWeatherIcon(day.condition)}</span>
                <span className="forecast-temp">{day.temp}°</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

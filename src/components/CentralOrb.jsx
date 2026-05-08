import React, { useEffect, useRef } from 'react'
import { motion, useAnimation } from 'framer-motion'

const PARTICLE_COUNT = 12

function Particle({ index, total, isListening }) {
  const angle = (index / total) * 360
  const radius = isListening ? 115 + Math.sin(index * 1.2) * 15 : 110
  const rad = (angle * Math.PI) / 180
  const x = Math.cos(rad) * radius
  const y = Math.sin(rad) * radius

  return (
    <motion.div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        width: isListening ? 4 : 3,
        height: isListening ? 4 : 3,
        borderRadius: '50%',
        background: '#00d4ff',
        boxShadow: `0 0 ${isListening ? 10 : 6}px #00d4ff`,
        marginLeft: -1.5,
        marginTop: -1.5,
      }}
      animate={{
        x: [x, x * 1.05, x],
        y: [y, y * 1.05, y],
        opacity: isListening ? [0.8, 1, 0.8] : [0.4, 0.7, 0.4],
        scale: isListening ? [1, 1.5, 1] : [1, 1.2, 1],
      }}
      transition={{
        duration: 2 + (index % 3) * 0.5,
        repeat: Infinity,
        ease: 'easeInOut',
        delay: index * 0.15,
      }}
    />
  )
}

export default function CentralOrb({ isListening, isProcessing, directMode, modelLoading, modelProgress, onOrbClick }) {
  const orbControls = useAnimation()

  useEffect(() => {
    if (isListening) {
      orbControls.start({
        scale: [1, 1.08, 1],
        boxShadow: [
          '0 0 40px rgba(0,212,255,0.35), 0 0 80px rgba(0,212,255,0.15), inset 0 0 40px rgba(0,212,255,0.1)',
          '0 0 70px rgba(0,212,255,0.6), 0 0 130px rgba(0,212,255,0.3), inset 0 0 60px rgba(0,212,255,0.2)',
          '0 0 40px rgba(0,212,255,0.35), 0 0 80px rgba(0,212,255,0.15), inset 0 0 40px rgba(0,212,255,0.1)',
        ],
        transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
      })
    } else if (isProcessing) {
      orbControls.start({
        scale: [1, 1.04, 1],
        boxShadow: [
          '0 0 40px rgba(0,212,255,0.35), 0 0 80px rgba(0,212,255,0.15)',
          '0 0 55px rgba(0,212,255,0.5), 0 0 100px rgba(0,212,255,0.25)',
          '0 0 40px rgba(0,212,255,0.35), 0 0 80px rgba(0,212,255,0.15)',
        ],
        transition: { duration: 1, repeat: Infinity, ease: 'easeInOut' },
      })
    } else {
      orbControls.start({
        scale: 1,
        boxShadow: '0 0 40px rgba(0,212,255,0.3), 0 0 80px rgba(0,212,255,0.12), inset 0 0 40px rgba(0,212,255,0.08)',
        transition: { duration: 0.5 },
      })
    }
  }, [isListening, isProcessing, orbControls])

  const orbText = modelLoading
    ? (modelProgress > 0 ? `LOADING ${modelProgress}%` : 'LOADING...')
    : isProcessing ? 'PROCESSING'
    : directMode ? 'SPEAK NOW'
    : isListening ? 'LISTENING'
    : 'JARVIS'

  return (
    <div className="orb-wrapper">
      {/* Rotating rings */}
      <div className="orb-ring orb-ring-1" />
      <div className="orb-ring orb-ring-2" />
      <div className="orb-ring orb-ring-3" />

      {/* Particles */}
      <div className="orb-particles">
        {Array.from({ length: PARTICLE_COUNT }).map((_, i) => (
          <Particle key={i} index={i} total={PARTICLE_COUNT} isListening={isListening} />
        ))}
      </div>

      {/* Main orb */}
      <motion.div
        className="orb-core"
        animate={orbControls}
        onClick={onOrbClick}
        whileHover={{ scale: 1.03 }}
        whileTap={{ scale: 0.97 }}
        initial={{
          boxShadow: '0 0 40px rgba(0,212,255,0.3), 0 0 80px rgba(0,212,255,0.12), inset 0 0 40px rgba(0,212,255,0.08)',
        }}
      >
        {/* Rotating inner highlight */}
        <motion.div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            background: 'conic-gradient(from 0deg, transparent 0%, rgba(0,212,255,0.08) 25%, transparent 50%)',
          }}
          animate={{ rotate: 360 }}
          transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
        />

        <motion.div
          className="orb-inner-text"
          animate={{
            opacity: isListening ? [0.8, 1, 0.8] : 1,
            letterSpacing: isListening ? ['3px', '5px', '3px'] : '3px',
          }}
          transition={{ duration: 1.5, repeat: isListening ? Infinity : 0 }}
        >
          {orbText}
        </motion.div>
      </motion.div>

      {/* Outer glow ring when listening */}
      {isListening && (
        <motion.div
          style={{
            position: 'absolute',
            width: '230px',
            height: '230px',
            borderRadius: '50%',
            border: '2px solid rgba(0,212,255,0.4)',
            pointerEvents: 'none',
          }}
          animate={{
            scale: [1, 1.3, 1.6],
            opacity: [0.5, 0.2, 0],
          }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
    </div>
  )
}

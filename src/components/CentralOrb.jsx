import React, { useEffect } from 'react'
import { motion, useAnimation } from 'framer-motion'

const PARTICLE_COUNT = 12

function Particle({ index, total }) {
  const angle = (index / total) * 360
  const radius = 110
  const rad = (angle * Math.PI) / 180
  const x = Math.cos(rad) * radius
  const y = Math.sin(rad) * radius

  return (
    <motion.div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        width: 3,
        height: 3,
        borderRadius: '50%',
        background: '#00d4ff',
        boxShadow: '0 0 6px #00d4ff',
        marginLeft: -1.5,
        marginTop: -1.5,
      }}
      animate={{
        x: [x, x * 1.05, x],
        y: [y, y * 1.05, y],
        opacity: [0.4, 0.7, 0.4],
        scale: [1, 1.2, 1],
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

export default function CentralOrb({ isProcessing }) {
  const orbControls = useAnimation()

  useEffect(() => {
    if (isProcessing) {
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
  }, [isProcessing, orbControls])

  return (
    <div className="orb-wrapper">
      <div className="orb-ring orb-ring-1" />
      <div className="orb-ring orb-ring-2" />
      <div className="orb-ring orb-ring-3" />

      <div className="orb-particles">
        {Array.from({ length: PARTICLE_COUNT }).map((_, i) => (
          <Particle key={i} index={i} total={PARTICLE_COUNT} />
        ))}
      </div>

      <motion.div
        className="orb-core"
        animate={orbControls}
        whileHover={{ scale: 1.03 }}
        initial={{
          boxShadow: '0 0 40px rgba(0,212,255,0.3), 0 0 80px rgba(0,212,255,0.12), inset 0 0 40px rgba(0,212,255,0.08)',
        }}
      >
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
          animate={{ opacity: isProcessing ? [0.8, 1, 0.8] : 1 }}
          transition={{ duration: 1.5, repeat: isProcessing ? Infinity : 0 }}
        >
          {isProcessing ? 'PROCESSING' : 'JARVIS'}
        </motion.div>
      </motion.div>
    </div>
  )
}

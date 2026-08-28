# Neural Cortex 3D Graph

The **Neural Cortex 3D Graph** is the topological knowledge and memory visualization engine of JARVIS.

## Architecture

- **Humanoid Brain Geometry**: Generates a 3D dual-hemisphere brain point cloud with temporal lobes, cerebellar structures, and brainstem coordinates based on geometric cortical shell projections.
- **Topological Synaptic Linking**: Notes are connected based on cosine similarity thresholds across their keyword/topic vectors and explicit markdown wiki-links (`[[Note Name]]`).
- **Real-Time 3D Force Simulation**: Uses `3d-force-graph` and `Three.js` to dynamically simulate spring-electrical force physics.
- **Interactive Inspection**: Clicking nodes triggers smooth camera flights (`flyToNode`, `flyToCluster`), pulses synaptic pathways, and displays the Note Inspector panel.
- **Live Memory Capture (`handle_remember`)**: Dictating "remember that..." parses and creates a new markdown note, attaches it to the most relevant knowledge cluster, and regenerates the graph live.

## Related Systems

- [[Zen White Glassmorphic UI]] delivering the visual presentation and HUD controls.
- [[Safety Protocol & Guardrails]] safeguarding memory notes against accidental deletion.
- [[Voice Synthesis & Multi-Key Failover]] providing spoken confirmations of captured memories.

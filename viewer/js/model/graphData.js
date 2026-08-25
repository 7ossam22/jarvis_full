// js/model/graphData.js — wraps the auto-generated GRAPH global
// (graph-data.js, loaded as a plain script before this module) into
// working state: a mutable copy, plus a neighbor index (Model layer).
// Visual highlight state lives in view/scene.js instead — it's consumed
// only by rendering accessors, so it belongs with the View, not here.

export const graphData = {
  nodes: GRAPH.nodes.map(n => ({ ...n })),
  links: GRAPH.links.map(l => ({ ...l })),
};

export function nodeById(id) {
  return graphData.nodes.find(n => n.id === id);
}

// adjacency for "highlight neighbors"
export const neighborsOf = {};
graphData.links.forEach(l => {
  const a = typeof l.source === "object" ? l.source.id : l.source;
  const b = typeof l.target === "object" ? l.target.id : l.target;
  (neighborsOf[a] ||= new Set()).add(b);
  (neighborsOf[b] ||= new Set()).add(a);
});

export function linkKey(l) {
  const a = typeof l.source === "object" ? l.source.id : l.source;
  const b = typeof l.target === "object" ? l.target.id : l.target;
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

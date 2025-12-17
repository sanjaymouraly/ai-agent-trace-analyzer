import { useCallback, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import clsx from 'clsx'
import type { DependencyGraph, GraphNode } from '../../lib/types'
import { actionIcon, fmtMs, humanize, shortId } from '../../lib/format'

interface SpanNodeData extends Record<string, unknown> {
  node: GraphNode
  onSelect: (spanId: string) => void
  selected: boolean
  dimmed: boolean
  critical: boolean
}

function SpanNode({ data }: NodeProps) {
  const { node, onSelect, selected, dimmed, critical } = data as SpanNodeData
  const failed = node.status === 'error' || node.status === 'timeout'
  const tone = failed ? 'border-danger/60 bg-danger/10' : node.status === 'ok' ? 'border-ok/40 bg-ok/8' : 'border-warn/40 bg-warn/8'
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(node.span_id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect(node.span_id)
        }
      }}
      className={clsx(
        'w-56 rounded-lg border px-2.5 py-2 text-left shadow-sm transition',
        tone,
        selected && 'ring-2 ring-accent',
        dimmed && 'opacity-25',
      )}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-line !bg-ink-faint" />
      <div className="flex items-center gap-1.5">
        <span className="text-2xs text-ink-faint" aria-hidden="true">
          {actionIcon(node.action_type)}
        </span>
        <span className="truncate text-2xs font-semibold text-ink">{node.name}</span>
      </div>
      <div className="mt-1 truncate text-2xs text-ink-muted">
        {node.tool_name || humanize(node.action_type)}
        {node.retry_attempt ? ` · attempt ${node.retry_attempt}` : ''}
      </div>
      <div className="mt-0.5 flex items-center justify-between text-2xs">
        <span className={failed ? 'text-danger' : 'text-ink-faint'}>{node.status}</span>
        <span className="font-mono text-ink-faint">
          {node.duration_ms === null || node.duration_ms === undefined ? '—' : fmtMs(node.duration_ms)}
        </span>
      </div>
      {critical && <div className="mt-1 text-2xs font-medium text-warn">critical path</div>}
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-line !bg-ink-faint" />
    </div>
  )
}

const nodeTypes = { span: SpanNode }

interface Layout {
  nodes: Node[]
  edges: Edge[]
  positions: Map<string, { x: number; y: number }>
}

/** Layered layout derived from declared dependency edges (hierarchy is not a dependency). */
function layoutGraph(graph: DependencyGraph): Layout {
  const nodes = graph.nodes || []
  const byId = new Map(nodes.map((node) => [node.span_id, node]))
  const dependencyEdges = graph.edges.filter((edge) => edge.dependency)
  const adjacency = new Map<string, string[]>()
  const indegree = new Map<string, number>()
  nodes.forEach((node) => {
    adjacency.set(node.span_id, [])
    indegree.set(node.span_id, 0)
  })
  dependencyEdges.forEach((edge) => {
    if (!byId.has(edge.source) || !byId.has(edge.target)) return
    adjacency.get(edge.source)!.push(edge.target)
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1)
  })

  const order = new Map((graph.chronological_order || []).map((id, index) => [id, index]))
  const depth = new Map<string, number>()
  const ready = nodes
    .filter((node) => (indegree.get(node.span_id) ?? 0) === 0)
    .map((node) => node.span_id)
    .sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
  ready.forEach((id) => depth.set(id, 0))
  let guard = 0
  while (ready.length && guard < 10000) {
    guard += 1
    const id = ready.shift()!
    for (const child of adjacency.get(id) ?? []) {
      depth.set(child, Math.max(depth.get(child) ?? 0, (depth.get(id) ?? 0) + 1))
      indegree.set(child, (indegree.get(child) ?? 0) - 1)
      if ((indegree.get(child) ?? 0) === 0) {
        ready.push(child)
        ready.sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
      }
    }
  }
  // Cyclic or unresolvable components fall back to a flat, chronological layout.
  nodes.forEach((node, index) => {
    if (!depth.has(node.span_id)) depth.set(node.span_id, Math.floor(index / 4))
  })

  const layers = new Map<number, GraphNode[]>()
  nodes.forEach((node) => {
    const level = depth.get(node.span_id) ?? 0
    if (!layers.has(level)) layers.set(level, [])
    layers.get(level)!.push(node)
  })
  layers.forEach((list) => list.sort((a, b) => (order.get(a.span_id) ?? 0) - (order.get(b.span_id) ?? 0)))

  const positions = new Map<string, { x: number; y: number }>()
  layers.forEach((list, level) => {
    list.forEach((node, index) => {
      positions.set(node.span_id, { x: 40 + level * 300, y: 30 + index * 108 })
    })
  })

  const critical = new Set(graph.critical_path?.span_ids ?? [])
  const flowNodes: Node[] = nodes.map((node) => ({
    id: node.span_id,
    type: 'span',
    position: positions.get(node.span_id) ?? { x: 0, y: 0 },
    data: { node, onSelect: () => undefined, selected: false, dimmed: false, critical: critical.has(node.span_id) },
  }))

  const flowEdges: Edge[] = (graph.edges || []).map((edge) => {
    const isCritical = critical.has(edge.source) && critical.has(edge.target)
    const isDependency = edge.dependency
    const failedSource = byId.get(edge.source)?.status === 'error' || byId.get(edge.source)?.status === 'timeout'
    return {
      id: `${edge.source}->${edge.target}:${edge.kind}`,
      source: edge.source,
      target: edge.target,
      label: edge.kind === 'tool_call_reference' ? 'tool call' : undefined,
      animated: isDependency && failedSource,
      style: {
        stroke: isCritical ? 'rgb(var(--warn))' : isDependency ? 'rgb(var(--accent))' : 'rgb(var(--line-strong))',
        strokeWidth: isCritical ? 2.4 : isDependency ? 1.6 : 1,
        strokeDasharray: edge.kind === 'span_link' ? '4 4' : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: isCritical ? 'rgb(var(--warn))' : isDependency ? 'rgb(var(--accent))' : 'rgb(var(--line-strong))' },
      title: edge.evidence,
    }
  })

  return { nodes: flowNodes, edges: flowEdges, positions }
}

export default function DependencyGraphView({
  graph,
  selectedSpanId,
  onSelect,
}: {
  graph: DependencyGraph
  selectedSpanId: string | null
  onSelect: (spanId: string | null) => void
}) {
  const [search, setSearch] = useState('')
  const [onlyDependencies, setOnlyDependencies] = useState(false)
  const [showCritical, setShowCritical] = useState(false)

  const layout = useMemo(() => layoutGraph(graph), [graph])
  const critical = useMemo(() => new Set(graph.critical_path?.span_ids ?? []), [graph])

  const query = search.trim().toLowerCase()
  const matches = useMemo(() => {
    if (!query) return null
    const set = new Set<string>()
    graph.nodes.forEach((node) => {
      const haystack = `${node.name} ${node.tool_name ?? ''} ${node.span_id} ${node.action_type} ${node.status}`.toLowerCase()
      if (haystack.includes(query)) set.add(node.span_id)
    })
    return set
  }, [query, graph.nodes])

  const nodes = useMemo(
    () =>
      layout.nodes.map((node) => {
        const data = node.data as SpanNodeData
        const dimmed = matches ? !matches.has(node.id) : false
        const hiddenByCritical = showCritical && !critical.has(node.id)
        return {
          ...node,
          hidden: hiddenByCritical,
          data: { ...data, onSelect, selected: selectedSpanId === node.id, dimmed },
        }
      }),
    [layout, matches, onSelect, selectedSpanId, showCritical, critical],
  )

  const edges = useMemo(() => {
    if (!onlyDependencies) return layout.edges
    const failed = new Set(
      graph.nodes.filter((node) => node.status === 'error' || node.status === 'timeout').map((node) => node.span_id),
    )
    return layout.edges.filter((edge) => failed.has(edge.source) || failed.has(edge.target))
  }, [layout.edges, onlyDependencies, graph.nodes])

  const handleNodesChange = useCallback(() => undefined, [])

  if (!graph.nodes?.length) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-line text-xs text-ink-muted">
        This run has no spans to reconstruct.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="field max-w-xs"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Find an action, tool or span id…"
          aria-label="Search the dependency graph"
        />
        <label className="flex items-center gap-1.5 text-2xs text-ink-muted">
          <input
            type="checkbox"
            checked={onlyDependencies}
            onChange={(event) => setOnlyDependencies(event.target.checked)}
            className="accent-[rgb(var(--accent))]"
          />
          Only failure-related edges
        </label>
        <label className="flex items-center gap-1.5 text-2xs text-ink-muted">
          <input
            type="checkbox"
            checked={showCritical}
            onChange={(event) => setShowCritical(event.target.checked)}
            className="accent-[rgb(var(--accent))]"
          />
          Critical path only
        </label>
        {selectedSpanId && (
          <button type="button" className="btn btn-sm" onClick={() => onSelect(null)}>
            Clear selection
          </button>
        )}
        <span className="ml-auto text-2xs text-ink-faint">
          {graph.nodes.length} actions · {graph.edges.length} links
          {graph.critical_path_supported ? ` · critical path ${fmtMs(graph.critical_path_ms)}` : ' · critical path unsupported'}
        </span>
      </div>

      <div className="h-[26rem] overflow-hidden rounded-lg border border-line bg-surface-sunken">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={handleNodesChange}
            onNodeClick={(_, node) => onSelect(node.id)}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            minZoom={0.15}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
            nodesDraggable
          >
            <Background gap={22} size={1} color="rgb(var(--line))" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </ReactFlowProvider>
      </div>

      <p className="text-2xs leading-relaxed text-ink-faint">
        Mint edges are declared dependencies or shared tool-call references. Grey edges are parent/child hierarchy and
        amber edges are the computed critical path (only shown when the dependency graph supports it). Chronological
        order is never used as a causal edge.
      </p>

      {graph.warnings?.length > 0 && (
        <ul className="space-y-1.5">
          {graph.warnings.map((warning, index) => (
            <li
              key={`${warning.type}-${index}`}
              className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-2xs text-ink-muted"
            >
              <strong className="text-warn">{humanize(warning.type)}</strong>
              {warning.span_id && <span className="mono ml-1.5 text-ink-faint">{shortId(warning.span_id)}</span>}
              <div>{warning.message}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeChange,
  type OnSelectionChangeParams,
} from "@xyflow/react";
import { useCallback, useEffect, useRef } from "react";
import { api } from "../api";
import type { Board } from "../types";
import { useStudio } from "../useStudio";
import { ReelNode } from "./ReelNode";
import { ScriptNode } from "./ScriptNode";
import { SequenceNode } from "./SequenceNode";

const NODE_TYPES = { script: ScriptNode, sequence: SequenceNode, reel: ReelNode };

// Auto-layout when a board has no saved positions: script above, beats in a row, reel last.
const COLUMN = 330;  // node is 240 wide; the gap is where the wire reads
const ROW_Y = 260;

/**
 * Node state lives in React Flow's own hooks rather than being derived on every render.
 * React Flow is a controlled component that writes back layout measurements, so feeding it
 * a freshly-built array each render puts it in an update loop. Instead the board is synced
 * in when it actually changes, and positions already on screen are preserved.
 */
function buildNodes(board: Board, existing: Node[]): Node[] {
  const saved = board.canvas?.nodes ?? {};
  const current = new Map(existing.map((node) => [node.id, node.position]));
  const at = (id: string, fallback: { x: number; y: number }) =>
    current.get(id) ?? saved[id] ?? fallback;

  const list: Node[] = [
    { id: "script", type: "script", position: at("script", { x: 0, y: 0 }), data: {} },
  ];
  board.beats.forEach((beat, index) => {
    list.push({
      id: `beat-${beat.n}`,
      type: "sequence",
      position: at(`beat-${beat.n}`, { x: index * COLUMN, y: ROW_Y }),
      data: { beat },
    });
  });
  // The reel node tracks the end of the chain, so it ignores its current on-screen position
  // and only yields to one the user deliberately dragged. Otherwise adding a beat drops the
  // new node exactly on top of it -- same column, same row.
  list.push({
    id: "reel",
    type: "reel",
    position: saved.reel ?? { x: board.beats.length * COLUMN, y: ROW_Y },
    data: {},
  });
  return list;
}

/**
 * The wire IS the frame handoff, so it is drawn from what each beat actually does: solid
 * green where a beat continues from the previous clip, dashed amber where it opens on its
 * own still and therefore costs an image from the quota.
 */
function buildEdges(board: Board): Edge[] {
  const list: Edge[] = [];
  board.beats.forEach((beat, index) => {
    const chained = beat.source === "chain" && index > 0;
    list.push({
      id: `wire-${beat.n}`,
      source: index === 0 ? "script" : `beat-${board.beats[index - 1].n}`,
      target: `beat-${beat.n}`,
      type: index === 0 ? "smoothstep" : "default",
      animated: beat.state === "rendering",
      style: chained
        ? { stroke: "#4ade80", strokeWidth: 1.5 }
        : { stroke: "#d99a4e", strokeWidth: 1.5, strokeDasharray: "5 4" },
      label: chained ? undefined : "cut",
      labelStyle: { fill: "#d99a4e", fontSize: 9 },
      labelBgStyle: { fill: "#16161b" },
    });
  });
  if (board.beats.length) {
    list.push({
      id: "wire-reel",
      source: `beat-${board.beats[board.beats.length - 1].n}`,
      target: "reel",
      style: { stroke: "#3f3f46", strokeWidth: 1.5 },
    });
  }
  return list;
}

export function Canvas() {
  const studio = useStudio();
  const setSelection = studio.setSelection;
  const board = studio.board!;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  const beatCount = useRef<number | null>(null);

  useEffect(() => {
    // Beat numbers are positional IDs. After insertion/removal they may refer to different
    // scenes, so discard the old on-screen positions and let the server-cleared layout
    // reflow the one-dimensional chain without overlaps.
    const structureChanged =
      beatCount.current !== null && beatCount.current !== board.beats.length;
    setNodes((existing) => buildNodes(board, structureChanged ? [] : existing));
    setEdges(buildEdges(board));
    if (structureChanged) setSelection([]);
    beatCount.current = board.beats.length;
  }, [board, setNodes, setEdges, setSelection]);

  // Absorb React Flow's own changes (dimensions, selection, drag) first, or it re-emits
  // them forever waiting to be acknowledged.
  const handleNodesChange = useCallback(
    (changes: NodeChange<Node>[]) => onNodesChange(changes),
    [onNodesChange],
  );

  /** Persist layout once, when the drag ends -- not on every frame of it. */
  const handleDragStop = useCallback(() => {
    const positions = Object.fromEntries(
      nodes.map((node) => [
        node.id,
        { x: Math.round(node.position.x), y: Math.round(node.position.y) },
      ]),
    );
    void studio.guard(() => api.patchBoard(board.slug, { canvas: { nodes: positions } }));
  }, [board.slug, nodes, studio]);

  /**
   * Selection is agy's context: "make this slower" needs to know which beat.
   *
   * Only ever set from a non-empty selection. Clicking into the chat box blurs the canvas
   * and React Flow reports an empty selection, which would silently drop the context the
   * user just chose -- and send "make this slower" with nothing attached. Clearing is
   * therefore an explicit gesture: click the empty canvas, or the × on the context chip.
   */
  const handleSelectionChange = useCallback(
    ({ nodes: selected }: OnSelectionChangeParams) => {
      const beats = selected
        .filter((node) => node.type === "sequence")
        .map((node) => Number(node.id.replace("beat-", "")))
        .sort((a, b) => a - b);
      if (beats.length) studio.setSelection(beats);
    },
    [studio],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      onNodesChange={handleNodesChange}
      onNodeDragStop={handleDragStop}
      onSelectionChange={handleSelectionChange}
      onPaneClick={() => studio.setSelection([])}
      // The topology is fixed -- a beat chain, not a free-form graph -- so users cannot
      // draw or break wires. They change the handoff on the node instead.
      nodesConnectable={false}
      edgesFocusable={false}
      elementsSelectable
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.25}
      maxZoom={1.5}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#26262e" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

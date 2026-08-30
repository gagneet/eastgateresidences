"use client";

import React, {useMemo, useState} from "react";
import {
    ReactFlow,
    Background,
    Controls,
    MiniMap,
    type Node,
    type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
    interactiveMindmapNodes,
    interactiveMindmapEdges,
    MindmapNode,
    MindmapEdge,
} from "@/lib/mindmap/interactiveMindmapData";
/**
 * @generated FunctionHeader
 * Function: toFlowNodes
 * Path: frontend/src/components/tech-docs/InteractiveMindmapFlow.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function toFlowNodes(nodes: MindmapNode[]): Node[] {
    return nodes.map((n) => ({
        id: n.id,
        position: {x: n.x, y: n.y},
        data: {label: n.label, node: n},
        type: "default",
    }));
}
/**
 * @generated FunctionHeader
 * Function: toFlowEdges
 * Path: frontend/src/components/tech-docs/InteractiveMindmapFlow.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function toFlowEdges(edges: MindmapEdge[]): Edge[] {
    return edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
    }));
}
/**
 * @generated FunctionHeader
 * Function: InteractiveMindmapFlow
 * Path: frontend/src/components/tech-docs/InteractiveMindmapFlow.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function InteractiveMindmapFlow() {
    const [selected, setSelected] = useState<MindmapNode | null>(null);

    const nodes = useMemo(() => toFlowNodes(interactiveMindmapNodes), []);
    const edges = useMemo(() => toFlowEdges(interactiveMindmapEdges), []);

    return (
        <div className="w-full h-[85vh] flex">
            <div className="flex-1">
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodeClick={(_e: React.MouseEvent, node: Node) => setSelected(node.data.node as MindmapNode)}
                    fitView
                >
                    <MiniMap/>
                    <Controls/>
                    <Background/>
                </ReactFlow>
            </div>

            {/* Side Panel */}
            <div className="w-[380px] border-l bg-white overflow-y-auto p-4">
                {!selected && (
                    <div className="text-sm text-gray-500">
                        Click a node to explore APIs, DB collections, UI, and jobs.
                    </div>
                )}

                {selected && (
                    <div>
                        <h2 className="text-lg font-bold mb-2">{selected.label}</h2>
                        <p className="text-sm text-gray-600 mb-3">{selected.detail}</p>

                        {selected.panel.summary && (
                            <Section title="Summary" items={[selected.panel.summary]}/>
                        )}

                        <Section title="Frontend" items={selected.panel.frontend}/>
                        <Section title="APIs" items={selected.panel.apis}/>
                        <Section title="Collections" items={selected.panel.collections}/>
                        <Section title="Fields" items={selected.panel.fields}/>
                        <Section title="Jobs" items={selected.panel.jobs}/>
                        <Section title="Docs" items={selected.panel.docs}/>
                        <Section title="Issues" items={selected.panel.issues}/>
                    </div>
                )}
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: Section
 * Path: frontend/src/components/tech-docs/InteractiveMindmapFlow.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Section({title, items}: { title: string; items?: string[] }) {
    if (!items || items.length === 0) return null;

    return (
        <div className="mb-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-1">{title}</h3>
            <ul className="text-xs text-gray-600 space-y-1">
                {items.map((i, idx) => (
                    <li key={idx}>• {i}</li>
                ))}
            </ul>
        </div>
    );
}

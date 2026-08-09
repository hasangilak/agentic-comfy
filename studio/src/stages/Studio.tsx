import { ReactFlowProvider } from "@xyflow/react";
import { Canvas } from "../canvas/Canvas";
import { CanvasToolbar } from "../panels/CanvasToolbar";

/**
 * The assembly stage: the chain, and the one control that spends money.
 *
 * The canvas used to be the whole app. It is one of four stages now, and it keeps exactly what
 * it is uniquely good at — showing the reel as a chain of joins, where a cut and an unbroken
 * take are two different wires — plus the money bar floating over it, because the price it
 * quotes is the price of the beats you can see.
 *
 * No page chrome: the canvas fills the card. `CanvasToolbar` carries the title.
 */
export function Studio() {
  return (
    <ReactFlowProvider>
      <CanvasToolbar />
      <Canvas />
    </ReactFlowProvider>
  );
}

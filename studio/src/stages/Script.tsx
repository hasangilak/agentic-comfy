import { useState } from "react";
import { api } from "../api";
import { JOIN_LOOK } from "../joins";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { TalkItOut, TheBrief } from "./TalkItOut";
import { StagePage, WaitingOn } from "./parts";

/**
 * Stage one: the film as words.
 *
 * Everything on this page is free and nothing on it reaches a renderer, which is what makes it
 * the right place to be indecisive. The style bible in particular belongs here rather than on a
 * node: it is one paragraph that every still and every clip is told, so it is a decision about
 * the whole film, not about a scene.
 *
 * Two columns whatever state the reel is in — the conversation on the left, what exists on the
 * right. Before the script lands, "what exists" is the brief the model is interviewing from;
 * after, it is the script itself.
 */
export function Script() {
  const studio = useStudio();
  const board = studio.board!;
  const written = board.beats.length > 0;

  const title = useDraft(board.title, (next) =>
    void studio.guard(() => api.patchBoard(board.slug, { title: next })),
  );
  const bible = useDraft(board.style_bible, (next) =>
    void studio.guard(() => api.patchBoard(board.slug, { style_bible: next })),
  );

  const seconds = board.beats.reduce((sum, beat) => sum + beat.actual_seconds, 0);
  const notes = board.notes ?? [];
  const [crewBusy, setCrewBusy] = useState(false);
  const crewJob = useBusy("crew", () => true);
  const agentJob = useBusy("agent", () => true);
  const crewRunning = crewJob || agentJob;
  const done = new Set(board.crew?.done ?? []);
  const awaiting = board.crew?.awaiting ?? null;
  const extracted = done.has("extract") || board.staging.length > 0;
  const wantsExtract =
    written && (!extracted || awaiting === "extract") && !done.has("panels") && !done.has("lock");

  const extractCast = () => {
    setCrewBusy(true);
    void studio
      .guard(() => api.runCrew(board.slug, { stage: "storyboard", phase: "extract" }))
      .then(() => studio.goStage("storyboard"))
      .finally(() => setCrewBusy(false));
  };
  const busy = crewBusy || crewRunning;

  return (
    <StagePage
      stage="script"
      title="Script"
      blurb={
        written
          ? `${board.beats.length} scenes · ${seconds.toFixed(1)}s · free to change`
          : "four questions, then the model writes it"
      }
      waiting={
        !written ? (
          <WaitingOn tone="quiet">
            Nothing is written yet. Fill in the form it shows — or leave any question as{" "}
            <em>it&apos;s on you</em>, or skip straight to writing with the button under the
            composer.
          </WaitingOn>
        ) : wantsExtract ? (
          <WaitingOn
            action={
              <div className="flex flex-wrap gap-2">
                <Button tone="primary" onClick={extractCast} disabled={busy}>
                  {busy ? "working…" : "Extract the cast"}
                </Button>
                <Button onClick={() => studio.goStage("storyboard")}>→ Storyboard</Button>
              </div>
            }
          >
            The script is written. Mise-en-scène names every recurring character and place
            next — sheets come after the storyboard.
            {notes.length
              ? ` ${notes.length === 1 ? notes[0] : `${notes.length} things are thin about this script.`}`
              : ""}
          </WaitingOn>
        ) : notes.length ? (
          <WaitingOn
            action={<Button onClick={() => studio.goStage("storyboard")}>→ Storyboard</Button>}
          >
            {notes.length === 1 ? notes[0] : `${notes.length} things are thin about this script.`}
          </WaitingOn>
        ) : (
          <WaitingOn
            tone="quiet"
            action={<Button onClick={() => studio.goStage("storyboard")}>→ Storyboard</Button>}
          >
            The cast is named. Next is how each shot is framed.
          </WaitingOn>
        )
      }
    >
      <div className="grid h-full min-h-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="flex min-h-0 flex-col">
          <TalkItOut />
        </div>

        <div className="thin min-h-0 space-y-4 overflow-y-auto">
          {written ? (
            <>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wide text-zinc-400">
                  title
                </span>
                <input
                  className={inputClass}
                  value={title.draft}
                  onChange={(event) => title.change(event.target.value)}
                  onBlur={title.flush}
                  placeholder="reel title"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wide text-zinc-400">
                  style bible
                </span>
                <textarea
                  className={`${inputClass} thin h-40 leading-relaxed`}
                  value={bible.draft}
                  onChange={(event) => bible.change(event.target.value)}
                  onBlur={bible.flush}
                  placeholder="the medium, the exact character, the palette — look only, never motion"
                />
                <span className="mt-1 block text-[10px] leading-snug text-zinc-400">
                  Reused in every still prompt and every clip prompt. Look only — motion belongs
                  to a scene's action line.
                </span>
              </label>

              {/* Persistent rather than a strip that vanished after import: these are derived
                  from the beats on every read, so they clear themselves as the gaps are filled
                  instead of being a message that was true once. */}
              {notes.length ? (
                <div className="space-y-1.5 rounded-2xl border border-stale/30 bg-stale/5 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-stale">worth fixing</p>
                  {notes.map((note) => (
                    <p key={note} className="text-[10px] leading-relaxed text-stale">
                      {note}
                    </p>
                  ))}
                  <p className="text-[10px] leading-relaxed text-zinc-500">
                    None of it blocks anything, and all of it is free to fix.
                  </p>
                </div>
              ) : null}

              <div className="space-y-2.5">
                {board.beats.map((beat) => (
                  <SceneRow key={beat.n} n={beat.n} />
                ))}
              </div>
            </>
          ) : (
            <TheBrief />
          )}
        </div>
      </div>
    </StagePage>
  );
}

/** One scene, read and edited as prose. The join is shown but not changed: that is the Studio. */
function SceneRow({ n }: { n: number }) {
  const studio = useStudio();
  const board = studio.board!;
  const beat = board.beats.find((entry) => entry.n === n)!;
  const look = JOIN_LOOK[beat.source];
  const [open, setOpen] = useState(false);

  const scene = useDraft(beat.scene, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, n, { scene: next })),
  );
  const action = useDraft(beat.action, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, n, { action: next })),
  );

  return (
    <div className="rounded-2xl border border-edge bg-panel p-3">
      <div className="mb-2 flex items-center gap-2 text-[10px] text-zinc-400">
        <span className="font-medium text-zinc-600">scene {beat.n}</span>
        <span>{beat.actual_seconds.toFixed(1)}s</span>
        <span className={look.tone} title={look.hint}>
          {look.short}
        </span>
        <button
          onClick={() => setOpen((value) => !value)}
          className="ml-auto rounded-lg px-1.5 py-0.5 transition-colors hover:bg-hover hover:text-zinc-700"
          title="edit the lines"
        >
          {open ? "▾" : "✎"}
        </button>
        <button
          onClick={() => studio.setExpanded(beat.n)}
          className="rounded-lg px-1.5 py-0.5 transition-colors hover:bg-hover hover:text-zinc-700"
          title="open the whole scene"
        >
          ⤢
        </button>
      </div>
      {open ? (
        <>
          <input
            className={`${inputClass} mb-1.5`}
            value={scene.draft}
            onChange={(event) => scene.change(event.target.value)}
            onBlur={scene.flush}
            placeholder="where we are — one line, shared by every beat of the same shot"
          />
          <textarea
            className={`${inputClass} thin h-20 leading-relaxed`}
            value={action.draft}
            onChange={(event) => action.change(event.target.value)}
            onBlur={action.flush}
            placeholder="one continuous movement, and nothing else"
          />
        </>
      ) : (
        <div className="space-y-1">
          <p className="text-[11px] leading-relaxed text-zinc-500">{beat.scene || "—"}</p>
          <p className="text-[11px] leading-relaxed text-zinc-700">{beat.action || "—"}</p>
        </div>
      )}
    </div>
  );
}

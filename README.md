# Paper-cutout Reels

Turn a one-line concept into a vertical 1080×1920 Instagram Reel in handcrafted
paper-cutout stop-motion, using MiniMax-H3 on a single GPU on Modal.

Scripts come from **Gemini** through Google's API — the same model then carries out board
edits, writes the caption, and *looks at* every still with its vision head to check it belongs
in the reel. Opening stills come from **Papercut Studio** in `image/`, using Gemini Nano Banana
through the same API and the same key. Words, image generation and video rendering are all
explicit stages, so the stages are deliberately separable. A script you wrote yourself is
imported as it stands, with no planning turn at all.

```
concept ─Gemini─▶ ┐
                  ├──▶ storyboard.json ──Gemini──▶ opening still  (image/, API)
  your own script ┘                                  │
                                     Gemini looks at it: same cast? ──reject──▶ rewrite, again
                                                    │
                                          compose 9:16 frame (local, free)
                                                    │
                          ONE warm Modal container: N chained beats  ◀── the only cost
                             beat N starts from beat N−1's last frame
                                                    │
                                        stitch ──▶ 1080×1920 H.264/AAC
```

## Setup

```bash
# X-GOOG-API-KEY=... in .env                          # the script, the stills, one key
make login                                             # once — uvx modal setup
make models                                            # once, ~177 GiB into a Volume
```

The download is separate on purpose: it lands in a persistent Volume, so cold starts
never re-pay for it. **Never needs re-running** — that's why cold start is ~10–30 s
rather than the minutes a HuggingFace pull would take.

### Endpoint authentication

The deployment is **authenticated by default**. The URL serves the full ComfyUI API,
the model weights, and every render in the output Volume, so a public URL is a real
exposure.

Mint a proxy-auth token at <https://modal.com/settings/proxy-auth-tokens> — these are
`wk-`/`ws-` credentials, a *different* type from the `ak-` CLI token in `~/.modal.toml`,
which will earn a 401 — then:

```bash
export MODAL_PROXY_TOKEN_ID=wk-...
export MODAL_PROXY_TOKEN_SECRET=ws-...
```

To poke at ComfyUI's browser UI, deploy a throwaway public instance instead (a browser
cannot attach auth headers to the UI's WebSocket requests), and re-deploy without the
flag afterwards:

```bash
PAPERREEL_PUBLIC=1 uvx modal deploy comfyui_minimax_h3.py
```

## The studio

A node canvas: talk to the model, get a script and a chain of shots, render when you're
ready.

```bash
make install                                           # npm + the uv environment
make run                                               # :8791 stills, :8787 API, :5173 UI
```

Use the Vite URL — it proxies the API through. `make run` starts all three servers and
takes them down together, so Ctrl-C never leaves one holding a port (`make studio` is an
alias for it). On a session that is only editing a script, `make backend` and `make frontend`
are the same thing without the image server.
`make serve` builds the frontend instead and serves everything from :8787; `make backend`,
`make frontend` and `make images` run one at a time; `make stop` kills every server either
project has running, whichever target started it.
`make help` lists the rest. Nothing in the Makefile can start a paid render.

### Where stills come from, and how they are checked

**Papercut Studio** (`image/`, Gemini Nano Banana) is the only still generator. It renders
straight onto H3's 768×1344 generation grid so the
frame reaches the video model exactly as it was approved rather than centre-cropped on the way
in. With that server not listening there is nothing to fall back to — a beat's still is an
upload, which is what the **my own** switch below is for.

Papercut is shown the reel's cast reference in **anchor** mode, so a cut changes the setting
rather than the characters. A board with no still at all renders its first beat alone,
unconditioned — that image becomes the reference the rest are anchored to.

**And the beat's own reference pictures, if it has any.** The pictures dropped on a scene's
reference tray go to the still renderer as well as to the video model — the first
`config.MAX_STILL_REFS` of them (9, the cast reference counting as one), because the still
request should stay bounded. The image server
reports its own cap in `limits.maxReferences` and the smaller number wins.
The reason to send them at all is that the still is what the clip's first sampling steps are
anchored to: a prop the *clip* is held to and the frame it opens on never saw is two answers to
what the same object looks like. Their one-line notes ride along too, appended to the frame's
prompt as *the reference images show: …*, for the same reason they exist on the video side —
shown a picture with no explanation, a model reads the picture as the scene.

Uploads on a beat that has since been moved off the reference join are ignored here, exactly as
they are in the video render: the join decides whether a picture counts, in one place.

Then the model looks at what came back. A style bible is words, and the same paragraph that
produced a round-eared pig in beat 1 produces a sharper-eared one in beat 4 — neither prompt
wrong, and until now nothing ever checked. Each finished still goes back to Gemini's vision head
alongside the cast reference, judged on the cast, the medium, the palette and the frame, and
explicitly *not* on the setting or the framing, which are supposed to differ. A still that
misses gets its `asset_prompt` rewritten against the specific mismatch and is rendered again:

```
[stills] beat 4: done in 20.3s
[stills] beat 4: the characters (foxes) do not match the recurring character in the style bible
[stills] beat 4: the palette (greens, oranges) violates the strict 6-colour limit
[stills] beat 4: prompt rewritten -> Vertical 9:16 portrait composition of a handcrafted …
[stills] beats [4]: rendering again from the rewritten prompts
```

One retry, not five (`PAPERREEL_STILL_ATTEMPTS`): Gemini is metered and not instant, and a still
rejected twice is usually telling you the style bible is the problem. The rewritten prompt is
saved to the board, so the node shows what changed and the next render starts from the
corrected wording. `PAPERREEL_STILL_REVIEW=0` turns the whole pass off.

The reference-defining beat is rendered and reviewed entirely on its own, before anything else
starts. Its still *is* the reference the others are matched against, so reviewing it at the end
of a batch would be too late to matter.

By hand, if you prefer:

```bash
cd studio && npm install && npm run build && cd ..
uv run studio.py                                       # http://127.0.0.1:8787
```

### Talking to a still

The review answers one question — *does this belong in the reel* — and it is usually not the
question you have. "The pig should be facing the other way" is not a mismatch with anything; it
is taste, and the reviewer is explicitly told not to reject for it. So every still has a
conversation of its own, on its node: **✎ talk about this still**.

Same model, same vision head, different prompt. This turn is shown the picture itself, everything
that picture is drawn from — the cast reference and the beat's own reference pictures — and
everything already said about that one image; what it writes back is the beat's `asset_prompt`,
and then it renders the still again — free, ~10–18 s, and the video is never touched.

```
✎ her ears are too pointed, and move the lamp off the table
  Rounded the ears back to the reference and put the lamp on the windowsill.
  ✦ rendered again
```

**A note can arrive with pictures attached** (⤒ picture, beside send). They are not context for
one turn: they are stored on the scene exactly as the reference tray stores them, which is the
only way an image reaches the renderer at all — `Board.still_pictures` reads the beat, so a
transient upload would steer the model's words and nothing else. So attaching carries the tray's
consequence too, and the panel says so before you send: the scene moves onto the reference join,
which on a continuation means it stops continuing. An attachment also forces the re-render rather
than leaving it to the model — the conditioning changed, so what is on screen was drawn from
something the scene no longer says.

Three things worth knowing:

- **The automatic review does not run on what this renders.** Half of what a director asks for
  here is a departure from the cast reference — asked for rounder ears, the reviewer would compare
  the result against a reference with sharp ones, call it drift and rewrite your note away. The
  review is for stills nobody has looked at. This one has been.
- **"Same thing again, a different draw" works.** With the prompt unchanged the still is redrawn
  on a seed the beat has not used, because Papercut derives a frame's seed from the scene's and a
  plain re-render would otherwise come back byte-identical.
- **The reviewer posts here too**, so the panel is the whole history of how that picture got to be
  what it is — including the turn where the prompt you can see was rewritten between the render
  you asked for and the one you got.

Talking to beat 1's still redraws the reel's cast reference, since that is what it is by default.
That stays allowed — otherwise the first image a board ever produced would lock its cast forever —
and it is said in the job log when it happens. Existing stills are left alone; regenerate the ones
you want matched.

The window the model is shown is the last `PAPERREEL_ASSET_CHAT_HISTORY` (12) lines; the board
keeps `PAPERREEL_ASSET_CHAT_MEMORY` (60), which is what you read on the node.

### One scene, full screen

A node is 240px wide because the whole chain has to stay readable at once, which makes it the
wrong place to actually *look* at a picture — the still is 36px tall there and the conversation
about it is a 40px scroller. **⤢** in a node's header opens the same scene full screen: the
same state, the same endpoints, a second view and never a second copy.

The strip along the bottom is everything that scene owns, in the order the prompt numbers them —
its still, the reel's cast reference, each reference picture, the rendered clip, the frame it
actually opened on. Pick one and the right-hand column is what can be done to *that* thing: the
prompt a still is drawn from and the conversation about it, what a picture is FOR, the clip's
download. Esc, the ×, or a click outside closes it.

Deliberately not in there: the join and the ▶ render checkbox. Both are decisions about the
chain, which is what the canvas exists to show — the join is drawn as the wire between two nodes
and stops meaning anything when you can only see one of them.

### Rewriting a line instead of typing it

The scene and the action are both rendered — the video prompt is the style bible, then the
scene, then the action — so both are ordinary text boxes you can edit. **✎ revise** beside
either hands it to the model instead:

```
✎ slower, and it must read as carrying straight on from the beat before
  Replaced "deliberate slowness" with "agonizing slowness" to stretch the movement
  over the 10-second beat.
```

The board's own chat panel can already do this — it is one `set_beat` call — but only after
working out from the sentence which scene and which line you meant, which is the part that goes
wrong on a board where every beat says something similar. Here both are decided by which box you
typed in, so the turn is spent on the writing. It is held to the same rules as every other
prompt that writes a beat (one copy of them, shared with the chat agent: the camera never moves,
one thing animates, a continuation reads as continuing), it is shown the scenes either side for
exactly that reason, and it marks the beat for re-rendering just as typing the change would. The
turn lands in the board's transcript too, so the next thing you say to the chat panel knows the
line moved.

### Three ways to start a film

**talk it through** is the default and the one that asks you anything. It puts the reel on disk
from your first message — with no beats yet — and then interviews you: how long the film
runs and how that time is split across 5s/10s beats, how many camera setups, who is in it,
what the last frame leaves you with. Those four
questions are not the studio's; they are section 0 of the authoring brief below, which opens
"STOP — interview the director first". Say *defaults* at any point and it picks. When it has
answers it writes the script, marks it against the brief's own self-check, and the reel you were
already looking at fills in — the URL never moves, and the interview stays in the board's
transcript as the first half of every later conversation about it.

**write it for me** is the one-shot path: a concept, a beat count, one length for every beat,
and a finished shot list a few minutes later with no questions asked.

**paste a script** adopts one that already exists — hand-written, or written with an AI
somewhere else — verbatim: beat order, per-beat lengths and which beats are cuts all arrive as
written, and no model turn happens. Talking a model into a script you have already finished is
slower and loses detail on the way.

All three are written against the *same* brief. `prompts/40s-stop-motion-script.md` is the
prompt that gets an AI to write a script — and it is also, verbatim, what the model is handed by
the other two: **talk it through** sends it whole, and **write it for me** replaces only its
opening interview with the beat count and length you already gave. There is deliberately no
second, shorter copy of those rules inside `planner.py` or `develop.py`: a summary that drifts
from the document would have the three ways into a board quietly writing to different
specifications.

### The four stages

The studio is a sequence, and each stage is a page with its own URL:

**Script** — the conversation above, the style bible, and every scene as prose. **Storyboard** —
the cast, the sets and the props, then a rough grey panel per shot and the whole reel as one
contact sheet; picking a design turns the grid into a checklist, so one character is bound
across seven shots in seven clicks. **Assets** — the still each shot opens on, beside the list
of what it is *actually* drawn from, in the order Gemini gets it and with anything past the cap
greyed out and explained. **Studio** — the canvas: the joins, the chain, the price and the
render.

Nothing gates a stage. The rail says what each one is waiting on and every stage is one click
away at any time — the pipeline genuinely is separable, and a reel that supplies its own stills
skips the third stage entirely. Only the last one spends money, and the price is on the button.

Then the model marks its own work. The brief ends in a 22-point self-check, so a second pass
hands the whole brief back with the draft and asks it to run that list — which catches the
faults that are invisible on the page and unmissable in the render: a chained beat whose action
does not pick up where the last one ended, a character description that has drifted from the
style bible, a `[Character Description]` placeholder left in an `asset_prompt`, no genuine
close-up anywhere in the film. Every correction is named in the job log. Plan plus review is
about five minutes and costs nothing; `PAPERREEL_PLAN_REVIEW=0` skips it.

The joins are the model's to choose, which they were not before: it decides where the cuts go
and where a take carries on unbroken, inside the rules of section 2. That used to be overwritten
with "beat 1 is a cut, everything after it chains", because a cut cost one image from a
five-per-five-hours quota. Stills are ordinary API requests now, so the shape of the film can be
decided by the shape of the story. A long take is successive `reference` beats — sheets and
poses on every clip, previous clip held as `<Video 1>` — not a chain of keyframe hand-offs.

Anything with `title` and a `beats` array of `action` lines will import; everything else has a
default.

The import is checked but not fussy. Beats are renumbered from their array order, beat 1 is
forced onto a join that stands on its own — the default cut, unless the script asked for the
exact-keyframe one — lengths snap to 5 s or 10 s, and render settings (steps, seed) stay the
board's rather than the script's. What is *thin* — no style bible, a cut with no
`asset_prompt` — is reported as a note and imported anyway, since all of it is free to fix on
the canvas. An import never overwrites an existing reel; a second copy lands as `-2`.

### Your own stills, and switching generation off

An imported script has already decided its shots, so what is left is one image per cut — and
those often exist already, made somewhere else. The script node's **opening stills** block
fills them in one selection: pick or drop several images and they land on the beats that need
one, in filename order. Name a file `beat3.png` and it goes to that beat exactly, which is also
how you replace a still or put one on a beat that currently continues from the one before.
Uploading costs nothing, and each node's own ⤒ upload still works for fixing one.

The **my own** switch beside it turns image generation off for the reel: every ✦ generate
control disappears and the server refuses an asset job with a 409, so no stale tab or stray
request can overwrite a still you supplied yourself — and neither can the model, which is
refused by the same check from the same place. `I'll supply the stills` on the import panel
starts a reel that way, since otherwise
the first thing an imported script offers is a button that generates the stills it just
described. Both are reversible: switch back to **generated** and the controls return.

The board is a fixed chain — a script node, a row of sequence nodes, a reel node — so
there is no way to wire it wrong. Scenes can be inserted before or after any existing scene
and removed in place; the immediate neighbors reconnect automatically. Manual wiring stays
disabled, so a scene cannot branch, connect twice, point backward, or form a loop. The
**wire between two beats is the frame handoff**:

- **solid green** — this beat continues from the previous clip's last frame. Not a new shot
  at all: the same take carrying on, same set, same camera. Needs no still.
- **dashed amber, labelled `◈`** — a **reference** cut, and the default one: this beat opens
  on its own still, handed to `ref2va` as `<Picture 1>`, with the reel's cast reference
  alongside it as `<Picture 2>` for the whole clip. A clean cut to somewhere else, one local
  render of ~11–19 s. Room for seven more pictures of the cast, the set or a prop, and it can
  instead carry the previous clip in as a reference *video*.
- **solid green, labelled `→ still`** — a **bridge**: it continues from the previous clip
  *and* is given its own still as the frame it has to arrive at. One unbroken take that ends
  on a composition you chose. Needs a still, same as a cut.
- **dashed amber, labelled `✂`** — an **asset** cut: the same cut, except the still goes to
  `fl2va` as an exact keyframe and nothing else goes with it. The opening frame lands pixel
  for pixel; the cast is not re-asserted after it. The join for a beat whose first frame has
  to be exact, and the one every board written before the default moved still uses.

The bridge exists because H3 takes two keyframes, a first and a last, and the wire only ever
used the first. Click a beat's join line to walk the four. Use it when a continuous shot has
to reach a specific state — the lamp lit, the character back on its mark — or when a long run
of continuations has drifted and needs pinning back to a still: the model has to land on that
frame, so the drift is corrected inside the beat instead of accumulating past it. Its
**closing still** slot is the same upload and generate you already have, and its
`asset_prompt` describes the ending rather than the opening. Everything downstream still
follows it: a bridge takes its first frame from the clip before, so re-rendering upstream
invalidates it exactly like a plain continuation.

Each join is told to the model as what it is, which is most of what continuity comes down
to. A cut is instructed to open on its still and hold that framing; a continuation is
instructed that its first frame is a freeze from the middle of a take already in motion, to
be carried on from that exact pose without re-posing, re-centring or re-establishing
anything. Given the cut wording, a continuation reads a mid-stride pose as a starting pose,
settles the puppet back to rest and begins again — the jolt you see at a seam. A bridge gets
the continuation wording plus an instruction that the second still is where this same move
ends and must be reached only on the last frame — without that, the model treats it as another
shot to cut to, arrives early and then sits there.

### Staging — designing the cast and the sets before anything is rendered

Everything above holds one film together with two things: a **style bible**, which is one
paragraph for the whole reel, and a **cast reference**, which is one image. Both have a ceiling
you hit the same way. The paragraph is words, and the same sentence that produced a round-eared
pig in scene 1 produces a sharper-eared one in scene 4 — neither prompt was wrong. The image is
scene 1's own *still*: a composed shot, so its framing, its staging and its light are carried
into every still conditioned on it, and there is exactly one of it, so a second character has
nowhere to live and a recurring location is redrawn from prose in every shot that uses it.

**Staging** is the layer between them. Open it from 🎭 on the script node. A design is a named
thing — a `character`, an `environment` or a `prop` — with a sentence saying what it is, a
**design sheet** drawn once by Gemini, and a stable id. Scenes then **bind** the designs they
contain, and every scene bound to one is conditioned on the same picture and told the same
sentence.

The kind is not decoration; it decides three things at once:

|  | drawn as | drawn at | reaches the clip | reaches the still |
| --- | --- | --- | --- | --- |
| `character` | model sheet: turnaround, expressions, head, palette | 16:9 | a picture | a picture |
| `prop` | subject whole and centred, plain ground | 1:1 | a picture | a picture |
| `environment` | the place, empty of characters | 9:16 | a picture | **words** |

That last cell is the one measured constraint in the feature. The video model takes nine
pictures; the still renderer takes four, one of which is already the cast reference. Three
characters and a set do not fit, so the set is dropped from the still and arrives as a sentence
instead — characters are what drift visibly between shots, and a clearing redrawn from a *fixed*
sentence in every scene is survivable in a way a wolf that changes species is not. The rule is
uniform rather than special-cased: **whatever a render is not handed as a picture, it is told in
words**, which is also what makes writing the bible useful before a single sheet is drawn.

Bound designs sit between the automatic slots and the scene's own uploads in the numbering, so a
cut with two characters bound reads `<Picture 1>` opening still, `<Picture 2>` cast reference,
`<Picture 3>`…`<Picture 4>` the two designs, uploads from `<Picture 5>`. The node shows the same
numbers the model is given, and the upload budget shrinks to match — a picture the render would
truncate away is refused rather than stored.

Three things about how a sheet is *drawn* are the same lesson wearing different clothes, and all
three were measured one level down on reference pictures: **a model shown the cast draws the
cast.** So nothing conditions a first draw unless you name a sibling with `@stage:`; the board's
style bible never reaches the render (it describes the cast, and a prop sheet is not the cast);
and a redraw uses the image server's `edit` mode, the one conditioned mode with no "move the
subject into a different pose" clause. Every sheet has a conversation of its own — *"her chest
should be cream, not white"* — which rewrites the prompt and draws it again. There is no
automatic review, deliberately: the reviewer holds an image to the cast reference, and a design
sheet is *supposed* to differ from it.

Naming a design in prose is `@stage:<id>`, alongside `@ref:` and `@cast`. It carries the id
rather than a number for the same reason those do — the two prompt builders order their pictures
differently, and a number typed into prose is persisted derived state.

Free, all of it, apart from the Gemini requests that draw the sheets. Deleting a design takes its
sheet, every binding to it, and rewrites every `@stage:` naming it into what it was for.

### The storyboard — seeing the whole film before drawing any of it

Everything above is about pictures a render uses. A **panel** is the opposite: a rough grey sketch
of one shot, drawn to be looked at and nothing else. It conditions nothing, no model is ever handed
one, and drawing or deleting one changes no scene's state — which is what makes it safe to press on
a reel that is already rendered and paid for.

Two rows in the sidebar. **Write the shots** hands the whole script to the model in one turn
and gets back a line per scene the board has never held: shot size, angle, camera move, where the
subject sits in the frame, what the arrows point at. One turn for the reel rather than one per
scene, because shot sizes only mean anything judged against each other — a model shown one beat
cannot tell it has just written five wide shots in a row. Free. **Draw the panels** renders each
line on Nano Banana 2 Lite at 1K, the cheapest of the three, and stitches the results into
`reels/<slug>/storyboard_sheet.png` — three across, numbered, with each scene's length and join
under it. That sheet is the point of the feature: the film read at once, and a file you can send
someone.

The sketch is deliberately *not* paper cutout. A cheap version of the real medium is a bad preview
of that medium and reads as a finished still, and a storyboard is about framing rather than
texture. Nothing conditions a panel either, for the reason a prop sheet is drawn from words alone
one section up — a model shown the cast reference comes back with the cast, in the cast's medium.
The consequence is worth knowing rather than discovering: two panels of the same fox are two
readings of one sentence, so a panel is a record of *framing*, never of continuity.

Each node grows a panel row once the board has a storyboard: the sketch, the line it was drawn
from, ✦ to redraw and ✕ to throw away. The expanded scene view has the line as an editable field.
There is no conversation about a panel and no automatic review — there is nothing for a verdict to
be about.

### The reference join — nine pictures instead of a keyframe, and the default cut

A keyframe is one image. This join is a different checkpoint rather than a different wiring:
`ref2va` takes **up to 9 reference images** (plus 3 videos and 3 audio clips, which this
pipeline does not wire) and **no first or last frame at all**. The prompt tells the model about
them as `<Picture 1>`…`<Picture 9>` in exactly that order, so position is meaning.

**Two of those nine fill themselves,** which is what makes this the default cut rather than an
uploads-only special case. `<Picture 1>` is the beat's own still — generated from its
`asset_prompt` exactly as a keyframe cut's is, and named to the model as the composition this
shot opens on. `<Picture 2>` is the reel's locked cast reference. Consistency is the whole
argument: a keyframe fixes frame zero exactly and says nothing about the nine seconds after it,
where several references keep pulling the puppets back towards their design through every
sampling step. What it gives up is exactness — the opening is close rather than identical — and
render time, since reference tokens ride the whole sampling run where a keyframe is one VAE
encode. `asset` is still there for the beat that needs the frame itself.

Drop more pictures on a scene's reference tray and they upload in order, starting at
`<Picture 3>`; the node shows the numbers the prompt uses. Remove one and the rest renumber,
because the numbers are what the prompt refers to. The first few also condition the beat's
**still** — the node says how many, and "Where stills come from" above says why.

Each picture gets its own one-line **prompt** on the node, numbered to match: what the model
should take *from* that picture. It matters more than it sounds. Shown a picture with no
explanation, ref2va assumes the picture is the scene — hand it a still of the cast standing in
the finished set and it will reproduce that *and* act the beat out with a second copy of the
same puppet, which is two moths on one lamp post. Told `<Picture 1> is the same single Moth
that performs the action, not an extra one` and `<Picture 2> is the set only, no puppet`, it
collapses back to one. The notes go into the render prompt, so editing one marks the beat
stale, exactly like editing the action. `reel.py --ref-note` is the same thing from the CLI,
paired to `--ref` by position.

**Carrying the previous clip.** A reference beat has no keyframe slot, so continuity cannot be
a frame handoff — but the node takes reference *video* (3 clips, 2–15 s each), and the tick box
on the node puts the **last 3 seconds of the previous clip** in as `<Video 1>`. The prompt then
swaps "compose the opening frame yourself" for "open on the moment `<Video 1>` ends and carry
its movement onward, same set, same camera, no restart". Only the tail is sent: reference
tokens ride through every sampling step, so a whole 10 s clip would cost ~9× this for motion
that stopped mattering seconds ago. Turning it on makes the scene depend on the one before it
again — re-rendering upstream marks it `follows a change`, and it re-enters the render
cascade. `config.REF_VIDEO_SECONDS` is the tail length; `config.REF_VIDEO_WITH_AUDIO` (off)
also pairs the clip's soundtrack into the matching audio slot.

What it costs is the handoff. A reference beat cannot land on a still, and it cannot take a
frame-exact continuation — its version of one is "here is where the take had got to". Render
time grows with the number of pictures, too, since reference tokens ride through every sampling
step rather than being encoded once like a keyframe, so the render-time predictions in
`config.py` — fitted on the keyframe path — read slightly low for a cut on this join.

The one shape that still cannot be generated into is a beat **carrying** the previous clip: it
already has an opening, and the prompt may only ever give the model one answer to where the shot
begins, so ✦ generate is gone from the node and the server refuses an asset job for it with a
409. Every other reference beat generates its still like any other cut.

Both checkpoints live on the Volume (19.5 GiB each), and ComfyUI loads whichever the queued beat
names, so a reel that mixes cuts with continuations now pays one model swap per shot boundary —
rendering in beat order, which the batch already does for chaining's sake, keeps it to that.
`reel.py --ref cast.png --ref set.png` is the same checkpoint from the CLI, with no still wired:
there, every `--ref` is a design reference and the model composes the opening itself.

The other half is that both kinds hold the same cast. The **cast reference** on the script
node is one image that every generated still is conditioned on, so a cut changes the setting
rather than the characters; the style bible goes into the video prompt as well, so a beat
that drifts mid-clip drifts toward that description rather than toward the model's own idea
of a paper fox. Beat 1's still is the reference by default — pin your own to override it.

A beat's video prompt is **the style bible, then its scene line, then its action**: what the
production looks like, where this shot is, and what moves. The scene line is an input to the
render, not a note to yourself — editing it on the node marks the beat stale, exactly like
editing the action. Without it the model has only one still to infer the setting from, which
is how a background quietly becomes a different place halfway through a clip.

Every beat has its own persistent **upload** and **generate** controls, so all scene assets
can be prepared before any video rendering starts. Dragging an image onto a frame works too,
and the script node fills a whole reel's stills from one multi-file selection. Uploading needs
no image server — and generation can be
switched off entirely, per reel. Supplying or generating a still makes
that beat a new shot, so its wire switches to a cut — unless it is already a bridge, where the
still is the frame it lands on and the continuation is left alone. Anything far from 9:16 gets
a crop warning before you pay to discover it.

A take you don't like is discardable: **× clip** on the rendered-output header, twice (it
disarms after four seconds). The beat drops back to `ready`, everything chained below it reads
as following a change, and the render button re-prices itself to include them. The file is
moved to the reel's `.discarded/` rather than deleted — renders are the only thing here that
costs money, so a mis-click should not be final. The beat's own still and its reference
pictures are left alone.

A beat is either **5 s or 10 s** — two buttons, no stepper (see the measured numbers for
why). Editing a beat marks it `edited` and everything chained below it `follows a change`,
and the render button re-prices itself. It renders **only what's dirty**, so fixing one beat
does not re-pay for the ones already on disk. Clips attach to their nodes as each beat
finishes, so beat 1 is watchable while beat 4 is still sampling.

The model can rewrite, re-time, reorder, add and remove beats, ask the image server for the
stills a board is missing, and write the caption — all free. A turn is a tool loop, so it can
edit a beat, read the board back to see what that did, and then ask for the stills that change
created, all inside one turn, with each step in the job log. It cannot render. Spending money
stays a human action.

The server runs locally because it holds every credential — the Google API key the language
model and the image server both go through, and the Modal proxy tokens; the browser never talks
to either, so nothing secret leaves this machine.

For frontend development, run `npm run dev` in `studio/` alongside `uv run studio.py` and
use the Vite URL — it proxies the API through.

## Make a reel from the CLI

```bash
# 1. plan — free
uv run storyboard.py --concept "a paper pig finds a hidden pond" --beats 4 --seconds 10
uv run storyboard.py --script story.json        # or adopt your own, no planner turn

# 2. storyboard — free; a rough sketch per shot on the cheapest model, plus a contact sheet
uv run storyboard.py --name <slug> --panels

# 3. opening stills — free; needs `make images` running, and reviews what it renders
uv run storyboard.py --name <slug> --assets

# 4. render — the only paid stage; deploys, renders, stitches, stops
uv run storyboard.py --name <slug> --render
```

Everything lands in `reels/<slug>/`. `storyboard.json` is editable between stages —
rewrite a beat's action or drop in your own `beat1_asset.png` and re-run; completed
work is skipped.

Add `--draft` for a cheap 5 s-per-beat approval pass before committing.

`--panels` is the stage you stop at and look at, which is why it is deliberately not part of
`--all`. It writes the shot grammar for any scene that has none — shot size, angle, camera move,
where the subject sits — then draws each one as a rough grey sketch and stitches them into
`reels/<slug>/storyboard_sheet.png`. Nothing it makes is rendered from: a panel is not a still, it
conditions nothing, and it changes no beat's state. Re-running skips what is drawn.

`--assets` on a board that names a source per beat — an imported script, or one built in the
studio — generates exactly the stills it needs, which is every beat but a plain continuation.
`--render` honours those joins too, but it applies one `--seconds` to the whole reel, so a script
mixing 5 s and 10 s beats renders as written only in the studio. The CLI says so when it adopts
one. `--scenes` is the deliberate override: every beat opens on its own still, whatever the board
says — as the default cut, unless the board asked for the exact-keyframe one.

For a single clip, `reel.py` composes a frame from a separate background and character:

```bash
uv run reel.py --preview                       # compose only, no GPU
uv run reel.py --prompt "the pig walks right" --seconds 10
uv run reel.py --ref cast.png --ref set.png    # reference mode, up to 9, no keyframe
```

## Measured numbers

All on RTX PRO 6000, quantized weights, 768×1344, 8 steps, chained beats.
BF16 on B200 has not been timed; the table is the old card.

| Beats × length | Container-sec | Cost | $/s of video | Status |
| --- | --- | --- | --- | --- |
| 4 × 5.2 s | 382 | $0.42 | 0.024 | proven |
| 4 × 10.1 s | 1036 | $1.13 | 0.028 | proven |
| 6 × 10.1 s (60 s) | ~1550 | ~$1.74 | 0.028 | extrapolated |
| 4 × 15.1 s | — | — | ~0.035 | failed — 362 frames never completed |

Render time grows **faster than linearly** with frame count (video attention is
quadratic in sequence length): 1.96× the frames cost 2.35× the time. Longer clips buy
fewer seams, not lower cost.

That last row is why **a beat is either 5 s or 10 s and nothing else**. 5 s is the model's
124-frame floor; 10 s snaps to 243 frames, which is exactly the longest render that has ever
completed on this card. The failing length is unreachable by construction rather than by
warning, and there is no third option to reason about.

Other measurements worth keeping:

- **Steps:** 8 vs 20 saves ~41% (115 s vs 196 s at 124 frames). 8 was judged good on
  flat paper art.
- **Model load:** ~22 s, paid **once per container**. Rendering N beats in one warm
  container instead of N separate runs is the single biggest structural saving.
- **B200 is required** for unpruned BF16 (~115 GB resident). The 1.19× / 2.06×
  comparison was the quantized job on this card against the RTX PRO 6000, and is
  why the cheap card was chosen then. It does not apply to this stack. Wall-clock
  on BF16/B200 is unmeasured. Escape hatch is B300 (288 GB), not 4×H200.
- **Parallelism does not save money.** Billing is per container-second, so 4 containers
  for T costs the same as 1 for 4T — and *more*, since each container repays the model
  load. Chaining is serial by construction anyway. Parallelism is a latency tool, not a
  cost tool.

### Gemini, for the words and the pictures

One model does everything that is words: `gemini-3.7-flash` over Google's API, with vision,
tool calling and a thinking level, which is what lets a single model write the script, drive
the board through tool calls and look at the stills. The stills come from Gemini Nano Banana
next door, through the same endpoint and the same credential — set `X-GOOG-API-KEY` in `.env`
once and both work. Prompts and reference images go to Google; generated files, boards and
scene history stay on disk.

Two things came before it. The Antigravity CLI, whose image tool allowed roughly **five
generations per five-hour window** with agent turns out of the same plan quota — the single
limit that shaped most of the original design, and why chaining is the default and a reel was
built to need one image rather than one per beat. Then `qwen3.6` under Ollama, which removed
the meter entirely at the price of 23 GiB of weights, a service to keep running and a draft
that took minutes. The API is the middle: nothing to install, and a turn priced in cents.

What that buys, concretely, is two passes worth keeping: the script marks its own work against
the brief's 22-point self-check, and every still is looked at next to the cast reference before
the board accepts it. Neither was worth a quota slot under `agy`. Both are worth a flash turn,
which costs a fraction of one of the images this pipeline already spends without hesitating.

Knobs, all with `PAPERREEL_` prefixes: `TEXT_MODEL`, `VISION_MODEL`, `GEMINI_API_URL`,
`LLM_TEMPERATURE`, `LLM_TIMEOUT`, `LLM_IMAGE_EDGE` (1024 — a picture is shrunk on the way to
the model, which resamples it anyway), `PLAN_THINK`, `PLAN_REVIEW`, `STILL_REVIEW`,
`STILL_ATTEMPTS`.

Reasoning is `low` everywhere except the planning pair. (`minimal` 400s on gemini-3.7-flash.)
Thought tokens are billed as output and an unambiguous board edit needs none of them; writing a
script is the one place the quality is worth both the tokens and the wall clock.

## Layout

```
paperreel/config.py     geometry, rates, prompt scaffold, measured constants
paperreel/media.py      chroma-key cutout, compositing, stitching  (local, free)
paperreel/comfy.py      ComfyUI client + the 15-node H3 graph
paperreel/gemini.py     Gemini: structured output, tool calls, vision
paperreel/papercut.py   stills from image/, over HTTP on this machine
paperreel/stills.py     rendering stills, then looking at them
paperreel/pictures.py   a beat's reference pictures, drawn as well as uploaded
paperreel/staging.py    the reel's cast and sets, designed once and bound to scenes
paperreel/panels.py     the storyboard: a rough sketch per shot, and the contact sheet
paperreel/planner.py    the authoring brief -> a script, then its own self-check
paperreel/script.py     adopting a script written outside the studio
paperreel/pipeline.py   app lifecycle + chained batch rendering
paperreel/board.py      the board document; state derived from disk
paperreel/agent.py      the tool loop -> board operations
paperreel/jobs.py       one worker, one container, one event stream
paperreel/render.py     rendering with per-beat telemetry
paperreel/api.py        HTTP + SSE for the studio
comfyui_minimax_h3.py   the Modal GPU app
studio.py               the studio server
studio/                 React + TypeScript + React Flow canvas
prompts/                the authoring prompt for writing a script elsewhere
image/                  Papercut Studio: the Gemini Nano Banana stills renderer (own README)
Makefile                install, run, and the one-time Modal steps
storyboard.py           CLI: full reel
reel.py                 CLI: single clip
minimax_h3.py           alternative: full-precision BF16 on 4×H200 via SGLang
```

`reels/<slug>/storyboard.json` is the only database, and beat state is *derived* from what
is on disk rather than stored. Hand-edit the JSON, drop in your own PNG, or run
`storyboard.py`, and the canvas reflects it — the CLI and the studio cannot drift apart.

`minimax_h3.py` is the faster 4×H200 SGLang path. It speaks `/v1/videos`; the pipeline
above talks ComfyUI, so it is unused.

## Known gaps

- **The crew has reached a live Gemini once, and it was a 429.** `--stage storyboard` on a
  scripted fixture (2026-08-17) failed every specialist with prepay credits depleted. The skip
  path then stamped the whole storyboard `done` with no roster or panels written. That cursor
  lie is closed: a failed phase is reopened, and `make harness` asserts it with a stub. A
  successful live cast still needs credits. `make harness` itself calls no model.
- **The CLI can still ask for 15 s beats**, which have failed once on this card. The studio
  cannot — `config.BEAT_LENGTHS` caps it at 243 frames — but `storyboard.py --seconds 15`
  bypasses that and only logs a warning.
- **Two of the four stage pages have never been seen in a browser.** The shell is exercised —
  every stage URL is served from the built bundle, a bad slug still 404s, and one whole
  interview ran against a live model: the four questions, then *defaults*, then a six-beat
  script with mixed lengths that survived the self-check, on the same reel it started on.
  Nobody has clicked the Storyboard grid's binding gesture or the Assets stage's "drawn from"
  strip, so both are reasoned rather than seen. Every existing board's state is unchanged by
  all of it — the fully-rendered reel still reads `rendered` on all five beats.
- **An abandoned conversation leaves a reel behind.** Starting one puts the board on disk
  before there is a script, which is what makes the interview survive a reload; a reel you
  never finished shows in the rail as `draft` and is deleted from the same place as any other.
- **Staging has never been drawn against a live model.** The server side is exercised end to
  end: entries mint, bind, renumber the pictures below them, reach the clip as `<Picture N>` and
  the still as words, and deleting one takes its sheet, its bindings and rewrites every
  `@stage:` naming it into what it was for. The fingerprints are verified to stay *byte
  identical* on a board that binds nothing, which is what stops this marking every rendered beat
  in every existing reel stale. What has not happened is a single Gemini request: nobody has
  drawn a character sheet, redrawn one from a note, or looked at whether a scene conditioned on
  two design sheets holds its cast better than one conditioned on the cast reference alone —
  which is the entire claim the feature makes. The set-sheet suffix and its 9:16 shape are
  reasoned from the prop-sheet failure one level down, not measured. The character model sheet
  (16:9, four labeled sections in one Gemini shot) is the same kind of claim: one-shot Gemini
  may drop a section or smear labels; the review retry is the mitigation; no quality claim
  until a live draw.
- **No storyboard panel has been drawn against a live model either.** The machinery is exercised:
  the routes answer, both job kinds run, a panel follows its scene through a delete-and-renumber,
  the contact sheet builds, and every beat's fingerprint is verified *byte identical* before and
  after a panel appears — so this cannot mark a rendered reel stale. What nobody has seen is a
  sketch. Three claims are therefore reasoned, not measured: that the panel style actually comes
  back as grey pencil rather than the paper cutout it explicitly negates, that Lite at 1K is
  legible enough to judge framing by, and that a sketch is a useful read of a shot that will be
  made of paper. Whether the model varies its shot sizes across a reel rather than writing
  five medium shots in a row is the fourth, and the contact sheet is exactly where that shows.
- **A different photo of the same design still goes through.** Pixel-identical uploads of a
  bound sheet are refused — bind it, don't copy it. A second drawing of the wolf is still two
  pictures of one puppet; only the bytes were compared.
- **Cross-scene consistency is improved but not measured.** Independent scenes no longer
  rely on the `style_bible` text alone — every still is generated conditioned on the cast
  reference image, the bible is in the video prompt too, and the model now looks at each
  finished still and rejects one whose cast has drifted. That last check is verified to fire
  correctly on an obvious mismatch and to pass a good still, but its rate of false accepts on
  *subtle* drift over a long reel of hard cuts has not been quantified.
- **The default cut moved to `ref2va` and nothing has been measured on it.** The argument is
  sound — a keyframe fixes frame zero and says nothing about the nine seconds after it, where
  the cast reference riding through every sampling step keeps asserting the design — but no
  A/B render exists. Three specific unknowns: how much *less* exact the opening frame is when
  the still is conditioning rather than a keyframe latent; how much slower a two-picture cut
  is, since `SECONDS_PER_FRAME` is fitted on the keyframe path and will read low; and whether
  the checkpoint swap at every shot boundary costs enough container time to matter. `asset` is
  the same cut on the old path, so an A/B is one join click and two renders.
- **A still drawn from several pictures is timed but not judged.** The plumbing is exercised end
  to end — the paths go over, the notes reach the prompt, the cap is honoured from both sides, a
  beat moved off the reference join is ignored — and a two-reference frame renders in 31.4 s
  against 18.6 s for one, same prompt and seed. What nobody has *looked at* is whether it is
  better: whether four pictures hold the cast harder than one or average them into something
  blander, and whether the *reference images show: …* clause helps or gives Gemini one
  more thing to draw into the frame. `PAPERREEL_MAX_STILL_REFS` is how to explore it, and it
  costs nothing but wall clock.
- **The still review is shown the cast reference, not the beat's uploads.** So a still that
  correctly follows a prop reference is judged against the reel's cast alone. That is the right
  question for the review to ask, but it means an upload cannot defend a still the reviewer
  wants to reject.
- **The review passes are not deterministic.** The script self-check and the still review are
  the same model reading its own output, so two runs of the same board can disagree about
  whether something needs fixing. The still review is pinned to temperature 0.1 to keep that
  narrow, and both are capped — one still retry, one script pass — so a swing costs wall clock
  and never loops. Turn either off with `PAPERREEL_STILL_REVIEW=0` / `PAPERREEL_PLAN_REVIEW=0`.
- **The per-still conversation is unmeasured against a live model.** Its plumbing is exercised —
  the prompt is rewritten, the still is redrawn, a retry moves the seed, an image server that is
  down leaves the rewrite saved rather than failing the turn — but two things are only as good as
  the model on the day: whether it sets `regenerate` correctly (a question about the picture that
  redraws it anyway costs 10–18 s of nothing), and whether it carries the untouched half of a
  prompt through a rewrite instead of paraphrasing the style bible. Both are visible in the
  transcript on the node.
- **The expanded scene view has not been driven in a browser.** Its server side has: a note with
  a picture attached stores the picture, moves the join, redraws the still and writes both turns
  into the transcript, and a revise of a scene and of an action both came back with the line
  rewritten and a sentence about it. What has not been exercised is the view itself — picking
  between assets, the modal following a beat that changes underneath it, Esc while a job is
  running.
- **`revise` is one model call with no self-check.** The script self-check reads a whole draft
  against the brief; this reads one line and the two beside it, and nothing looks at what comes
  back. It is free and it is undo-able by typing, but a rewrite that quietly drops half the
  movement is caught by you, not by the studio.
- **Planning is still not interactive.** Draft plus self-check measured **239 s** on
  `gemini-3.6-flash` with thinking on for both passes — faster than the local 36B model it
  replaced, and still a job you watch rather than a prompt you wait on.
- **Turn latency is unexplained.** Every request in the environment these numbers were taken
  in came back in roughly **81 s** regardless of payload — a 1 KB prompt, a 100 KB prompt and
  two 1.5 MB stills all measured the same, and the same calls through `curl` were seconds. So
  the flat minute is something about this machine's network path, not about the model, and
  none of the per-call timings here should be read as the API's speed.
- **Container may be over-provisioned** at 8 cores / 128 GiB — host RAM is sized
  to stage the BF16 files, and was never measured against actual usage.
- **The studio's per-step progress is unverified against a live render.** ComfyUI's `/ws`
  through Modal's auth proxy has not been exercised yet; if it fails, the phase strip and
  per-beat timing still work from `/history` polling, only the `step 5/8` detail is lost.
- **Cost readouts are estimates**, derived from wall clock × `config.RATE_PER_SEC`
  (B200 + 8 cores + 128 GiB, published as `/api/status.rate_per_second`) rather than
  Modal's billing API, and they exclude the scale-down tail. Wall-clock is still the
  old quantized fit, so quotes read low.

## License

Review the MiniMax-H3 model card license before commercial use. Serving it on Modal
doesn't grant usage rights.

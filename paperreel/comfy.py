"""Client for the ComfyUI server running on Modal, plus the MiniMax-H3 graph.

The graph is built here as a plain dict rather than loaded from a saved UI workflow,
so every knob that affects cost or quality is visible in one function.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

from . import config


def modal_auth_headers() -> dict[str, str]:
    """Modal proxy-auth headers, so the endpoint need not be public.

    These are PROXY auth tokens (`wk-...` / `ws-...`), a different credential from the
    CLI token (`ak-...`) in ~/.modal.toml -- passing the CLI token here earns a 401.
    Proxy tokens can only be minted in the dashboard:

        https://modal.com/settings/proxy-auth-tokens

    Then export them:

        export MODAL_PROXY_TOKEN_ID=wk-...
        export MODAL_PROXY_TOKEN_SECRET=ws-...

    Returns {} when unset, which is correct against a PAPERREEL_PUBLIC=1 deployment.
    """
    key = os.environ.get("MODAL_PROXY_TOKEN_ID")
    secret = os.environ.get("MODAL_PROXY_TOKEN_SECRET")
    if not (key and secret):
        return {}
    if not key.startswith("wk-"):
        raise SystemExit(
            f"MODAL_PROXY_TOKEN_ID should start with 'wk-', got '{key[:8]}...'. "
            "The CLI token from ~/.modal.toml is not a proxy-auth token; mint one at "
            "https://modal.com/settings/proxy-auth-tokens"
        )
    return {"Modal-Key": key, "Modal-Secret": secret}


def client(url: str | None = None, *, timeout: float = 300.0) -> httpx.Client:
    return httpx.Client(
        base_url=url or config.BACKEND_URL,
        timeout=httpx.Timeout(timeout, connect=30.0),
        headers=modal_auth_headers(),
    )


CLIENT_ID = "paper-reel"


def progress_listener(on_progress, *, log=print, closers=None, stop_event=None) -> None:
    """Republish ComfyUI's per-step sampling progress. Runs in a thread; never raises.

    ComfyUI pushes {"type": "progress", "data": {"value": n, "max": m}} over /ws for the
    client_id that queued the prompt, which is where the UI's "step 5/8" comes from.

    This is best-effort by design. A WebSocket through Modal's auth proxy is the one
    unproven link in the chain, so a failure here degrades to the per-beat timing that
    /history polling already provides rather than taking the render down with it.

    `stop_event` is what makes it stop: reconnecting forever against a container that has
    been torn down would spam the log and outlive the render that started it. `closers`
    collects the socket's close callable so the caller can interrupt a blocking connect --
    checked as a list, since the caller may reach the teardown before this thread has even
    constructed the socket.
    """
    try:
        from websocket import WebSocketApp  # websocket-client
    except ImportError:
        log("[ws] websocket-client not installed; per-step progress disabled")
        return

    url = config.BACKEND_URL.replace("https://", "wss://").replace("http://", "ws://")
    headers = [f"{key}: {value}" for key, value in modal_auth_headers().items()]

    def on_message(_ws, raw):
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        data = message.get("data") or {}
        if message.get("type") == "progress" and data.get("max"):
            on_progress(int(data.get("value", 0)), int(data["max"]))

    stopping = lambda: stop_event is not None and stop_event.is_set()  # noqa: E731
    complained = False

    def on_error(_ws, error):
        nonlocal complained
        if not (stopping() or complained):
            complained = True  # once, not once per reconnect
            log(f"[ws] progress stream unavailable ({error}); falling back to timing")

    socket = WebSocketApp(
        f"{url}/ws?clientId={CLIENT_ID}",
        header=headers,
        on_message=on_message,
        on_error=on_error,
    )
    if closers is not None:
        closers.append(socket.close)
    try:
        # run_forever returns on close or error; loop so a blip reconnects, but only while
        # the render that wants us is still going.
        while not stopping():
            socket.run_forever()
            if stopping():
                break
            time.sleep(2)
    except Exception as error:  # a dead socket must never kill a paid render
        log(f"[ws] progress listener stopped ({error})")


def build_graph(*, first_frame: str | None, prompt: str, length: int,
                steps: int, seed: int, last_frame: str | None = None,
                filename_prefix: str = "video/reel") -> dict:
    """The 15-node H3 image-to-video graph in ComfyUI API format.

    `first_frame` is optional at the node level: omitting it makes this pure
    text-to-video, which is the escape hatch when no source art is available.
    """
    h3_inputs: dict = {
        "clip": ["3", 0],
        "vae": ["4", 0],
        "prompt": prompt,
        "width": config.GEN_WIDTH,
        "height": config.GEN_HEIGHT,
        "length": length,
    }
    graph: dict = {
        "2": {"class_type": "UNETLoader",
              "inputs": {"unet_name": config.UNET, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": config.CLIP, "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": config.VIDEO_VAE}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": config.AUDIO_VAE}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": h3_inputs},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler",
              "inputs": {"model": ["2", 0], "scheduler": "simple",
                         "steps": steps, "denoise": 1.0}},
        "10": {"class_type": "BasicGuider",
               "inputs": {"model": ["2", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0],
                          "sigmas": ["9", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "13": {"class_type": "VAEDecodeAudio",
               "inputs": {"samples": ["11", 0], "vae": ["5", 0]}},
        "14": {"class_type": "CreateVideo",
               "inputs": {"images": ["12", 0], "audio": ["13", 0],
                          "fps": float(config.FPS), "bit_depth": 8}},
        "15": {"class_type": "SaveVideo",
               "inputs": {"video": ["14", 0], "filename_prefix": filename_prefix,
                          "format": "auto", "codec": "auto"}},
    }
    if first_frame:
        graph["1"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        h3_inputs["first_frame"] = ["1", 0]
    if last_frame:
        graph["16"] = {"class_type": "LoadImage", "inputs": {"image": last_frame}}
        h3_inputs["last_frame"] = ["16", 0]
    return graph


def wake(http: httpx.Client, *, timeout: float = 15 * 60, log=print) -> None:
    """Wait out a cold start. Weights come off a Modal Volume, so this is ~10-30s."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = http.get("/system_stats")
            if response.is_success:
                return
            if response.status_code in (401, 403):
                have = "set" if modal_auth_headers() else "NOT set"
                raise SystemExit(
                    f"ComfyUI refused us (HTTP {response.status_code}). The deployment "
                    f"requires Modal proxy auth and MODAL_PROXY_TOKEN_ID/SECRET are {have}.\n"
                    "  Mint a token at https://modal.com/settings/proxy-auth-tokens and "
                    "export it,\n  or redeploy for local use with:\n"
                    "    PAPERREEL_PUBLIC=1 uvx modal deploy comfyui_minimax_h3.py"
                )
        except httpx.HTTPError:
            pass
        log("  server cold, waiting...")
        time.sleep(10)
    raise SystemExit(f"ComfyUI did not come up within {timeout / 60:.0f} minutes")


def upload_image(http: httpx.Client, path: Path, *, subfolder: str = "reel") -> str:
    """Upload a frame. The subfolder must be its own form field -- a '/' inside the
    filename makes ComfyUI's upload handler return a 500."""
    name = f"{uuid.uuid4().hex}{path.suffix.lower()}"
    response = http.post(
        "/upload/image",
        files={"image": (name, path.read_bytes(), "image/png")},
        data={"type": "input", "overwrite": "true", "subfolder": subfolder},
    )
    response.raise_for_status()
    body = response.json()
    return f"{body.get('subfolder', '')}/{body['name']}".lstrip("/")


class Cancelled(RuntimeError):
    """The caller asked us to stop mid-render."""


def interrupt(http: httpx.Client) -> None:
    """Stop the running graph. Called before teardown so no half-written file lands."""
    try:
        http.post("/interrupt")
    except httpx.HTTPError:
        pass  # we are tearing the container down regardless


def run_graph(http: httpx.Client, graph: dict, *, poll: float = 5.0, log=print,
              should_stop=None) -> list[dict]:
    """Queue a graph and block until it produces outputs."""
    response = http.post("/prompt", json={"client_id": CLIENT_ID, "prompt": graph})
    if response.is_error:
        raise SystemExit(f"ComfyUI rejected the graph:\n{response.text}")
    prompt_id = response.json()["prompt_id"]
    log(f"  queued {prompt_id}")

    started = time.monotonic()
    strikes = 0
    while True:
        time.sleep(poll)
        if should_stop is not None and should_stop():
            interrupt(http)
            raise Cancelled(f"cancelled after {int(time.monotonic() - started)}s")
        # A dead container answers with an HTML error page, not JSON. Tolerate a few
        # in a row (a restart is survivable) but don't spin forever on a corpse.
        try:
            record = http.get(f"/history/{prompt_id}").json().get(prompt_id)
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            strikes += 1
            if strikes >= 12:
                raise SystemExit(
                    f"Lost the ComfyUI server after {int(time.monotonic() - started)}s "
                    f"({error}). Check `uvx modal app logs {config.APP_NAME}` -- a long "
                    "render can exhaust GPU memory."
                )
            log(f"  server unreachable ({strikes}/12), retrying...")
            continue
        strikes = 0

        if not record:
            log(f"  rendering... {int(time.monotonic() - started)}s")
            continue
        status = record.get("status", {})
        if status.get("status_str") == "error":
            messages = status.get("messages", [])
            detail = next((m[1] for m in reversed(messages) if m[0] == "execution_error"), {})
            raise SystemExit(
                "Generation failed: " + detail.get("exception_message", json.dumps(status))
            )
        if status.get("completed"):
            log(f"  rendered in {int(time.monotonic() - started)}s")
            files: list[dict] = []
            for output in record.get("outputs", {}).values():
                for value in output.values():
                    if isinstance(value, list):
                        files += [v for v in value if isinstance(v, dict) and v.get("filename")]
            return files


def download(http: httpx.Client, item: dict, out_path: Path) -> Path:
    query = (
        f"filename={quote(item['filename'])}"
        f"&subfolder={quote(item.get('subfolder', ''))}"
        f"&type={quote(item.get('type', 'output'))}"
    )
    with http.stream("GET", f"/view?{query}") as response:
        response.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    return out_path


def only_video(outputs: list[dict]) -> dict:
    videos = [o for o in outputs if o["filename"].lower().endswith((".mp4", ".webm"))]
    if not videos:
        raise SystemExit(f"no video in outputs: {outputs}")
    return videos[0]

"""Paper-cutout Reels: concept -> script -> stills -> chained clips -> 1080x1920 MP4.

Stages are separable on purpose. Everything up to the video is local and unmetered -- the
script and the board edits come from a model on Ollama (`qwen.py`), the opening stills from
Papercut Studio next door (`papercut.py`, `stills.py`) -- and only rendering touches a GPU.
Iterate on the script for free, then pay once.

    from paperreel import config, planner, pipeline

    board = planner.plan("a paper pig finds a pond", beats=4, seconds=10)
    result = pipeline.render_reel(board, workdir, seconds=10)
    print(result.reel, result.cost)
"""

from . import comfy, config, media, pipeline, planner, qwen, script, stills

__all__ = ["comfy", "config", "media", "pipeline", "planner", "qwen", "script", "stills"]

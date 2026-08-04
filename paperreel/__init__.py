"""Paper-cutout Reels: concept -> script -> assets -> chained clips -> 1080x1920 MP4.

Stages are separable on purpose. Planning and asset generation run through the
Antigravity CLI against your Google plan quota and cost no money; only rendering
touches a GPU. Iterate on the script for free, then pay once.

    from paperreel import config, planner, pipeline

    board = planner.plan("a paper pig finds a pond", beats=4, seconds=10, workdir=d)
    result = pipeline.render_reel(board, d, seconds=10)
    print(result.reel, result.cost)
"""

from . import comfy, config, media, pipeline, planner

__all__ = ["comfy", "config", "media", "pipeline", "planner"]

"""The tribute_render worker: consume the tribute_render queue, render the
MP4 + PDF (Gemini illustrations -> templated pages -> video), upload via the
Node-minted presigned URLs, flip status + NOTIFY tribute_render_complete.
"""

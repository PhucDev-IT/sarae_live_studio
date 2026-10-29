"""AI Timeline Analyzer: suggests product pin markers for a Single Video session's main video, before LIVE.

FFmpeg (metadata, audio) → transcript (Faster-Whisper, Vietnamese) → word timestamps (Whisper words or WhisperX)
→ LLM segments the transcript by product, choosing only from the owner's real catalog → lexical check + optional
vision check of uncertain segments → suggestions with evidence. A person reviews them in the Timeline Editor before any
version can be activated; nothing is ever applied automatically. See docs/SINGLE_VIDEO_TIMELINE.md.
"""

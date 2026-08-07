from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class SubtitleCue:
    index: int
    start_time: float
    end_time: float
    text: str


def _format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    if milliseconds >= 1000:
        secs += 1
        milliseconds -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_cues(start_times: List[float], end_times: List[float], texts: List[str]) -> List[SubtitleCue]:
    """Build subtitle cues from parallel lists, skipping empty/blank text."""
    cues: List[SubtitleCue] = []
    n = min(len(start_times), len(end_times), len(texts))
    index = 1
    for i in range(n):
        text = (texts[i] or "").strip()
        if not text:
            continue
        start_t = float(start_times[i])
        end_t = float(end_times[i])
        if end_t <= start_t:
            end_t = start_t + 0.5
        cues.append(SubtitleCue(index=index, start_time=start_t, end_time=end_t, text=text))
        index += 1
    return cues


def write_srt(cues: List[SubtitleCue], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for cue in cues:
        lines.append(str(cue.index))
        lines.append(f"{_format_srt_timestamp(cue.start_time)} --> {_format_srt_timestamp(cue.end_time)}")
        lines.append(cue.text)
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")

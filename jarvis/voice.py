"""귀와 입 — 로컬 STT(faster-whisper)·TTS(Supertonic). 모델은 처음 쓸 때 로드한다."""
import io
import os
import threading

import soundfile as sf

_lock = threading.Lock()
_stt = None
_tts = None


def transcribe(audio: bytes) -> str:
    """webm/wav 등 오디오 바이트 → 한국어 텍스트."""
    global _stt
    if not audio:
        return ""
    with _lock:
        if _stt is None:
            from faster_whisper import WhisperModel
            dev = os.environ.get("JARVIS_STT_DEVICE", "cuda")
            _stt = WhisperModel("large-v3-turbo", device=dev,
                                compute_type="int8_float16" if dev == "cuda" else "int8")
        segs, _ = _stt.transcribe(io.BytesIO(audio), language="ko", vad_filter=True)
        return "".join(s.text for s in segs).strip()


def speak(text: str) -> bytes:
    """한국어 텍스트 → wav 바이트."""
    global _tts
    with _lock:
        if _tts is None:
            import supertonic
            tts = supertonic.TTS()
            _tts = (tts, tts.get_voice_style(os.environ.get("JARVIS_VOICE", "M1")))
        tts, style = _tts
        wav, _ = tts.synthesize(text, voice_style=style, lang="ko")
    buf = io.BytesIO()
    sf.write(buf, wav[0], tts.sample_rate, format="WAV")
    return buf.getvalue()

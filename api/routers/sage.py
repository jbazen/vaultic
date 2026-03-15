import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from api.dependencies import get_current_user
from api.sage import chat
from api import security_log, rate_limit

logger = logging.getLogger("vaultic.sage")
router = APIRouter(prefix="/api/sage", tags=["sage"])


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class ChatResponse(BaseModel):
    response: str
    history: list[dict]


class SpeakRequest(BaseModel):
    text: str


@router.post("/chat", response_model=ChatResponse)
async def sage_chat(body: ChatRequest, _user: str = Depends(get_current_user)):
    limited, remaining = rate_limit.check_sage(_user)
    if limited:
        security_log.log_server_event(f"SAGE_RATE_LIMITED  user={_user}")
        raise HTTPException(status_code=429, detail="Sage message limit reached (60/hour). Try again later.")
    rate_limit.record_sage(_user)
    security_log.log_sage_query(_user, body.message)
    try:
        response, updated_history = chat(body.history, body.message)
        return ChatResponse(response=response, history=updated_history)
    except Exception as e:
        logger.error(f"Sage chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speak")
async def sage_speak(body: SpeakRequest, _user: str = Depends(get_current_user)):
    """
    Convert text to speech using OpenAI TTS. Returns a streaming MP3.

    Voice choice — "ballad":
    The "ballad" voice is one of OpenAI's expressive, emotionally-aware voices
    (introduced with the gpt-4o-audio family). It sounds more natural and
    conversational than the older "onyx"/"alloy" voices — fitting for a personal
    financial advisor persona. Requires a paid OpenAI account with credits at
    platform.openai.com/billing (not covered by a ChatGPT subscription).

    Streaming approach — with_streaming_response vs .content:
    - `.content` would download the entire MP3 into memory before returning anything
      to the browser. For long Sage responses this adds noticeable latency.
    - `with_streaming_response.create(...)` opens an HTTP streaming connection to
      OpenAI and pipes bytes through to the browser as they arrive. The frontend
      can start playback before the full audio is downloaded.
    - The context manager (with ... as response) is required to keep the upstream
      connection open while we iterate; it closes cleanly when the generator exits.
    - The frontend fires one request per sentence (see SageChat.jsx speak()), so
      each individual audio chunk is short — but streaming still reduces first-byte
      latency, which matters for the first sentence where the user is waiting.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        def audio_stream():
            with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="ballad",      # Expressive, natural-sounding voice for Sage's persona
                input=body.text[:4096],
                response_format="mp3",
            ) as response:
                yield from response.iter_bytes(chunk_size=4096)

        return StreamingResponse(audio_stream(), media_type="audio/mpeg")
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
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
    Convert text to speech using OpenAI TTS (async). Returns the full MP3.

    Voice — "fable": warm, expressive voice fitting Sage's advisor persona.
    Valid tts-1 voices: nova, shimmer, echo, onyx, fable, alloy, ash, sage, coral.
    Note: "ballad" is gpt-4o-audio only — not valid for tts-1.

    Why AsyncOpenAI + .content instead of sync streaming:
    - The sync streaming approach (with_streaming_response + yield) runs in a
      thread-pool worker, which adds overhead and can serialize concurrent requests.
    - The frontend (SageChat.jsx) fires all sentence TTS requests in parallel and
      does `await res.blob()` anyway — so true byte-level streaming provides no
      benefit; the browser waits for the full audio before playback starts either way.
    - Using AsyncOpenAI keeps the request fully async (no thread-pool blocking),
      which is faster when multiple sentence TTS calls are in flight simultaneously.
    - Sentence TTS payloads are short (5-20 words → ~20-80KB MP3), so holding
      them in memory briefly is fine.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")

    try:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.audio.speech.create(
            model="tts-1",
            voice="fable",
            input=body.text[:4096],
            response_format="mp3",
        )
        return Response(response.content, media_type="audio/mpeg")
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from services.llm import ask_qwen, stream_qwen
from core.dependencies import get_rag
from core.logging import setup_logging
from services.chat import format_context_for_llm
from services.chat import extract_sources
from services.rewritter import rewrite_prompt
import json
from pydantic import BaseModel
from rag.pipeline import RagPipeline

logger = setup_logging()
router = APIRouter()

class PromptRequest(BaseModel):
    prompt: str
    notebook_id: str

@router.post("/api/prompt/stream")
async def prompt_stream(request: PromptRequest, rag: RagPipeline = Depends(get_rag)):
    user_prompt = request.prompt

    async def generate():
        try:
            try:
                context_json = rag.retrieve_context(user_prompt)
                formatted_context = format_context_for_llm(context_json)
                sources = extract_sources(context_json)
            except Exception:
                formatted_context = ""

            rewritten_prompt = await rewrite_prompt(request.notebook_id, user_prompt)

            enriched_prompt = f"""
    You are a helpful assistant.

    Context:
    {formatted_context}

    User Query:
    {rewritten_prompt}
    """

            full_response = []

            yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
            async for token in stream_qwen(enriched_prompt):
                full_response.append(token)

                # SSE format
                
                yield f"data: {json.dumps({'response': token})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            sources = []
            yield f"data: {json.dumps({'type': 'error', 'message': 'Streaming failed'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@router.post("/api/prompt")
async def prompt(request: PromptRequest, rag: RagPipeline = Depends(get_rag)):
    user_prompt = request.prompt
    try:
        # rag = RagPipeline()
        logger.info("RAG pipeline initialized - For Prompt")
        try:
            context_json = rag.retrieve_context(user_prompt)
            logger.info(f"context_json: {context_json}")

            formatted_context = format_context_for_llm(context_json)
            sources = extract_sources(context_json)
            logger.info(f"Context retrieved successfully for prompt: {user_prompt}")
        except Exception as e:
            logger.error(
                f"Error retrieving context for prompt: {user_prompt}. Exception: {e}"
            )
            formatted_context = ""  # fallback to empty context
            sources = []

    except Exception as e:
        logger.error(f"Error initiating RAG pipeline. Exception: {e}")
        return {"response": "Pipeline initialization failed."}

    rewritten_user_prompt = await rewrite_prompt(request.notebook_id, user_prompt)

    # --- Prompt engineering: combine context with user prompt ---
    enriched_prompt = f"""
    You are a helpful assistant.
    Use the following context to answer the user query:"

    Context:
    {formatted_context}

    User Query:
    {rewritten_user_prompt}
    """

    response_text = await ask_qwen(enriched_prompt)

    return {"chatbot_response": response_text, "sources": sources}
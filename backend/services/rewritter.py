from core.db import pg_connection
from core.logging import setup_logging
from services.llm import ask_qwen

logger = setup_logging()

def get_last_messages(notebook_id: str, limit: int = 6):
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, text
                FROM messages
                WHERE notebook_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (notebook_id, limit),
            )
            rows = cur.fetchall()
    return list(reversed(rows))

def format_chat_history(messages):
    history = []
    for role, text in messages:
        if role == "user":
            history.append(f"User: {text}")
        else:
            history.append(f"Assistant: {text}")

    return "\n".join(history)

async def rewrite_prompt(notebook_id: str, user_prompt: str) -> str:
    messages = get_last_messages(notebook_id)

    # If no history → return original prompt
    if not messages:
        return user_prompt

    history_text = format_chat_history(messages)

    # Prompt engineering for rewriting
    rewrite_instruction = f"""
    You are a query rewriter.

    Your task is to rewrite the user's query ONLY.

    Rules:
    - Do NOT answer
    - Do NOT greet
    - Do NOT add explanations
    - Output MUST be a single rewritten query
    - If the query is already clear, return it unchanged

    Conversation History:
    {history_text}

    Latest User Query:
    {user_prompt}

    Rewritten Query:
    """

    try:
        rewritten_prompt = await ask_qwen(rewrite_instruction)
        logger.info(f"Rewrittend User Query: {rewritten_prompt}")
        return rewritten_prompt.strip()
    except Exception as e:
        logger.error(f"Error rewriting prompt: {e}")
        return user_prompt  # fallback
import os
import psycopg2
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests as req

app = Flask(__name__)
CORS(app)

LLM_URL = os.environ.get('LLM_API_URL', 'http://10.10.30.65:8000')

def get_db():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '5432'),
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD']
    )

def ask_qwen(prompt):
    response = req.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "model": "qwen2.5-coder-14b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512
        },
        proxies={"http": None, "https": None}  # bypass proxy for internal call
    )
    return response.json()["choices"][0]["message"]["content"]

@app.route('/api/health')
def health():
    return jsonify({"status": "ok"})

@app.route('/api/prompt', methods=['POST'])
def prompt():
    data = request.json
    user_prompt = data.get('prompt', '')
    response_text = ask_qwen(user_prompt)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO prompts (prompt, response) VALUES (%s, %s)",
        (user_prompt, response_text)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"response": response_text})

@app.route('/api/history')
def history():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, prompt, response, created_at FROM prompts ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {"id": r[0], "prompt": r[1], "response": r[2], "created_at": str(r[3])}
        for r in rows
    ])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
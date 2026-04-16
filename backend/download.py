from huggingface_hub import snapshot_download

# Replace with any model repo ID from Hugging Face
model_id = "BAAI/bge-reranker-v2-m3"

# Download the model into a local folder (cached automatically)
local_dir = snapshot_download(repo_id=model_id)

local_dir = snapshot_download(
    repo_id=model_id,
    local_dir="D:/models/reranker/bge_reranker_v2_m3"
)

print(f"Model downloaded to: {local_dir}")
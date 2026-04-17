from huggingface_hub import snapshot_download

# Replace with any model repo ID from Hugging Face
model_id = "BAAI/bge-m3"

# Download the model into a local folder (cached automatically)
local_dir = snapshot_download(repo_id=model_id)

local_dir = snapshot_download(
    repo_id=model_id,
    local_dir="D:/models/bge-m3"
)

print(f"Model downloaded to: {local_dir}")
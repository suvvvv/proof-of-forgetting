"""Deploy the live demo to a Hugging Face Space (Docker). Keys go in as Space secrets, never into files.

    python -m scripts.deploy_space            # needs HF_TOKEN, ARMORIQ_API_KEY, GEMINI_API_KEY, MCP_AUTH_TOKEN in .env
"""

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

load_dotenv(".env")

SPACE_NAME = "proof-of-forgetting"
SECRETS = ("ARMORIQ_API_KEY", "GEMINI_API_KEY", "MCP_AUTH_TOKEN")
VARIABLES = {"GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-flash-latest"), "ARMORIQ_MCP_NAME": "memory-mcp"}
FILES = ["Dockerfile", "requirements.txt"]
DIRS = ["memory_mcp", "agent", "demo", "scripts"]

SPACE_README = """---
title: Proof of Forgetting
emoji: 🧠
colorFrom: indigo
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: AI agent that forgets provably, governed by ArmorIQ
---

Live demo for **Proof of Forgetting** (ArmorIQ Flow State 2026). Source: https://github.com/suvvvv/proof-of-forgetting
"""


def main():
    api = HfApi(token=os.environ["HF_TOKEN"])
    repo_id = f"{api.whoami()['name']}/{SPACE_NAME}"
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    for name in SECRETS:
        api.add_space_secret(repo_id, name, os.environ[name])
    for name, value in VARIABLES.items():
        api.add_space_variable(repo_id, name, value)

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "README.md").write_text(SPACE_README)
        api.upload_file(path_or_fileobj=str(Path(tmp, "README.md")), path_in_repo="README.md", repo_id=repo_id, repo_type="space")
    for f in FILES:
        api.upload_file(path_or_fileobj=f, path_in_repo=f, repo_id=repo_id, repo_type="space")
    for d in DIRS:
        api.upload_folder(folder_path=d, path_in_repo=d, repo_id=repo_id, repo_type="space",
                          ignore_patterns=["__pycache__/*", "*.pyc"])
    host = repo_id.replace("/", "-").replace("_", "-").lower()
    print(f"Space: https://huggingface.co/spaces/{repo_id}")
    print(f"App:   https://{host}.hf.space   (MCP at /mcp, bearer token required)")


if __name__ == "__main__":
    main()

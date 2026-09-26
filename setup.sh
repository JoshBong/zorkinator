#!/usr/bin/env bash
set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PYTHON_DIR="$ROOT_DIR/.python"
readonly VENV_DIR="$ROOT_DIR/.venv"
readonly UV_CACHE_DIR="$ROOT_DIR/.uv-cache"
readonly TOOLS_DIR="$ROOT_DIR/.tools"
readonly STORY_FILE="$ROOT_DIR/games/zork1.z5"
readonly STORY_URL="https://github.com/BYU-PCCL/z-machine-games/raw/master/jericho-game-suite/zork1.z5"
readonly STORY_MD5="b732a93a6244ddd92a9b9a3e3a46c687"
readonly SPACY_MODEL_URL="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

cd "$ROOT_DIR"

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
else
  echo "Installing uv locally..."
  mkdir -p "$TOOLS_DIR"
  installer="$(mktemp "${TMPDIR:-/tmp}/zorkinator-uv.XXXXXX")"
  trap 'rm -f "$installer"' EXIT
  curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$installer"
  UV_INSTALL_DIR="$TOOLS_DIR" sh "$installer"
  UV_BIN="$TOOLS_DIR/uv"
fi

export UV_PYTHON_INSTALL_DIR="$PYTHON_DIR"
export UV_CACHE_DIR

echo "Installing Python 3.11 and project dependencies..."
"$UV_BIN" python install 3.11
"$UV_BIN" venv --python 3.11 "$VENV_DIR"
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -r requirements-dev.txt
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" "$SPACY_MODEL_URL"

mkdir -p "$ROOT_DIR/games"
if [[ ! -f "$STORY_FILE" ]]; then
  echo "Downloading Zork I..."
  curl --proto '=https' --tlsv1.2 -fL "$STORY_URL" -o "$STORY_FILE"
fi

if command -v md5sum >/dev/null 2>&1; then
  actual_md5="$(md5sum "$STORY_FILE" | awk '{print $1}')"
elif command -v md5 >/dev/null 2>&1; then
  actual_md5="$(md5 -q "$STORY_FILE")"
elif command -v openssl >/dev/null 2>&1; then
  actual_md5="$(openssl md5 "$STORY_FILE" | awk '{print $NF}')"
else
  echo "error: md5sum, md5, or openssl is required to verify the game file" >&2
  exit 1
fi

if [[ "$actual_md5" != "$STORY_MD5" ]]; then
  echo "error: games/zork1.z5 has MD5 $actual_md5; expected $STORY_MD5" >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "Created .env; add ANTHROPIC_API_KEY and MONGODB_URI for automated runs."
fi

if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  git_dir="$(git -C "$ROOT_DIR" rev-parse --git-dir)"
  if [[ "$git_dir" != /* ]]; then
    git_dir="$ROOT_DIR/$git_dir"
  fi
  if [[ -w "$git_dir/hooks" ]]; then
    "$VENV_DIR/bin/pre-commit" install
  else
    echo "warning: $git_dir/hooks is read-only; pre-commit hook was not installed" >&2
  fi
fi

"$VENV_DIR/bin/python" -m unittest discover -v

echo
echo "Setup complete."
echo "Activate: source .venv/bin/activate"
echo "Play:     python -m zorkinator manual --seed 0"
echo "Checks:   ruff check . && ruff format --check . && mypy && python -m unittest discover -v"

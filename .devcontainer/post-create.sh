set -e

echo "==> Running post-create.sh"

echo "==> Installing python dependencies via poetry"
poetry install

echo "==> Installing claude."
curl -fsSL https://claude.ai/install.sh | bash
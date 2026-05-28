#!/bin/bash
set -e
echo "=== FYP Platform — Python Data Science Service Setup ==="

# Create virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Copy .env.example to .env and fill in your values"
echo "  2. Run:  python run.py"
echo "  3. Open: http://localhost:8000/ds/docs"

import sys
from pathlib import Path

# Make the project root importable (crosswalk/, DemoMcpServer.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

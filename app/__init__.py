"""ExpBot · local purchase-document preparation."""
from pathlib import Path
import sys

# Small pinned pure-Python PDF library is bundled so no runtime installation is needed.
_vendor = Path(__file__).resolve().parent / '_vendor'
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

__version__ = '1.0.0'
__app_name__ = 'ExpBot'

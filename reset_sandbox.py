"""Remove the synthetic portal database; stop the server before running."""
import os
from pathlib import Path
path = Path(os.environ.get('LINDA_DB_PATH', str(Path(__file__).resolve().parent / 'portal.sqlite3')))
if path.exists():
    path.unlink()
    print(f'Reset completed: {path}')
else:
    print('No database exists; fresh state will be created when the portal starts.')

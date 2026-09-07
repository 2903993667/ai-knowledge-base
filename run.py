import sys
import os

# When running as PyInstaller exe, set working directory to exe location
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
    os.chdir(base_dir)
    # Add _internal to sys.path so bundled modules can be found
    internal_dir = os.path.join(base_dir, '_internal')
    if os.path.isdir(internal_dir):
        sys.path.insert(0, internal_dir)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, base_dir)

import uvicorn
from app import app

if __name__ == '__main__':
    reload = not getattr(sys, 'frozen', False)
    uvicorn.run(app, host='127.0.0.1', port=8800, reload=reload)

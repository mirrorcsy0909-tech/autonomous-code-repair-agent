"""The existing paired workflow harness, configured for the SQLparse task catalog."""
from pathlib import Path
import subprocess
import sys

import comparison_agent as app

app.DATA = Path(__file__).resolve().parent / 'expanded_tasks'
app.IMAGE = 'code-repair-python:expanded-v1'
app.DOCKERFILE = Path(__file__).resolve().parent / 'Dockerfile.expanded'


if __name__ == '__main__':
    try:
        raise SystemExit(app.main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print('Interrupted; inspect saved records before starting a new prefix.', file=sys.stderr)
        raise SystemExit(130)

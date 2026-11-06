"""Shared managed CLI boundary: OS writer serialization; read-only database snapshots."""
import sys
from pathlib import Path
from ..core import FoundationError
from .lock import workspace_lock
from .workspace import read_database

READ_COMMANDS={'inspect','verify','handoff','stats','query','candidates','outcomes','conflicts'}


def module_main(original,argv,namespace):
    args=list(sys.argv[1:] if argv is None else argv)
    if '--help' in args or '-h' in args or '--version' in args:return original(args)
    workspace=None
    if '--workspace' in args:
        index=args.index('--workspace')
        if index+1<len(args):workspace=Path(args[index+1])
    for a in args:
        if a.startswith('--workspace='):workspace=Path(a.split('=',1)[1])
    if workspace is None:return original(args)
    try:
        if args and args[0] in READ_COMMANDS:
            database=namespace['Database'];namespace['Database']=read_database
            try:return original(args)
            finally:namespace['Database']=database
        with workspace_lock(workspace):return original(args)
    except (FoundationError,OSError):
        print('Workspace validation/lock failed; use doctor --offline. Private inputs are not printed.',file=sys.stderr);return 2

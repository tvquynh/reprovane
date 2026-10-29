"""Reuse ignored config > environment convention; never serialize credential values."""
import os
from pathlib import Path
from ..config import load_config
from ..openalex import OpenAlexClient


def _user_environment_key():
    # A desktop process may predate a newly set Windows User environment value.
    # Read only this named credential, never enumerate/export a profile or env.
    if os.name!='nt': return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as key:
            value,kind=winreg.QueryValueEx(key,'OPENALEX_API_KEY')
        return value.strip() if isinstance(value,str) else ''
    except OSError: return ''


def local_client(*,config_path=None,session=None):
    config=load_config(config_path,Path(__file__).resolve().parents[2])
    key=config.get('openalex','api_key') or os.environ.get('OPENALEX_API_KEY','').strip() or _user_environment_key()
    email=config.get('openalex','email') or ''
    return OpenAlexClient(key,email,base_url='https://api.openalex.org/works',session=session,max_attempts=3)

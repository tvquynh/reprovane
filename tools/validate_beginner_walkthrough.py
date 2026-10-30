"""Execute the documented offline practice blocks in disposable data folders."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def section_blocks(text,start,end):
    section=text.split(start,1)[1].split(end,1)[0]
    return re.findall(r'^```(?:powershell|bash)\n(.*?)^```',section,re.M|re.S)


def walkthrough(root=ROOT,python=None,console=None):
    python=Path(python or sys.executable).resolve()
    console=(os.environ.get('GITHUB_ACTIONS')=='true') if console is None else console
    windows=os.name=='nt';shell=shutil.which('powershell') or shutil.which('pwsh') if windows else shutil.which('bash')
    if not shell:raise RuntimeError('Required documentation-validation shell is unavailable')
    text=(root/'QUICKSTART.md').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='reprovane-beginner-') as folder:
        base=Path(folder)/'software';base.mkdir();ws=Path(folder)/'practice workspace'
        env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        for key in ('OPENALEX_API_KEY','UNPAYWALL_EMAIL','GH_TOKEN','GITHUB_TOKEN'):env.pop(key,None)
        if windows:
            launcher=next(p for p in (python.parent/'reprovane.exe',python.parent/'Scripts/reprovane.exe') if p.is_file())
            setup='$ErrorActionPreference = "Stop"\n$base = '+json.dumps(str(base))+'\n$py = '+json.dumps(str(python))+'\n$rp = '+json.dumps(str(launcher))+'\nSet-Location "$base"\n'
            def adapt(s):
                s=s.replace('$base = "$HOME\\Documents\\Reprovane"','$base = '+json.dumps(str(base)))
                s=s.replace('$ws = "$HOME\\Documents\\My Reprovane Review"','$ws = '+json.dumps(str(ws)))
                s=s.replace('".\\.venv-reprovane\\Scripts\\reprovane.exe"',json.dumps(str(launcher))).replace('".\\.venv-reprovane\\Scripts\\python.exe"',json.dumps(str(python)))
                if not console:s=s.replace('& $rp ','& $py -m reprovane ')
                lines=[]
                for line in s.splitlines():
                    lines.append(line)
                    if line.startswith('& $rp ') or line.startswith('& $py -m reprovane '):lines.append('if ($LASTEXITCODE -ne 0) { throw "Documented command failed" }')
                return '\n'.join(lines)
            first='\n'.join(section_blocks(text,'### A3.','### A5.'))
            reopened='\n'.join(section_blocks(text,'### A5.','## B.'))
            def run(script,name):
                return subprocess.run([shell,'-NoProfile','-NonInteractive','-Command',setup+adapt(script)],cwd=base,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
        else:
            launcher=python.parent/'reprovane'
            setup='set -eu\ncd '+shlex.quote(str(base))+'\nPY='+shlex.quote(str(python))+'\nRP='+shlex.quote(str(launcher))+'\n'
            def adapt(s):
                s=s.replace('cd "$HOME/Reprovane"','cd '+shlex.quote(str(base)))
                s=s.replace('RP="$HOME/Reprovane/.venv-reprovane/bin/reprovane"','RP='+shlex.quote(str(launcher)))
                s=s.replace('PY="$HOME/Reprovane/.venv-reprovane/bin/python"','PY='+shlex.quote(str(python)))
                s=s.replace('WS="$HOME/My Reprovane Review"','WS='+shlex.quote(str(ws)))
                return s if console else s.replace('"$RP" ','"$PY" -m reprovane ')
            first='\n'.join(section_blocks(text,'### D2.','### D3.'))
            reopened='\n'.join(section_blocks(text,'### D3.','## Upgrade'))
            def run(script,name):return subprocess.run([shell,'-c',setup+adapt(script)],cwd=base,env=env,capture_output=True,text=True,encoding='utf-8')
        for script,name in [(first,'first.ps1'),(reopened,'reopened.ps1')]:
            result=run(script,name)
            if result.returncode:raise AssertionError('Beginner '+name+' failed: '+result.stderr)
        project=json.loads((base/'practice-project-result.json').read_text(encoding='utf-8-sig'))
        revision=json.loads((base/'practice-revision-result.json').read_text(encoding='utf-8-sig'))
        assert project['project_id'] and revision['revision_id'] and (ws/'db/litintel.sqlite').is_file()
        return dict(status='PASS',shell='PowerShell' if windows else 'Bash',literal_console=console,document='QUICKSTART.md',blocks_executed='workspace/project/revision/inspect/verify and separate fresh-shell reopen',synthetic_only=True,credentials_removed=True,project_id_persisted=True,revision_id_persisted=True)


if __name__=='__main__':print(json.dumps(walkthrough(),sort_keys=True))

"""Deterministic public-documentation language, link and parser-only CLI audit."""
import argparse
import contextlib
import importlib
import io
from pathlib import Path
import re
import shlex
import subprocess
import sys
import unicodedata
from urllib.parse import unquote, urlparse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DIACRITICS = re.compile(r'[\u00c0-\u00ff\u0102\u0103\u0110\u0111\u0128\u0129\u0168\u0169\u01a0\u01a1\u01af\u01b0\u1ea0-\u1ef9]')
PHRASES = ('huong dan','bat dau','cai dat','chuan bi','nguoi dung','du lieu','bao mat',
           'van hanh','ky vong','neu ban','loi khi','chay lenh','tao thu muc','de tai',
           'nghien cuu','toan van')


def public_docs(root=ROOT):
    if (root/'.git').exists():
        names = subprocess.check_output(['git','ls-files','--cached','-z'],cwd=root).decode().split('\0')
        return sorted(root/n for n in names if n.endswith(('.md','.rst','.txt')) and (root/n).is_file())
    return sorted(p for p in root.rglob('*') if p.suffix in ('.md','.rst','.txt') and '.git' not in p.parts)


def language_findings(text):
    findings = []
    for number,line in enumerate(text.splitlines(),1):
        folded = unicodedata.normalize('NFD',line.lower().replace('\u0111','d'))
        folded = ''.join(c for c in folded if not unicodedata.combining(c))
        if DIACRITICS.search(line) or any(re.search(r'\b'+re.escape(p)+r'\b',folded) for p in PHRASES):
            findings.append(number)
    return findings


def markdown_links(text):
    return re.findall(r'!?\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)',text)


def heading_ids(text):
    ids=[];counts={}
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$',text,re.M):
        heading=re.sub(r'[^\w\- ]','',heading.lower()).replace(' ','-')
        n=counts.get(heading,0);counts[heading]=n+1
        ids.append(heading+('-'+str(n) if n else ''))
    return ids


def link_findings(path,root=ROOT):
    broken=[]
    for url in markdown_links(path.read_text(encoding='utf-8')):
        parsed=urlparse(url)
        if parsed.scheme:
            if parsed.netloc=='github.com' and parsed.path.startswith('/tvquynh/reprovane/blob/main/'):
                target=root/unquote(parsed.path.split('/blob/main/',1)[1])
            else:continue
        else:target=path.parent/unquote(parsed.path) if parsed.path else path
        if not target.exists():broken.append((url,'missing target'))
        elif parsed.fragment and target.is_file() and target.suffix=='.md' and unquote(parsed.fragment) not in heading_ids(target.read_text(encoding='utf-8')):
            broken.append((url,'missing heading'))
    return broken


def cli_commands(path):
    """Extract full CLI lines from fenced shell blocks, leaving data/OS commands alone."""
    results=[]
    for shell,block in re.findall(r'^```(\w+)\n(.*?)^```',path.read_text(encoding='utf-8'),re.M|re.S):
        if shell not in ('bash','powershell','sh','text'):continue
        for line in block.splitlines():
            match=re.search(r'(?:& \$rp |& \$py -m reprovane |"\$RP" |"\$PY" -m reprovane |^reprovane )(.+)',line)
            if match:
                command=match[1].split(' | ',1)[0].split(' > ',1)[0].strip()
                results.append((shell,command,shlex.split(command)))
    return results


def validate_command(args):
    """Parse the real CLI but stop before dispatch, file I/O or network operations."""
    from reprovane.__main__ import main, ROUTES
    args=list(args)
    if args and args[0] in ROUTES:
        module=importlib.import_module('reprovane.'+ROUTES[args[0]]+'.__main__')
        main=module._unmanaged_main
        args=args[1:]
    class Parsed(Exception):pass
    original=argparse.ArgumentParser.parse_args
    def parse(parser,args=None,namespace=None):
        original(parser,args,namespace)
        raise Parsed()
    try:
        with patch.object(argparse.ArgumentParser,'parse_args',parse),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):main(args)
    except Parsed:return True
    except SystemExit as exc:return exc.code==0
    return False


def main():
    docs=public_docs();language={p.relative_to(ROOT).as_posix():language_findings(p.read_text(encoding='utf-8')) for p in docs}
    broken={p.relative_to(ROOT).as_posix():link_findings(p) for p in docs if p.suffix=='.md'}
    commands=[]
    for name in ['README.md','QUICKSTART.md','USER_GUIDE.md','OPERATIONS.md','MIGRATION_FROM_SCHOLARFLOW_1.md']:
        for shell,text,args in cli_commands(ROOT/name):commands.append(dict(document=name,shell=shell,command=text,validated=validate_command(args),source='real argparse parser; dispatch intercepted'))
    import json
    print(json.dumps(dict(documents=len(docs),language={k:v for k,v in language.items() if v},broken={k:v for k,v in broken.items() if v},commands=commands),indent=2))
    return int(any(language.values()) or any(broken.values()) or any(not c['validated'] for c in commands))


if __name__=='__main__':raise SystemExit(main())

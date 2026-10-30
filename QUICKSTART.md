# Beginner Quick Start

Reprovane records literature searches and human review decisions so a team can
explain how it reached its results. This tutorial uses an empty practice project.
It does not search online, screen studies, or download papers yet. No application programming interface (API) key is needed.

You will install Python and Reprovane, check the installation, create a workspace
and practice project, then reopen the project in a new terminal. Work through four
stages: Python, a private software environment, a data workspace, and a project.
There is no fixed completion time; read each expected result before continuing.

This guide describes 2.0.1. The install command gets the latest published version.
While 2.0.1 awaits release, that may still be 2.0.0. Both support this offline practice
path. An unpublished candidate is tested separately by maintainers, not assumed to
be available on the Python Package Index (PyPI).

## Words you will see

| Word | Plain-English meaning |
| --- | --- |
| Python | The software that runs Reprovane. You do not need to write programs for this tutorial. |
| Terminal | A window where you type instructions and read their results. |
| PowerShell | The terminal command language used in the Windows examples. |
| Command | An instruction copied into the terminal and run with Enter. |
| Folder / directory | Two names for a place holding files. |
| Path | A file or folder's address. Quotes keep an address containing spaces together. |
| PATH | The system list of places to look for programs; different from one file's path. |
| Virtual environment | A private folder for Reprovane and its Python libraries, separate from other Python projects. The creation command calls this a `venv`. |
| Workspace | A data folder holding review projects and evidence, separate from the installed program. |
| Project | One review topic with a name and permanent identifier (ID). |
| Revision | A saved version of the project's description; a new revision preserves earlier descriptions. |
| API key | A private credential for online services. API means application programming interface, a service software can call. |
| JSON | JavaScript Object Notation: text containing named fields and values. You will copy a small example. |
| CLI | Command-line interface: the commands used to operate Reprovane. |

Copy only commands inside a code block, not backticks, language labels or prompt
markers such as `PS C:\...>` or `$`. The `$` inside Windows variables such as `$ws`
is part of the command. Run one block at a time and stop if it fails.
If you see Python's `>>>` prompt, enter this at that Python prompt only:

```python
exit()
```

Then open the terminal for your operating system. Do not paste shell commands at `>>>`.

## A. Windows 10/11

### A1. Python and PowerShell

1. Open a web browser and visit [official Python downloads](https://www.python.org/downloads/).
2. Install supported Python 3.11 or newer. Current Windows installation may use
   the Python install manager. If a traditional installer offers **Add python.exe
   to PATH**, select it so the terminal can find Python. On a managed computer,
   ask your administrator for approved Python.
3. Close old terminals. Press **Start**, type **PowerShell**, and open **Windows
   PowerShell** or **PowerShell**. Administrator mode is not needed.
4. In PowerShell:

```powershell
python --version
```

**What you should see:** `Python 3.11` or newer, followed by more numbers. If not found,
try this PowerShell command:

```powershell
py -3 --version
```

If it works, use `py -3` instead of the initial `python` in the environment-creation
command below. Later commands use the environment's Python directly. If neither
works, consult the [official Windows instructions](https://docs.python.org/3/using/windows.html)
or your administrator. Do not install software from a random error-message link.

### A2. Private software environment

In PowerShell, make a writable software folder. `$HOME` already means your user
folder; these commands read it without changing it. `Set-Location` moves the terminal.

```powershell
$base = "$HOME\Documents\Reprovane"
New-Item -ItemType Directory -Force "$base" | Out-Null
Set-Location "$base"
$env:PYTHONUTF8 = "1"
python -m venv .venv-reprovane
.\.venv-reprovane\Scripts\python.exe -m pip install --upgrade pip
.\.venv-reprovane\Scripts\python.exe -m pip install reprovane
$rp = ".\.venv-reprovane\Scripts\reprovane.exe"
$py = ".\.venv-reprovane\Scripts\python.exe"
& $rp --version
& $rp doctor --offline
```

**What you should see:** successful installation (or already installed), a version
number, then JSON. `pip` is Python's package installer. Look for `offline: true`,
`python_supported: true` and dependencies marked `true`. Missing online credentials
are normal. The guide's candidate version is `2.0.1`; see the publication note above.
No environment activation or PowerShell execution-policy change is required.

The `&` runs the program named by a variable. If Application Control blocks the
launcher, leave that policy in place and use this PowerShell module alternative:

```powershell
& $py -m reprovane --version
& $py -m reprovane doctor --offline
```

In later Windows commands replace `& $rp` with `& $py -m reprovane`, keeping all
following arguments. If Python itself is blocked, ask the administrator.

### A3. Workspace

Use a new practice folder, never a folder inside `.venv-reprovane`. In PowerShell:

```powershell
$ws = "$HOME\Documents\My Reprovane Review"
& $rp workspace init --workspace "$ws"
& $rp doctor --offline --workspace "$ws" --full
```

**What you should see:** initialization JSON, then `workspace_exists: true`,
`schema_version: 13`, no pending migrations and zero foreign-key violations. A new
workspace has no sealed review runs; that is normal. `--full` checks all sealed
objects rather than sampling. The workspace is your data, not your installation.

### A4. Practice project and revision

In PowerShell, create a project once and save its returned ID:

```powershell
$project = (& $rp project create --workspace "$ws" --key practice-review --display-name "Practice Review" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Project creation failed. Stop and read the error." }
$projectId = $project.project_id
$project | ConvertTo-Json | Set-Content -Encoding utf8 practice-project-result.json
$projectId
```

**What you should see:** a nonempty project ID. `ConvertFrom-Json` reads named
fields. The saved file lets you recover the ID later. Do not repeat a successful
`project create` with the same key. Copy this entire PowerShell block next,
including the `@'` and `'@` lines, to save a description and revision 1:

```powershell
@'
{
  "title": "Practice Review",
  "question": "How should our team document a reproducible review?",
  "status": "TRAINING_ONLY"
}
'@ | Set-Content -Encoding utf8 practice-project.json
$revision = (& $rp project revision --workspace "$ws" --project-id $projectId --number 1 --manifest practice-project.json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Revision creation failed. Stop and inspect the project." }
$revisionId = $revision.revision_id
$revision | ConvertTo-Json | Set-Content -Encoding utf8 practice-revision-result.json
$revisionId
& $rp project inspect --workspace "$ws" --project-id $projectId
& $rp verify --offline --workspace "$ws"
```

**What you should see:** a revision ID, the saved project with its revision, then
successful verification. This project contains no studies yet. Record the workspace
path, both IDs and result files. A title alone is not an ID. The practice description
is not a reviewed research protocol; revisions preserve changes to your description.

### A5. Close and reopen

Closing PowerShell does not delete files. Open a new PowerShell window and run:

```powershell
$base = "$HOME\Documents\Reprovane"
Set-Location "$base"
$env:PYTHONUTF8 = "1"
$rp = ".\.venv-reprovane\Scripts\reprovane.exe"
$py = ".\.venv-reprovane\Scripts\python.exe"
$ws = "$HOME\Documents\My Reprovane Review"
$projectId = (Get-Content -Raw practice-project-result.json | ConvertFrom-Json).project_id
& $rp doctor --offline --workspace "$ws" --full
& $rp project inspect --workspace "$ws" --project-id $projectId
& $rp verify --offline --workspace "$ws"
```

**What you should see:** the same project and revision. Variables last for only one
terminal window, so set them again. Use the module alternative if needed. Do not
create another workspace just to reopen the original.

## B. macOS

Open **Spotlight** with Command-Space, type **Terminal**, and open it. In Terminal:

```bash
python3 --version
```

**What you should see:** Python 3.11 or newer. If missing or too old, use a supported
signed installer from [python.org](https://www.python.org/downloads/) and follow the
[official macOS instructions](https://docs.python.org/3/using/mac.html), including
installer-provided certificate setup when offered. Reopen Terminal and check again.
Do not remove or replace Apple's system Python. Continue with section D.

## C. Linux

Open your distribution's **Terminal** application. In Linux Bash:

```bash
python3 --version
```

**What you should see:** Python 3.11 or newer. If needed, use your distribution's
official package manager or administrator. Some distributions separately package
the virtual-environment component, often called `python3-venv`. Ask for the component
matching your Python if environment creation fails. Do not replace system Python
or install this package with administrator pip. Continue with D.

## D. macOS/Linux commands

These Bash-compatible commands also work in the usual macOS shell. Do not paste
PowerShell's `& $rp` syntax here. No activation or extra JSON program is required.

### D1. Install

In macOS Terminal or Linux Bash:

```bash
mkdir -p "$HOME/Reprovane"
cd "$HOME/Reprovane"
python3 -m venv .venv-reprovane
.venv-reprovane/bin/python -m pip install --upgrade pip
.venv-reprovane/bin/python -m pip install reprovane
RP="$HOME/Reprovane/.venv-reprovane/bin/reprovane"
PY="$HOME/Reprovane/.venv-reprovane/bin/python"
"$RP" --version
"$RP" doctor --offline
```

**What you should see:** successful installation, version and doctor results as
explained in A2. Direct paths avoid activation and PATH confusion. The equivalent
macOS/Linux module form is:

```bash
"$PY" -m reprovane --version
```

If using that form, replace `"$RP"` with `"$PY" -m reprovane` in later commands.

### D2. Workspace and project

In macOS Terminal or Linux Bash:

```bash
WS="$HOME/My Reprovane Review"
"$RP" workspace init --workspace "$WS"
"$RP" doctor --offline --workspace "$WS" --full
"$RP" project create --workspace "$WS" --key practice-review --display-name "Practice Review" > practice-project-result.json
```

**What you should see:** successful workspace initialization and doctor results.
The last command saves project JSON rather than printing it. Immediately check
its exit code in macOS Terminal or Linux Bash:

```bash
echo $?
```

`0` means the previous command succeeded; otherwise stop. Next, copy this entire
macOS/Linux block. The short Python instruction reads an ID from the saved JSON;
you do not need to program or install `jq`:

```bash
PROJECT_ID=$("$PY" -c 'import json; print(json.load(open("practice-project-result.json"))["project_id"])')
printf '%s\n' "$PROJECT_ID"
cat > practice-project.json <<'JSON'
{
  "title": "Practice Review",
  "question": "How should our team document a reproducible review?",
  "status": "TRAINING_ONLY"
}
JSON
"$RP" project revision --workspace "$WS" --project-id "$PROJECT_ID" --number 1 --manifest practice-project.json > practice-revision-result.json
```

**What you should see:** a project ID. Check `echo $?` again after the revision
command, and stop if nonzero. Then in macOS Terminal or Linux Bash:

```bash
REVISION_ID=$("$PY" -c 'import json; print(json.load(open("practice-revision-result.json"))["revision_id"])')
printf '%s\n' "$REVISION_ID"
"$RP" project inspect --workspace "$WS" --project-id "$PROJECT_ID"
"$RP" verify --offline --workspace "$WS"
```

**What you should see:** a revision ID, saved project/revision and successful
verification. Keep the workspace address, IDs and result files for later.

### D3. Close and reopen

Close Terminal, open a new window, then run these macOS/Linux commands:

```bash
cd "$HOME/Reprovane"
RP="$HOME/Reprovane/.venv-reprovane/bin/reprovane"
PY="$HOME/Reprovane/.venv-reprovane/bin/python"
WS="$HOME/My Reprovane Review"
PROJECT_ID=$("$PY" -c 'import json; print(json.load(open("practice-project-result.json"))["project_id"])')
"$RP" doctor --offline --workspace "$WS" --full
"$RP" project inspect --workspace "$WS" --project-id "$PROJECT_ID"
"$RP" verify --offline --workspace "$WS"
```

**What you should see:** the original project and revision. Closing the terminal
reset variables, not your stored work. Nothing needs activation.

## Upgrade and uninstall

Before important upgrades, stop writers and back up the full workspace as described
in [Operations](OPERATIONS.md). Reopen the software folder as above. In PowerShell:

```powershell
.\.venv-reprovane\Scripts\python.exe -m pip install --upgrade reprovane
& $py -m reprovane --version
```

In macOS Terminal or Linux Bash:

```bash
.venv-reprovane/bin/python -m pip install --upgrade reprovane
"$PY" -m reprovane --version
```

**What you should see:** the installed published version. Read the [Changelog](CHANGELOG.md).
Installation is not a promise of automatic data migration. Check a copy of important
data before adopting an upgrade.

To uninstall only the package, close running programs, reopen the software folder
and run this in PowerShell:

```powershell
.\.venv-reprovane\Scripts\python.exe -m pip uninstall reprovane
```

Or in macOS Terminal or Linux Bash:

```bash
.venv-reprovane/bin/python -m pip uninstall reprovane
```

Read pip's confirmation. Alternatively, remove only the specifically named
`.venv-reprovane` folder with your file manager after checking its address. This
removes the installed program. Keep the separate workspace and saved project files
unless you intend to delete research data. Reinstalling software cannot restore
a deleted workspace.

## Troubleshooting

| Symptom | Meaning | What to do | What NOT to do |
| --- | --- | --- | --- |
| Python not recognized | The terminal cannot find Python. | Reopen PowerShell, try `py -3 --version`, revisit A1. | Do not install random suggested executables. |
| Windows opens Microsoft Store | A command alias opened the Store. | Use official Python installation or installed `py`; ask IT if managed. | Do not change enterprise policy without authorization. |
| Permission denied | Folder/program access is restricted. | Choose a personal writable folder or ask IT. | Do not run all review work as administrator. |
| pip cannot connect | Installation needs network access. | Check connectivity and the sanitized error. | Do not disable certificate verification. |
| Corporate proxy blocks pip | An approved network route is needed. | Ask IT for its supported package-installation method. | Do not bypass network or endpoint protection. |
| Application Control blocks the launcher | The generated executable is restricted. | Use the documented module form if Python is allowed; otherwise ask IT. | Do not disable Application Control. |
| Command missing after reopening | Variables/folder changed. | Repeat A5 or D3 and use direct paths. | Do not create a replacement workspace. |
| Workspace path has spaces | The shell split an address. | Keep quotes around paths. | Do not rename existing data to guess a fix. |
| Database ABSENT | No initialized database at this address. | Check the saved workspace path first. | Do not delete or reinitialize real data. |
| Writer active / lock | Another process may be writing. | Let it finish; consult Operations. | Do not delete lock files to force access. |
| API key missing | Online access is not configured. | Complete this offline tutorial; read User Guide later. | Do not paste a key into an issue or project file. |
| Garbled characters | Terminal encoding differs. | Use UTF-8; set `$env:PYTHONUTF8 = "1"` in PowerShell and inspect saved state. | Do not alter sealed bytes to improve display. |
| Wrong Python version | Python is older than 3.11. | Install supported Python and create a new environment. | Do not overwrite a system interpreter. |
| Python `>>>` prompt | You are in interactive Python. | Enter `exit()` there, then return to the terminal. | Do not paste shell commands at `>>>`. |

## Get help and continue

You may share the program version, operating system, exact command with private
values removed, exit code, sanitized error and checked doctor output. Never share
an API key, password, cookie, Authorization header, private signed URL, browser
profile, private database or restricted full-text PDF (Portable Document Format).
Read [Security](SECURITY.md), then continue with the [User Guide](USER_GUIDE.md).
Human reviewers decide inclusion; the software records their work.

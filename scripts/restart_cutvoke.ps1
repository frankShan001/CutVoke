param([switch]$CheckOnly, [switch]$WebOnly)
$ErrorActionPreference = 'Stop'
$cutvokeRoot = Split-Path -Parent $PSScriptRoot
$cutvokePython = Join-Path $cutvokeRoot '.venv\Scripts\python.exe'
$cutvokeData = Join-Path $env:USERPROFILE '.cutvoke\data'
$cutvokeDb = Join-Path $cutvokeData 'projects.sqlite'
$cutvokePort = 8787
$cutvokeUrl = "http://127.0.0.1:$cutvokePort"
$cutvokeStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$cutvokeEvidence = Join-Path $cutvokeRoot "output\activation\restart-$cutvokeStamp"
$env:PYTHONPATH = Join-Path $cutvokeRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
$cutvokeHeaders = @{}
if ($env:CUTVOKE_TOKEN) { $cutvokeHeaders['X-CutVoke-Token'] = $env:CUTVOKE_TOKEN }

if (!(Test-Path -LiteralPath $cutvokePython) -or !(Test-Path -LiteralPath $cutvokeDb)) {
    throw 'The source Python environment or original project database is missing.'
}
$cutvokeExpectedJson = & $cutvokePython -c 'import json,sys; from cutvoke.runtime import runtime_identity; print(json.dumps(runtime_identity(sys.argv[1])))' $cutvokeDb
if ($LASTEXITCODE -ne 0) { throw 'Could not determine the source runtime.' }
$cutvokeExpected = $cutvokeExpectedJson | ConvertFrom-Json
$cutvokeListener = @(Get-NetTCPConnection -LocalPort $cutvokePort -State Listen -ErrorAction SilentlyContinue)
$cutvokeOld = $null
$cutvokeWrapper = $null
if ($cutvokeListener.Count) {
    if (@($cutvokeListener.OwningProcess | Select-Object -Unique).Count -ne 1) { throw 'Multiple listeners need manual inspection.' }
    $cutvokeOld = Get-CimInstance Win32_Process -Filter "ProcessId=$($cutvokeListener[0].OwningProcess)"
    $cutvokeWrapper = Get-CimInstance Win32_Process -Filter "ProcessId=$($cutvokeOld.ParentProcessId)"
    $cutvokeServePattern = '-m\s+cutvoke\s+serve\s+--port\s+8787(?:\s|$)'
    if ($cutvokeOld.CommandLine -notmatch $cutvokeServePattern -or
        !$cutvokeWrapper -or $cutvokeWrapper.ExecutablePath -ine $cutvokePython -or
        $cutvokeWrapper.CommandLine -notmatch $cutvokeServePattern) {
        throw 'Port 8787 is not the expected source CutVoke service. Nothing was stopped.'
    }
    if ($cutvokeOld.CommandLine -notmatch '-m\s+cutvoke\s+serve\s+--port\s+8787\s*$') {
        $cutvokeExistingRuntime = Invoke-RestMethod "$cutvokeUrl/api/v1/runtime" -Headers $cutvokeHeaders -TimeoutSec 5
        if ($cutvokeExistingRuntime.processId -ne $cutvokeOld.ProcessId -or
            $cutvokeExistingRuntime.dataPath -ine $cutvokeDb -or
            $cutvokeExistingRuntime.packageDirectory -ine $cutvokeExpected.packageDirectory) {
            throw 'The existing runtime is not the original data and source checkout. Nothing was stopped.'
        }
    }
}

# The dry run reads state only. A manual launch creates an online SQLite backup.
$cutvokeSnapshotCode = @'
import hashlib,json,sqlite3,sys
from pathlib import Path
source=Path(sys.argv[1]).resolve()
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db:
    active=db.execute('select job_id,status from export_jobs where status not in (?,?,?)',('succeeded','failed','cancelled')).fetchall()
    if active:
        raise SystemExit('Active export jobs must finish before restarting: '+repr(active))
    rows=db.execute('select * from projects order by project_id').fetchall()
    digest=hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    result={'projectCount':len(rows),'projectDigest':digest}
    if len(sys.argv)>2:
        target=Path(sys.argv[2]); target.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(target) as backup:
            db.backup(backup)
            if backup.execute('pragma integrity_check').fetchone()[0]!='ok':
                raise SystemExit('Backup integrity check failed.')
        result.update(backup=str(target),backupSha256=hashlib.sha256(target.read_bytes()).hexdigest())
print(json.dumps(result))
'@
if ($CheckOnly) {
    $cutvokeBeforeJson = & $cutvokePython -c $cutvokeSnapshotCode $cutvokeDb
} else {
    $cutvokeBeforeJson = & $cutvokePython -c $cutvokeSnapshotCode $cutvokeDb (Join-Path $cutvokeEvidence 'projects-before.sqlite')
}
if ($LASTEXITCODE -ne 0) { throw 'Project/export safety check or database backup failed. Nothing was stopped.' }
$cutvokeBefore = $cutvokeBeforeJson | ConvertFrom-Json
if ($CheckOnly) {
    [pscustomobject]@{ checkOnly=$true; oldProcessId=$cutvokeOld.ProcessId; expectedFingerprint=$cutvokeExpected.sourceFingerprint; dataPath=$cutvokeDb; projectCount=$cutvokeBefore.projectCount } | ConvertTo-Json
    exit 0
}
$cutvokeBeforeJson | Set-Content -LiteralPath (Join-Path $cutvokeEvidence 'before.json') -Encoding UTF8
if ($cutvokeOld) {
    $cutvokeNow = Get-CimInstance Win32_Process -Filter "ProcessId=$($cutvokeOld.ProcessId)"
    if (!$cutvokeNow -or $cutvokeNow.CreationDate -ne $cutvokeOld.CreationDate -or $cutvokeNow.CommandLine -ne $cutvokeOld.CommandLine) {
        throw 'The service process changed during backup. Nothing was stopped.'
    }
    Stop-Process -Id $cutvokeOld.ProcessId -ErrorAction Stop
    $cutvokeStillWrapper = Get-CimInstance Win32_Process -Filter "ProcessId=$($cutvokeWrapper.ProcessId)"
    if ($cutvokeStillWrapper -and $cutvokeStillWrapper.CreationDate -eq $cutvokeWrapper.CreationDate -and $cutvokeStillWrapper.CommandLine -eq $cutvokeWrapper.CommandLine) {
        Stop-Process -Id $cutvokeWrapper.ProcessId -ErrorAction Stop
    }
}
$cutvokeStartArguments = @('-m','cutvoke','serve','--port',"$cutvokePort",'--data',('"'+$cutvokeData+'"'),'--log',('"'+(Join-Path $cutvokeEvidence 'serve.jsonl')+'"'))
$cutvokeStarted = Start-Process -FilePath $cutvokePython -ArgumentList $cutvokeStartArguments -WorkingDirectory $cutvokeRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $cutvokeEvidence 'stdout.log') -RedirectStandardError (Join-Path $cutvokeEvidence 'stderr.log') -PassThru
$cutvokeRuntime = $null
for ($cutvokeAttempt=0; $cutvokeAttempt -lt 40; $cutvokeAttempt++) {
    Start-Sleep -Milliseconds 500
    try { $cutvokeRuntime = Invoke-RestMethod "$cutvokeUrl/api/v1/runtime" -Headers $cutvokeHeaders -TimeoutSec 2; break } catch {}
    if ($cutvokeStarted.HasExited) { throw "The new service exited. Read $cutvokeEvidence\stderr.log" }
}
if (!$cutvokeRuntime -or $cutvokeRuntime.sourceFingerprint -ne $cutvokeExpected.sourceFingerprint -or $cutvokeRuntime.dataPath -ine $cutvokeDb) {
    throw "The new runtime did not match this source and the original database. Read $cutvokeEvidence"
}
$cutvokeCapabilities = Invoke-RestMethod "$cutvokeUrl/api/v1/capabilities" -Headers $cutvokeHeaders -TimeoutSec 10
if (@($cutvokeCapabilities.commands | Where-Object type -eq 'project.preflight').Count -ne 1) { throw 'The new command catalogue is missing project.preflight.' }
$cutvokeAfterJson = & $cutvokePython -c $cutvokeSnapshotCode $cutvokeDb
if ($LASTEXITCODE -ne 0) { throw 'Post-restart project verification failed.' }
$cutvokeAfter = $cutvokeAfterJson | ConvertFrom-Json
if ($cutvokeAfter.projectDigest -ne $cutvokeBefore.projectDigest) { throw 'Project content changed during restart; compare the saved backup.' }
[pscustomobject]@{ ok=$true; runtime=$cutvokeRuntime; commandCount=@($cutvokeCapabilities.commands).Count; projectCount=$cutvokeAfter.projectCount; projectsUnchanged=$true; backup=$cutvokeBefore.backup } | ConvertTo-Json -Depth 8 | Tee-Object -FilePath (Join-Path $cutvokeEvidence 'result.json')
Write-Host "Refresh the editor at $cutvokeUrl"
if ($WebOnly) {
    Write-Host 'Web service restarted and verified. Refresh the editor. Codex and MCP can stay open.'
} else {
    Write-Host 'Web service restarted and verified. Fully quit and reopen Codex to reconnect MCP, then return to this chat.'
}

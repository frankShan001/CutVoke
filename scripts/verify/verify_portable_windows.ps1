param([Parameter(Mandatory)][string]$App, [Parameter(Mandatory)][string]$Output, [switch]$Normal)
$ErrorActionPreference = 'Stop'
$taskFolder = Join-Path (Split-Path $App) 'CutVoke Data'
$taskDatabase = Join-Path $taskFolder 'data/projects.sqlite'
$taskHash = (Get-FileHash -LiteralPath $taskDatabase -Algorithm SHA256).Hash
$taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskProcess = $null
$taskResult = @{ passed = $false; beforeSHA256 = $taskHash; scope = 'Actual Windows ACL write denial and native error dialog; dialog text inspected via Win32.' }
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class CutVokeWindows {
  public delegate bool EnumProc(IntPtr handle, IntPtr data);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr data);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr data);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint process);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle, StringBuilder text, int max);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
  [DllImport("user32.dll")] public static extern IntPtr PostMessage(IntPtr handle, uint msg, IntPtr w, IntPtr l);
}
'@
if ($Normal) {
    $taskNormal = @{ passed = $false; scope = 'Normal executable startup without QA/debug flags; native window, frozen backend health and window-close cleanup.' }
    $taskBackend = $null
    try {
        Remove-Item Env:CUTVOKE_DATA, Env:CUTVOKE_PORTABLE_HOME, Env:CUTVOKE_DESKTOP_QA -ErrorAction SilentlyContinue
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        Remove-Item Env:__COMPAT_LAYER -ErrorAction SilentlyContinue
        $taskProcess = Start-Process -FilePath $App -WorkingDirectory $Output -PassThru
        $taskDeadline = [DateTime]::UtcNow.AddSeconds(90)
        do {
            $taskBackend = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($taskProcess.Id)" |
                Where-Object { $_.Name -eq 'cutvoke-engine.exe' } | Select-Object -First 1
            if ($taskBackend -and $taskBackend.CommandLine -match '--port\s+(\d+)') {
                $taskOrigin = "http://127.0.0.1:$($Matches[1])"
                try { $taskRuntime = Invoke-RestMethod -Uri "$taskOrigin/api/v1/runtime" -TimeoutSec 2 } catch { $taskRuntime = $null }
                if ($taskRuntime.processId -eq $taskBackend.ProcessId) { break }
            }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $taskDeadline -and !$taskProcess.HasExited)
        if (!$taskRuntime -or $taskRuntime.processId -ne $taskBackend.ProcessId) { throw 'Normal startup did not reach its own healthy frozen backend.' }
        $taskProcess.Refresh()
        if ($taskProcess.MainWindowHandle -eq [IntPtr]::Zero -or ![CutVokeWindows]::IsWindowVisible($taskProcess.MainWindowHandle)) { throw 'Normal startup did not open a visible native window.' }
        $taskNormal.runtime = $taskRuntime
        $taskNormal.nativeWindowHandle = $taskProcess.MainWindowHandle.ToInt64()
        $taskNormal.windowTitle = $taskProcess.MainWindowTitle
        if (!$taskProcess.CloseMainWindow() -or !$taskProcess.WaitForExit(20000)) { throw 'Normal window close did not exit cleanly.' }
        if (Get-Process -Id $taskBackend.ProcessId -ErrorAction SilentlyContinue) { throw 'Normal close left its owned backend running.' }
        $taskNormal.passed = $true
    } finally {
        if ($taskProcess -and !$taskProcess.HasExited) { Stop-Process -Id $taskProcess.Id -Force }
        if ($taskBackend -and (Get-Process -Id $taskBackend.ProcessId -ErrorAction SilentlyContinue)) { Stop-Process -Id $taskBackend.ProcessId -Force }
        $taskNormal | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Output 'normal-startup-result.json') -Encoding utf8
    }
    exit 0
}
try {
    & icacls.exe $taskFolder /deny "*${taskSid}:(OI)(CI)(W)" /t | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply isolated test ACL.' }
    # Explicit denies on existing children take priority over a child's explicit
    # Chromium ACL. Prove the profile really is unwritable before testing it.
    $taskWriteDenied = $false
    try { [IO.File]::WriteAllText((Join-Path $taskFolder 'profile/write-must-fail'), 'test') }
    catch [UnauthorizedAccessException] { $taskWriteDenied = $true }
    $taskResult.profileWriteDenied = $taskWriteDenied
    if (!$taskWriteDenied) { throw 'The isolated profile did not reject writes.' }
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    # No debug/QA switches: normal double-click-equivalent startup.
    Remove-Item Env:CUTVOKE_DATA, Env:CUTVOKE_PORTABLE_HOME, Env:CUTVOKE_DESKTOP_QA -ErrorAction SilentlyContinue
    $taskProcess = Start-Process -FilePath $App -WorkingDirectory $Output -PassThru
    $taskWindows = [Collections.Generic.List[IntPtr]]::new()
    $taskTexts = [Collections.Generic.List[string]]::new()
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $taskWindows.Clear(); $taskTexts.Clear()
        [CutVokeWindows]::EnumWindows({ param($handle, $unused)
            [uint32]$taskOwner = 0
            [CutVokeWindows]::GetWindowThreadProcessId($handle, [ref]$taskOwner) | Out-Null
            if ($taskOwner -eq $taskProcess.Id -and [CutVokeWindows]::IsWindowVisible($handle)) {
                $taskText = [Text.StringBuilder]::new(4096)
                [CutVokeWindows]::GetWindowText($handle, $taskText, 4096) | Out-Null
                if ($taskText.ToString() -like '*Storage*') {
                    $taskWindows.Add($handle); $taskTexts.Add($taskText.ToString())
                    [CutVokeWindows]::EnumChildWindows($handle, { param($child, $unusedChild)
                        $taskChildText = [Text.StringBuilder]::new(4096)
                        [CutVokeWindows]::GetWindowText($child, $taskChildText, 4096) | Out-Null
                        $taskTexts.Add($taskChildText.ToString()); return $true
                    }, [IntPtr]::Zero) | Out-Null
                    # Modern task dialogs draw controls whose text is not
                    # exposed by cross-process GetWindowText. Use real Windows
                    # accessibility providers for their displayed content.
                    $taskElement = [Windows.Automation.AutomationElement]::FromHandle($handle)
                    $taskElements = $taskElement.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
                    foreach ($taskChild in $taskElements) {
                        if ($taskChild.Current.Name) { $taskTexts.Add($taskChild.Current.Name) }
                    }
                }
            }
            return $true
        }, [IntPtr]::Zero) | Out-Null
        if ($taskWindows.Count -and ($taskTexts -join ' ') -like '*cannot write*') { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $taskDeadline -and !$taskProcess.HasExited)
    $taskResult.dialogText = @($taskTexts.ToArray())
    if (!$taskWindows.Count -or ($taskTexts -join ' ') -notlike '*cannot write*') { throw 'Expected native read-only storage explanation was not found.' }
    foreach ($handle in $taskWindows) { [CutVokeWindows]::PostMessage($handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }
    if (!$taskProcess.WaitForExit(15000)) { throw 'Storage failure did not close cleanly.' }
    $taskResult.afterSHA256 = (Get-FileHash -LiteralPath $taskDatabase -Algorithm SHA256).Hash
    if ($taskResult.afterSHA256 -ne $taskHash) { throw 'Existing database changed during read-only startup.' }
    $taskResult.passed = $true
} catch {
    $taskResult.error = $_.ToString()
    throw
} finally {
    & icacls.exe $taskFolder /remove:d "*$taskSid" /t | Out-Null
    if ($taskProcess -and !$taskProcess.HasExited) { Stop-Process -Id $taskProcess.Id -Force }
    [IO.File]::WriteAllText((Join-Path $Output 'readonly-result.json'), ($taskResult | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
}

param([Parameter(Mandatory)][string]$App, [Parameter(Mandatory)][string]$Output)
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
try {
    & icacls.exe $taskFolder /deny "*${taskSid}:(OI)(CI)(W)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply isolated test ACL.' }
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
                }
            }
            return $true
        }, [IntPtr]::Zero) | Out-Null
        if ($taskWindows.Count) { break }
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
    & icacls.exe $taskFolder /remove:d "*$taskSid" | Out-Null
    if ($taskProcess -and !$taskProcess.HasExited) { Stop-Process -Id $taskProcess.Id -Force }
    $taskResult | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Output 'readonly-result.json') -Encoding utf8
}

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskDownloads = Join-Path $taskRoot 'build/desktop-downloads'
New-Item -ItemType Directory -Path $taskDownloads -Force | Out-Null

function Get-VerifiedArchive([string]$Name, [string]$Url, [string]$Sha256, [string]$Cached = '') {
    $target = Join-Path $taskDownloads $Name
    if (!(Test-Path -LiteralPath $target) -and $Cached -and (Test-Path -LiteralPath $Cached)) {
        if ((Get-FileHash -LiteralPath $Cached -Algorithm SHA256).Hash -eq $Sha256) {
            Copy-Item -LiteralPath $Cached -Destination $target
        }
    }
    if (!(Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $Sha256) {
        Write-Host "Downloading $Name from the official release..."
        $partial = "$target.partial"
        Invoke-WebRequest -Uri $Url -OutFile $partial -TimeoutSec 240 -UseBasicParsing
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
            throw "SHA256 mismatch: $Name"
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
    return $target
}

$electronFolder = Join-Path $taskRoot 'desktop/node_modules/electron'
$electronVersion = (Get-Content -LiteralPath (Join-Path $electronFolder 'package.json') -Raw | ConvertFrom-Json).version
$electronArchive = "electron-v$electronVersion-win32-x64.zip"
$electronSums = Get-Content -LiteralPath (Join-Path $electronFolder 'checksums.json') -Raw | ConvertFrom-Json
$electronHash = $electronSums.PSObject.Properties[$electronArchive].Value
if (!$electronHash) { throw "Missing official Electron checksum: $electronArchive" }
$electronZip = Get-VerifiedArchive 'electron.zip' "https://github.com/electron/electron/releases/download/v$electronVersion/$electronArchive" $electronHash
$electronDist = Join-Path $electronFolder 'dist'
if (!(Test-Path -LiteralPath (Join-Path $electronDist 'electron.exe'))) {
    Expand-Archive -LiteralPath $electronZip -DestinationPath $electronDist -Force
}
[System.IO.File]::WriteAllText((Join-Path $electronFolder 'path.txt'), 'electron.exe')

$extractor = Join-Path $taskRoot 'desktop/node_modules/electron-winstaller/vendor/7z.exe'
if (!(Test-Path -LiteralPath $extractor)) { throw 'Install desktop npm dependencies first.' }
$builderCache = Join-Path $env:LOCALAPPDATA 'electron-builder/Cache'
$nsisArchive = Get-VerifiedArchive 'nsis.7z' 'https://github.com/electron-userland/electron-builder-binaries/releases/download/nsis-3.0.4.1/nsis-3.0.4.1.7z' '9877df902530f96357d13a7a31ae2b9df67f48b11ffc9a1700a7c961574ec5fa' (Join-Path $builderCache 'nsis-3.0.4.1/nsis-3.0.4.1.7z')
$resourcesArchive = Get-VerifiedArchive 'nsis-resources.7z' 'https://github.com/electron-userland/electron-builder-binaries/releases/download/nsis-resources-3.4.1/nsis-resources-3.4.1.7z' '593a9a92ef958321293ac6a2ee61e64bf1bd543142a5bd6b3d310709cc924103'
foreach ($item in @(@($nsisArchive, 'nsis'), @($resourcesArchive, 'nsis-resources'))) {
    $destination = Join-Path $taskDownloads $item[1]
    & $extractor x $item[0] "-o$destination" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Extraction failed: $($item[1])" }
}
$sevenArchive = Get-VerifiedArchive '7zip-win-x64.tar.gz' 'https://github.com/electron-userland/electron-builder-binaries/releases/download/7zip@1.0.0/7zip-win-x64.tar.gz' 'be071f15bd6da2f78fe81c6ddef2009b0c4d8a51f36b780cb806c7e6df95e1b3' (Join-Path $builderCache '7zip@1.0.0/7zip-win-x64.tar.gz')
$sevenFolder = Join-Path $taskDownloads '7zip'
New-Item -ItemType Directory -Path $sevenFolder -Force | Out-Null
tar.exe -xf $sevenArchive --strip-components 1 -C $sevenFolder
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath (Join-Path $sevenFolder 'bin/7za.exe'))) { throw '7zip extraction failed.' }
Write-Host 'Desktop tools verified and ready.'

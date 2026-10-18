[CmdletBinding()]
param(
    [string]$RuntimeRoot = 'F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-sandbox-20261018\computation-runtime-final',
    [string]$DownloadsRoot = 'F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-sandbox-20261018',
    [string]$TaskTemp = 'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-sandbox-20261018',
    [string]$PythonSourceRoot = 'F:\Codex另一个工作的地方\python\cpython-3.13.14-windows-x86_64-none',
    [string]$BuildPython = 'F:\Codex另一个工作的地方\worktrees\mathmodel-paper-studio-impl-20261015\.venv\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$approvedRoot = 'F:\Codex另一个工作的地方'
$lockPath = Join-Path $projectRoot 'requirements-execution.lock'
$wheelhouse = Join-Path $DownloadsRoot 'wheels'
$sitePackages = Join-Path $RuntimeRoot 'Lib\site-packages'
$licenseRoot = Join-Path $RuntimeRoot 'licenses\execution'
$manifestPath = Join-Path $RuntimeRoot 'runtime-manifest.json'

function Get-ValidatedFPath([string]$candidate, [switch]$RequireFile) {
    $resolved = [IO.Path]::GetFullPath($candidate)
    if (!$resolved.StartsWith($approvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Runtime, downloads, caches, and build paths must remain below the approved F: root.'
    }
    $current = 'F:\'
    foreach ($component in $resolved.Substring(3).Split('\')) {
        if (!$component) { continue }
        $current = Join-Path $current $component
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Runtime build paths cannot pass through a symbolic link or directory junction.'
            }
        }
    }
    if ($RequireFile -and !(Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw 'A required build input file is missing.'
    }
    if ($RequireFile -and !$resolved.StartsWith($approvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build input files must be stored below the approved F: root.'
    }
    return $resolved
}

if (!(Test-Path -LiteralPath 'F:\' -PathType Container)) { throw 'F: drive is unavailable.' }
$projectRoot = Get-ValidatedFPath $projectRoot
$RuntimeRoot = Get-ValidatedFPath $RuntimeRoot
$DownloadsRoot = Get-ValidatedFPath $DownloadsRoot
$TaskTemp = Get-ValidatedFPath $TaskTemp
$PythonSourceRoot = Get-ValidatedFPath $PythonSourceRoot
$BuildPython = Get-ValidatedFPath $BuildPython -RequireFile
$lockPath = Get-ValidatedFPath $lockPath -RequireFile
if (!$RuntimeRoot.StartsWith($DownloadsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The runtime package must be created inside its task-specific downloads directory.'
}
if (!(Test-Path -LiteralPath $DownloadsRoot -PathType Container)) {
    throw 'Create the task-specific downloads directory before building or downloading packages.'
}
if (!(Test-Path -LiteralPath $wheelhouse -PathType Container)) { throw 'The F: wheelhouse is missing.' }
if (Test-Path -LiteralPath $RuntimeRoot) { throw 'RuntimeRoot already exists; choose a new task-specific path.' }

[void][IO.Directory]::CreateDirectory($TaskTemp)
$env:TEMP = $TaskTemp
$env:TMP = $TaskTemp
$env:TMPDIR = $TaskTemp
$env:PIP_CACHE_DIR = Join-Path $TaskTemp 'pip-cache'
$env:PYTHONPYCACHEPREFIX = Join-Path $TaskTemp 'pycache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
[void][IO.Directory]::CreateDirectory($env:PIP_CACHE_DIR)

[void][IO.Directory]::CreateDirectory($RuntimeRoot)
$excluded = @(
    (Join-Path $PythonSourceRoot 'Lib\site-packages'),
    (Join-Path $PythonSourceRoot 'Scripts'),
    (Join-Path $PythonSourceRoot 'include'),
    (Join-Path $PythonSourceRoot 'libs'),
    (Join-Path $PythonSourceRoot 'tcl')
)
$copyArgs = @($PythonSourceRoot, $RuntimeRoot, '/E', '/COPY:DAT', '/R:1', '/W:1',
    '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD') + $excluded
& robocopy.exe @copyArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw 'Copying the F: CPython base runtime failed.' }

$RuntimeRoot = Get-ValidatedFPath $RuntimeRoot
[void][IO.Directory]::CreateDirectory($sitePackages)
$installArgs = @('-B', '-m', 'pip', 'install', '--target', $sitePackages,
    '--require-hashes', '--only-binary', ':all:', '--no-index',
    '--find-links', $wheelhouse, '--disable-pip-version-check',
    '--no-warn-script-location', '-r', $lockPath)
& $BuildPython @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Installing the locked execution dependencies failed.' }

$entryTarget = Join-Path $RuntimeRoot 'sandbox_entry.py'
Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\runtime\sandbox_entry.py') `
    -Destination $entryTarget
Get-ValidatedFPath $entryTarget -RequireFile | Out-Null

$licenseParent = Split-Path -Parent $licenseRoot
[void][IO.Directory]::CreateDirectory($licenseParent)
& (Join-Path $RuntimeRoot 'python.exe') -B `
    (Join-Path $projectRoot 'scripts\verify_f_drive_outputs.py') `
    --verify-lock $lockPath --licenses $licenseRoot
if ($LASTEXITCODE -ne 0) { throw 'Execution dependency or license inventory verification failed.' }

& (Join-Path $RuntimeRoot 'python.exe') -B `
    (Join-Path $projectRoot 'scripts\create_computation_runtime_manifest.py') `
    --runtime-root $RuntimeRoot --lock $lockPath --output $manifestPath
if ($LASTEXITCODE -ne 0) { throw 'Computation runtime manifest creation failed.' }

$inventory = Join-Path $DownloadsRoot 'computation-runtime-files.json'
& (Join-Path $RuntimeRoot 'python.exe') -B `
    (Join-Path $projectRoot 'scripts\verify_f_drive_outputs.py') `
    --inventory-root $RuntimeRoot --trusted-runtime-lock $lockPath --report $inventory
if ($LASTEXITCODE -ne 0) { throw 'F: computation runtime inventory failed.' }
Write-Output $RuntimeRoot

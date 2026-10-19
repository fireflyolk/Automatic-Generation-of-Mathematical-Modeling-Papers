[CmdletBinding()]
param(
    [string]$BuildRoot,
    [string]$OutputRoot,
    [string]$PythonPath,
    [string]$TaskTemp,
    [string]$ComputationRuntimeRoot,
    [switch]$CheckOnly,
    [switch]$LaunchSource
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectParent = Split-Path -Parent $projectRoot
if ([IO.Path]::GetPathRoot($projectRoot) -ne 'F:\' -or (Split-Path -Leaf $projectParent) -ne 'worktrees') {
    throw 'Project root must be a worktree below the approved F: workspace.'
}
$approvedRoot = Split-Path -Parent $projectParent
$taskTemp = if ($TaskTemp) { $TaskTemp } else { Join-Path $approvedRoot 'tmp\mathmodel-paper-studio-fluent-20261017' }
if (!$ComputationRuntimeRoot) {
    $ComputationRuntimeRoot = Join-Path $approvedRoot 'downloads\mathmodel-paper-studio-sandbox-20261018\computation-runtime-final'
}
if (!$BuildRoot) { $BuildRoot = Join-Path $taskTemp 'build' }
if (!$OutputRoot) { $OutputRoot = Join-Path $approvedRoot 'downloads\mathmodel-paper-studio-fluent-20261017\release' }
if (!$PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$verifier = Join-Path $PSScriptRoot 'verify_f_drive_outputs.py'
function Get-ValidatedFPath([string]$candidate, [switch]$RequireFile) {
    $resolved = [IO.Path]::GetFullPath($candidate)
    if (!$resolved.StartsWith($approvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'All project/build/output/runtime paths must be below the approved F: root.'
    }
    $relative = $resolved.Substring(3)
    $current = 'F:\'
    foreach ($component in $relative.Split('\')) {
        if (!$component) { continue }
        $current = Join-Path $current $component
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Managed paths cannot pass through a symbolic link or directory junction.'
            }
        }
    }
    if ($RequireFile) {
        if (!(Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw 'The selected Python interpreter must be an existing file below the approved F: root.'
        }
    }
    return $resolved
}
if (!(Test-Path -LiteralPath 'F:\' -PathType Container)) { throw 'F: drive is unavailable.' }
$projectRoot = Get-ValidatedFPath $projectRoot
$BuildRoot = Get-ValidatedFPath $BuildRoot
$OutputRoot = Get-ValidatedFPath $OutputRoot
$taskTemp = Get-ValidatedFPath $taskTemp
$ComputationRuntimeRoot = Get-ValidatedFPath $ComputationRuntimeRoot
$pythonPath = Get-ValidatedFPath $PythonPath -RequireFile
$verifier = Get-ValidatedFPath $verifier -RequireFile
$runtimePython = Get-ValidatedFPath (Join-Path $ComputationRuntimeRoot 'python.exe') -RequireFile
$runtimeManifest = Get-ValidatedFPath (Join-Path $ComputationRuntimeRoot 'runtime-manifest.json') -RequireFile
$runtimeEntry = Get-ValidatedFPath (Join-Path $ComputationRuntimeRoot 'sandbox_entry.py') -RequireFile
$sourceRuntimeEntry = Get-ValidatedFPath (Join-Path $projectRoot 'packaging\runtime\sandbox_entry.py') -RequireFile
if (!(Test-Path -LiteralPath $ComputationRuntimeRoot -PathType Container)) {
    throw 'The locked F: computation runtime directory is missing.'
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $pythonPath -B -c "import sys; from mathmodel_paper_studio.bootstrap.paths import validate_managed_path; tuple(validate_managed_path(p) for p in sys.argv[1:])" $projectRoot $BuildRoot $OutputRoot $taskTemp $pythonPath
if ($LASTEXITCODE -ne 0) { throw 'Physical F: path validation failed before creating outputs.' }
& $pythonPath -B $verifier --runtime-entry $runtimeEntry --source-runtime-entry $sourceRuntimeEntry
if ($LASTEXITCODE -ne 0) { throw 'Computation runtime Sandbox launcher does not match the current host protocol.' }
[void][IO.Directory]::CreateDirectory($taskTemp)
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:TMPDIR = $taskTemp
$env:PYTHONPYCACHEPREFIX = Join-Path $taskTemp 'pycache'
$env:PIP_CACHE_DIR = Join-Path $taskTemp 'pip-cache'
$env:UV_CACHE_DIR = Join-Path $taskTemp 'uv-cache'
$env:MPLCONFIGDIR = Join-Path $taskTemp 'matplotlib'
$env:QT_SHADER_CACHE_PATH = Join-Path $taskTemp 'qt-cache'
$env:QML_DISK_CACHE_PATH = Join-Path $taskTemp 'qml-cache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $taskTemp 'pyinstaller-cache'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:MMPS_COMPUTATION_RUNTIME_ROOT = $ComputationRuntimeRoot
if ($LaunchSource) {
    & $pythonPath -B $verifier
    exit $LASTEXITCODE
}
& $pythonPath -B $verifier --verify-lock (Join-Path $projectRoot 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Dependency lock verification failed.' }
if ($CheckOnly) { exit 0 }
if (Test-Path -LiteralPath $BuildRoot) { throw 'BuildRoot already exists; choose a new empty F: build path.' }
if (Test-Path -LiteralPath $OutputRoot) { throw 'OutputRoot already exists; choose a new empty F: output path.' }
[void][IO.Directory]::CreateDirectory($BuildRoot)
[void][IO.Directory]::CreateDirectory($OutputRoot)
$licenseRoot = Join-Path $BuildRoot 'licenses'
& $pythonPath -B $verifier --verify-lock (Join-Path $projectRoot 'requirements.lock') --licenses $licenseRoot
if ($LASTEXITCODE -ne 0) { throw 'License inventory failed.' }
$env:MMPS_BUILD_ROOT = $BuildRoot
$env:MMPS_LICENSE_ROOT = $licenseRoot
$env:MMPS_COMPUTATION_RUNTIME_ROOT = $ComputationRuntimeRoot
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -B -m PyInstaller --noconfirm --workpath (Join-Path $BuildRoot 'work') --distpath $OutputRoot (Join-Path $projectRoot 'packaging\mathmodel_paper_studio.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $packageRoot = Join-Path $OutputRoot 'MathModelPaperStudio'
    $packageRuntime = Join-Path $packageRoot '_internal\computation-runtime'
    $runtimeInventory = Join-Path $OutputRoot 'computation-runtime-files.json'
    & (Join-Path $packageRuntime 'python.exe') -B $verifier --inventory-root $packageRuntime `
        --trusted-runtime-lock (Join-Path $projectRoot 'requirements-execution.lock') --report $runtimeInventory
    if ($LASTEXITCODE -ne 0) { throw 'Bundled computation runtime inventory/lock verification failed.' }
    $packageRuntimeEntry = Get-ValidatedFPath (Join-Path $packageRuntime 'sandbox_entry.py') -RequireFile
    & $pythonPath -B $verifier --runtime-entry $packageRuntimeEntry --source-runtime-entry $sourceRuntimeEntry
    if ($LASTEXITCODE -ne 0) { throw 'Bundled Sandbox launcher does not match the current host protocol.' }
    & $pythonPath -B $verifier --inventory-root $packageRoot --report (Join-Path $OutputRoot 'package-files.json')
    if ($LASTEXITCODE -ne 0) { throw 'Package path/secret scan failed.' }
    & $pythonPath -B $verifier --archive (Join-Path $packageRoot 'MathModelPaperStudio.exe') --report (Join-Path $OutputRoot 'archive-members.json')
    if ($LASTEXITCODE -ne 0) { throw 'Executable member audit failed.' }
} finally {
    Pop-Location
}
Write-Output (Join-Path $OutputRoot 'MathModelPaperStudio\MathModelPaperStudio.exe')

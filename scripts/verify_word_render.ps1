[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputDocx,
    [Parameter(Mandatory = $true)][string]$OutputPdf,
    [Parameter(Mandatory = $true)][string]$EvidenceDirectory,
    [ValidateRange(10, 300)][int]$TimeoutSeconds = 120,
    [switch]$PreflightOnly,
    [switch]$Worker,
    [string]$RunToken
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Assert-FPath([string]$Path) {
    if (![IO.Path]::IsPathRooted($Path) -or $Path -notmatch '^[Ff]:\\') { throw 'F_PATH_REQUIRED' }
    $full = [IO.Path]::GetFullPath($Path)
    if ([IO.Path]::GetPathRoot($full) -ine 'F:\') { throw 'F_PATH_REQUIRED' }
    $ancestor = $full
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'REPARSE_PATH_NOT_SUPPORTED'
            }
        }
        $parent = Split-Path -Parent $ancestor
        if ($parent -eq $ancestor) { break }
        $ancestor = $parent
    }
    return $full
}

function Read-SafeXml([IO.Compression.ZipArchiveEntry]$Entry) {
    $settings = New-Object Xml.XmlReaderSettings
    $settings.DtdProcessing = [Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver = $null
    $stream = $Entry.Open()
    $reader = $null
    try {
        $reader = [Xml.XmlReader]::Create($stream, $settings)
        $xml = New-Object Xml.XmlDocument
        $xml.XmlResolver = $null
        $xml.Load($reader)
        return ,$xml
    } catch {
        throw ('UNSAFE_XML: ' + $Entry.FullName)
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        $stream.Dispose()
    }
}

function Assert-SafeFieldInstruction([string]$Instruction) {
    if (!$Instruction -or $Instruction.Length -gt 4096) { throw 'FIELD_NOT_SUPPORTED' }
    $normalized = [Text.RegularExpressions.Regex]::Replace($Instruction, '\s+', ' ').Trim()
    $match = [Text.RegularExpressions.Regex]::Match($normalized, '^([A-Za-z]+)(?:\s|\\|$)')
    if (!$match.Success) { throw 'FIELD_NOT_SUPPORTED' }
    $command = $match.Groups[1].Value.ToUpperInvariant()
    $safeCommands = @(
        'AUTHOR', 'COMMENTS', 'CREATEDATE', 'DATE', 'DOCVARIABLE', 'DOCPROPERTY',
        'EDITTIME', 'FILENAME', 'FILESIZE', 'INDEX', 'KEYWORDS', 'LISTNUM',
        'NUMCHARS', 'NUMPAGES', 'NUMWORDS', 'PAGE', 'PAGEREF', 'PRINTDATE',
        'REF', 'SAVEDATE', 'SECTION', 'SECTIONPAGES', 'SEQ', 'STYLEREF',
        'SUBJECT', 'TIME', 'TITLE', 'TOA', 'TOC', 'TC'
    )
    if ($command -notin $safeCommands) { throw 'FIELD_NOT_SUPPORTED' }
}

function Assert-SafeWordFields([xml]$Xml) {
    $wordNamespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    $fields = New-Object 'Collections.Generic.List[object]'
    foreach ($node in $Xml.SelectNodes('//*')) {
        if ($node.NamespaceURI -ne $wordNamespace) { continue }
        if ($node.LocalName -eq 'fldSimple') {
            Assert-SafeFieldInstruction $node.GetAttribute('instr', $wordNamespace)
        } elseif ($node.LocalName -eq 'fldChar') {
            $fieldType = $node.GetAttribute('fldCharType', $wordNamespace)
            if ($fieldType -eq 'begin') {
                $fields.Add([ordered]@{ instruction = ''; separated = $false })
            } elseif ($fieldType -eq 'separate') {
                if ($fields.Count -eq 0) { throw 'FIELD_NOT_SUPPORTED' }
                $field = $fields[$fields.Count - 1]
                if ($field.separated) { throw 'FIELD_NOT_SUPPORTED' }
                Assert-SafeFieldInstruction $field.instruction
                $field.separated = $true
            } elseif ($fieldType -eq 'end') {
                if ($fields.Count -eq 0) { throw 'FIELD_NOT_SUPPORTED' }
                $field = $fields[$fields.Count - 1]
                if (!$field.separated) { Assert-SafeFieldInstruction $field.instruction }
                $fields.RemoveAt($fields.Count - 1)
            } else {
                throw 'FIELD_NOT_SUPPORTED'
            }
        } elseif ($node.LocalName -eq 'instrText') {
            if ($fields.Count -eq 0) { throw 'FIELD_NOT_SUPPORTED' }
            $field = $fields[$fields.Count - 1]
            if ($field.separated) { throw 'FIELD_NOT_SUPPORTED' }
            $field.instruction += $node.InnerText
            if ($field.instruction.Length -gt 4096) { throw 'FIELD_NOT_SUPPORTED' }
        }
    }
    if ($fields.Count -ne 0) { throw 'FIELD_NOT_SUPPORTED' }
}

function Assert-SafeDocx([string]$Path) {
    if ([IO.Path]::GetExtension($Path) -ine '.docx' -or
        !(Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'DOCX_REQUIRED' }
    $size = (Get-Item -LiteralPath $Path).Length
    if ($size -le 0 -or $size -gt 64MB) { throw 'PACKAGE_SIZE_LIMIT' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = $null
    try {
        try {
            $zip = [IO.Compression.ZipFile]::OpenRead($Path)
            $entries = $zip.Entries
        } catch {
            throw 'DOCX_PACKAGE_INVALID'
        }
        if ($null -eq $entries) { throw 'DOCX_PACKAGE_INVALID' }
        if ($entries.Count -gt 4096) { throw 'PACKAGE_MEMBER_LIMIT' }
        $names = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        [long]$expanded = 0
        foreach ($entry in $entries) {
            $name = $entry.FullName
            if (!$name -or $name.StartsWith('/') -or $name.Contains('\') -or
                $name.Contains(':') -or ($name.Split('/') -contains '..') -or !$names.Add($name)) {
                throw 'UNSAFE_MEMBER'
            }
            $expanded += $entry.Length
            if ($entry.Length -gt 16MB -or $expanded -gt 64MB) { throw 'PACKAGE_SIZE_LIMIT' }
            if ($name -match '(?i)(vba|activex|embeddings|attachedtemplate)' -or
                $name -match '(?i)\.(bin|vba)$') { throw 'ACTIVE_CONTENT' }
            if ($name -notmatch '(?i)(\.xml|\.rels)$') { continue }
            $xml = Read-SafeXml $entry
            if ($name -ieq '[Content_Types].xml' -and
                $xml.OuterXml -match '(?i)(macroEnabled|vbaProject|activeX|oleObject)') {
                throw 'ACTIVE_CONTENT'
            }
            foreach ($node in $xml.SelectNodes('//*')) {
                if ($node.LocalName -eq 'Relationship' -and
                    $node.GetAttribute('TargetMode') -ieq 'External') {
                    throw 'EXTERNAL_RELATIONSHIP'
                }
                if ($node.NamespaceURI -ne 'http://schemas.openxmlformats.org/wordprocessingml/2006/main') {
                    continue
                }
                if ($node.LocalName -in @('object', 'control', 'altChunk', 'attachedTemplate')) {
                    throw 'ACTIVE_CONTENT'
                }
            }
            Assert-SafeWordFields $xml
        }
        if (!$names.Contains('[Content_Types].xml') -or !$names.Contains('word/document.xml')) {
            throw 'DOCX_PARTS_REQUIRED'
        }
    } finally {
        if ($null -ne $zip) { $zip.Dispose() }
    }
}

function Get-Sha256([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '') }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function Write-NewJson([string]$Path, $Value) {
    $encoded = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 8))
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($encoded, 0, $encoded.Length) } finally { $stream.Dispose() }
}

function Stop-OwnedWord([string]$OwnerPath, [string]$Token, [int]$WorkerId) {
    if (!(Test-Path -LiteralPath $OwnerPath -PathType Leaf)) { return }
    $owner = Get-Content -LiteralPath $OwnerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($owner.token -ne $Token -or [int]$owner.worker_pid -ne $WorkerId) { return }
    $process = Get-Process -Id ([int]$owner.word_pid) -ErrorAction SilentlyContinue
    if ($null -ne $process -and $process.ProcessName -ieq 'WINWORD' -and
        $process.StartTime.ToUniversalTime().Ticks -eq [long]$owner.start_ticks) {
        Stop-Process -Id $process.Id -Force
    }
}

function Invoke-WordWorker {
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') { throw 'STA_REQUIRED' }
    if (@(Get-Process -Name WINWORD -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'WORD_ALREADY_RUNNING'
    }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WordRenderNative {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
'@
    $profileRoot = Join-Path $EvidenceDirectory 'profile'
    $roamingRoot = Join-Path $profileRoot 'AppData\Roaming'
    $localRoot = Join-Path $profileRoot 'AppData\Local'
    foreach ($folder in @($profileRoot, $roamingRoot, $localRoot)) {
        [void][IO.Directory]::CreateDirectory($folder)
    }
    $env:USERPROFILE = $profileRoot
    $env:APPDATA = $roamingRoot
    $env:LOCALAPPDATA = $localRoot
    $workerEnvironment = [ordered]@{
        USERPROFILE=$env:USERPROFILE; APPDATA=$env:APPDATA; LOCALAPPDATA=$env:LOCALAPPDATA
        TEMP=$env:TEMP; TMP=$env:TMP
    }
    Write-NewJson (Join-Path $EvidenceDirectory 'worker-environment.json') $workerEnvironment
    $word = $null
    $documents = $null
    $document = $null
    $wordPid = 0
    $stage = 'activate'
    $result = $null
    try {
        $activationStarted = [DateTime]::UtcNow
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0
        $word.AutomationSecurity = 3
        $newProcesses = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | Where-Object {
            $_.StartTime.ToUniversalTime() -ge $activationStarted.AddSeconds(-1)
        })
        if ($newProcesses.Count -ne 1) { throw 'WORD_PID_OWNERSHIP_AMBIGUOUS' }
        $process = $newProcesses[0]
        $wordPid = $process.Id
        $owner = [ordered]@{
            token = $RunToken; worker_pid = $PID; word_pid = $wordPid
            start_ticks = $process.StartTime.ToUniversalTime().Ticks
        }
        Write-NewJson (Join-Path $EvidenceDirectory 'owner.json') $owner
        $version = (Get-Item -LiteralPath $process.Path).VersionInfo.FileVersion
        $documents = $word.Documents
        if ($documents.Count -ne 0) { throw 'WORD_INSTANCE_NOT_EMPTY' }
        $stage = 'open_read_only'
        $missing = [Type]::Missing
        $document = $documents.Open($InputDocx, $false, $true, $false)
        $word.Visible = $false
        if ($null -eq $document) { throw 'WORD_OPEN_RETURNED_NULL' }
        if (!$document.ReadOnly -or $document.FullName -ine $InputDocx -or $documents.Count -ne 1) {
            throw 'WORD_DOCUMENT_OWNERSHIP_FAILED'
        }
        $documentWindows = $document.Windows
        $window = $null
        try {
            $window = $documentWindows.Item(1)
            [uint32]$windowPid = 0
            [void][WordRenderNative]::GetWindowThreadProcessId([IntPtr]$window.Hwnd, [ref]$windowPid)
            if ([int]$windowPid -ne $wordPid) { throw 'WORD_WINDOW_PID_MISMATCH' }
        } finally {
            if ($null -ne $window) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($window) }
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($documentWindows)
        }
        $stage = 'repaginate'
        $document.Repaginate()
        $pages = $document.ComputeStatistics(2)
        $stage = 'export_pdf'
        $document.ExportAsFixedFormat($OutputPdf, 17, $false, 0, 0, 1, 1, 0, $false, $false, 0, $true, $true, $false)
        if (!(Test-Path -LiteralPath $OutputPdf -PathType Leaf) -or
            (Get-Item -LiteralPath $OutputPdf).Length -lt 100) { throw 'PDF_NOT_CREATED' }
        $result = [ordered]@{
            status = 'exported'; word_version = $version; word_pid = $wordPid
            page_count_word = $pages; input_docx = $InputDocx; output_pdf = $OutputPdf
            input_sha256 = (Get-Sha256 $InputDocx)
            pdf_sha256 = (Get-Sha256 $OutputPdf)
            macros_disabled = $true; read_only = $true; add_to_recent_files = $false
            worker_environment = $workerEnvironment; word_environment_verified = $false
            internal_office_c_writes_audited = $false
            autorecover_disabled = $false
            autorecover_note = 'Read-only document is never edited. Persistent Word recovery preferences are not modified.'
            normal_template_saved_by_script = $false; quit_save_changes = 0
        }
    } catch {
        $result = [ordered]@{
            status = 'failed'; stage = $stage; error = $_.Exception.Message
            hresult = ('0x{0:X8}' -f $_.Exception.HResult); word_pid = $wordPid; stack = $_.ScriptStackTrace; detail = $_.Exception.ToString()
        }
    } finally {
        if ($null -ne $document) {
            try { $document.Close(0) } catch { }
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
        }
        if ($null -ne $word) {
            try {
                if ($null -eq $documents -or $documents.Count -eq 0) { $result.cleanup_safe = $true; $word.Quit(0) }
                else { $result.cleanup = 'Word kept open because another document is present.' }
            } catch { }
        }
        if ($null -ne $documents) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($documents) }
        if ($null -ne $word) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    Write-NewJson (Join-Path $EvidenceDirectory 'worker-result.json') $result
    if ($result.status -ne 'exported') { throw ('WORD_EXPORT_FAILED: ' + $result.error) }
}

try {
    $InputDocx = Assert-FPath $InputDocx
    $OutputPdf = Assert-FPath $OutputPdf
    $EvidenceDirectory = Assert-FPath $EvidenceDirectory
    if ([IO.Path]::GetExtension($OutputPdf) -ine '.pdf') { throw 'PDF_OUTPUT_REQUIRED' }
    if (Test-Path -LiteralPath $OutputPdf) { throw 'OUTPUT_EXISTS' }
    if (!(Test-Path -LiteralPath $EvidenceDirectory -PathType Container) -or
        !(Test-Path -LiteralPath (Split-Path -Parent $OutputPdf) -PathType Container)) {
        throw 'EXISTING_F_DIRECTORY_REQUIRED'
    }
    Assert-SafeDocx $InputDocx
    $sourceHash = (Get-Sha256 $InputDocx)
    if ($PreflightOnly) {
        [ordered]@{status='preflight_passed'; input_sha256=$sourceHash; word_started=$false} |
            ConvertTo-Json -Compress
        exit 0
    }
    $env:TEMP = $EvidenceDirectory
    $env:TMP = $EvidenceDirectory
    $env:TMPDIR = $EvidenceDirectory
    if ($Worker) {
        if ($RunToken -notmatch '^[a-f0-9]{32}$') { throw 'WORKER_TOKEN_REQUIRED' }
        Invoke-WordWorker
        exit 0
    }
    if (@(Get-Process -Name WINWORD -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'WORD_ALREADY_RUNNING'
    }
    $token = [Guid]::NewGuid().ToString('N')
    $runDirectory = Join-Path $EvidenceDirectory ('word-export-' + $token)
    [void][IO.Directory]::CreateDirectory($runDirectory)
    $snapshot = Join-Path $runDirectory 'source-copy.docx'
    [IO.File]::Copy($InputDocx, $snapshot, $false)
    if ((Get-Sha256 $snapshot) -ne $sourceHash) {
        throw 'SOURCE_COPY_HASH_MISMATCH'
    }
    $privatePdf = Join-Path $runDirectory 'render.pdf'
    $stdout = Join-Path $runDirectory 'stdout.log'
    $stderr = Join-Path $runDirectory 'stderr.log'
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-STA', '-File', ('"{0}"' -f $PSCommandPath),
        '-InputDocx', ('"{0}"' -f $snapshot), '-OutputPdf', ('"{0}"' -f $privatePdf),
        '-EvidenceDirectory', ('"{0}"' -f $runDirectory), '-Worker', '-RunToken', $token)
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $shell
    $startInfo.Arguments = $arguments -join ' '
    $startInfo.WorkingDirectory = $runDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $child = New-Object Diagnostics.Process
    $child.StartInfo = $startInfo
    [void]$child.Start()
    if (!$child.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-OwnedWord (Join-Path $runDirectory 'owner.json') $token $child.Id
        Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
        throw ('WORD_EXPORT_TIMEOUT: ' + $runDirectory)
    }
    $child.WaitForExit()
    [IO.File]::WriteAllText($stdout, $child.StandardOutput.ReadToEnd(), [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($stderr, $child.StandardError.ReadToEnd(), [Text.UTF8Encoding]::new($false))
    $workerReportPath = Join-Path $runDirectory 'worker-result.json'
    if (Test-Path -LiteralPath $workerReportPath -PathType Leaf) {
        $workerReport = Get-Content -LiteralPath $workerReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($workerReport.PSObject.Properties.Name -contains 'cleanup_safe' -and $workerReport.cleanup_safe) {
            Stop-OwnedWord (Join-Path $runDirectory 'owner.json') $token $child.Id
        }
    }
    if ($null -eq $child.ExitCode -or $child.ExitCode -ne 0) {
        throw ('WORD_WORKER_FAILED: ' + $runDirectory + ' ' + (Get-Content -LiteralPath $stderr -Raw -Encoding UTF8))
    }
    if ((Get-Sha256 $InputDocx) -ne $sourceHash -or
        (Get-Sha256 $snapshot) -ne $sourceHash) {
        throw 'SOURCE_CHANGED_DURING_EXPORT'
    }
    $report = Get-Content -LiteralPath (Join-Path $runDirectory 'worker-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    [IO.File]::Move($privatePdf, $OutputPdf)
    $report | Add-Member -NotePropertyName source_docx -NotePropertyValue $InputDocx
    $report.output_pdf = $OutputPdf
    $report | Add-Member -NotePropertyName evidence_directory -NotePropertyValue $runDirectory
    Write-NewJson (Join-Path $runDirectory 'result.json') $report
    $report | ConvertTo-Json -Depth 8
} catch {
    [Console]::Error.WriteLine('WORD_RENDER_REJECTED: ' + $_.Exception.Message)
    exit 1
}










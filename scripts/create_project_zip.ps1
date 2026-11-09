[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$repositoryRoot = $repositoryRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $OutputPath = Join-Path $repositoryRoot "dist\Vanguard_AI_project_$timestamp.zip"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repositoryRoot $OutputPath
}

$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ([System.IO.Path]::GetExtension($OutputPath) -ine '.zip') {
    throw 'OutputPath must have a .zip extension.'
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite an existing archive: $OutputPath"
}

$outputDirectory = [System.IO.Path]::GetDirectoryName($OutputPath)
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$forbiddenDirectoryNames = @(
    '.git', '.venv', 'venv', 'node_modules', '.pytest_cache', '__pycache__',
    'logs', 'log', 'dist', 'build'
)
$modelArtifactExtensions = @(
    '.pt', '.pth', '.onnx', '.safetensors', '.weights', '.ckpt', '.pb',
    '.tflite', '.engine', '.gguf', '.h5', '.hdf5', '.model', '.bin',
    '.pkl', '.pickle', '.joblib'
)
$modelOutputDirectories = @('runs', 'outputs', 'weights', 'checkpoints', 'wandb', 'mlruns')

function Get-RepositoryRelativePath {
    param([Parameter(Mandatory = $true)][string]$FullPath)

    $relative = $FullPath.Substring($repositoryRoot.Length).TrimStart('\', '/')
    return ($relative -replace '\\', '/')
}

function Test-ForbiddenArchivePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = ($RelativePath -replace '\\', '/').Trim('/')
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $false
    }

    $segments = @($normalized.Split('/'))
    foreach ($segment in $segments) {
        if ($forbiddenDirectoryNames -contains $segment) {
            return $true
        }
    }

    $leafName = $segments[$segments.Length - 1]
    if ($leafName -ieq '.env.example') {
        # The placeholder template is safe and must remain in the archive.
    }
    elseif ($leafName -match '(?i)^\.env(?:\..*)?$') {
        return $true
    }

    if ($leafName -match '(?i)\.log(?:\..*)?$') {
        return $true
    }

    if ($segments.Length -ge 2 -and $segments[0] -ieq 'datasets' -and $segments[1] -match '(?i)^raw(?:[-_].*)?$') {
        return $true
    }

    if ([System.IO.Path]::GetExtension($leafName) -in $modelArtifactExtensions) {
        return $true
    }

    if ($segments[0] -ieq 'ai_engine') {
        foreach ($segment in $segments) {
            if ($modelOutputDirectories -contains $segment) {
                return $true
            }
        }
    }

    if ([System.IO.Path]::GetExtension($leafName) -ieq '.zip') {
        return $true
    }

    return $false
}

function Get-IncludedProjectFiles {
    param([Parameter(Mandatory = $true)][string]$RootPath)

    $pendingDirectories = [System.Collections.Generic.Stack[string]]::new()
    $pendingDirectories.Push($RootPath)

    while ($pendingDirectories.Count -gt 0) {
        $currentDirectory = $pendingDirectories.Pop()
        foreach ($entry in Get-ChildItem -LiteralPath $currentDirectory -Force -ErrorAction Stop) {
            $relativePath = Get-RepositoryRelativePath -FullPath $entry.FullName

            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                continue
            }

            if ($entry.PSIsContainer) {
                if (-not (Test-ForbiddenArchivePath -RelativePath $relativePath)) {
                    $pendingDirectories.Push($entry.FullName)
                }
                continue
            }

            if (-not (Test-ForbiddenArchivePath -RelativePath $relativePath)) {
                $entry
            }
        }
    }
}

$requiredArchivePaths = @(
    'README.md',
    '.gitignore',
    'backend/requirements.txt',
    'backend/.env.example',
    'docs/ARCHITECTURE.md',
    'docs/DATABASE_DESIGN.md',
    'docs/DEVELOPER_GUIDELINES.md'
)

$archiveCreated = $false
$zipArchive = $null

try {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $projectFiles = @(Get-IncludedProjectFiles -RootPath $repositoryRoot)
    $includedRelativePaths = @(
        $projectFiles | ForEach-Object { Get-RepositoryRelativePath -FullPath $_.FullName }
    )

    $missingRequiredPaths = @(
        $requiredArchivePaths | Where-Object { $includedRelativePaths -notcontains $_ }
    )
    if ($missingRequiredPaths.Count -gt 0) {
        throw ('Required project files are missing or excluded: ' + ($missingRequiredPaths -join ', '))
    }

    $zipArchive = [System.IO.Compression.ZipFile]::Open(
        $OutputPath,
        [System.IO.Compression.ZipArchiveMode]::Create
    )
    $archiveCreated = $true

    foreach ($file in $projectFiles) {
        $relativePath = Get-RepositoryRelativePath -FullPath $file.FullName
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zipArchive,
            $file.FullName,
            $relativePath,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }

    $zipArchive.Dispose()
    $zipArchive = $null

    $readArchive = [System.IO.Compression.ZipFile]::OpenRead($OutputPath)
    try {
        $forbiddenPathsFound = @(
            foreach ($entry in $readArchive.Entries) {
                if (Test-ForbiddenArchivePath -RelativePath $entry.FullName) {
                    $entry.FullName
                }
            }
        )

        if ($forbiddenPathsFound.Count -gt 0) {
            $uniqueForbiddenPaths = @($forbiddenPathsFound | Sort-Object -Unique)
            throw ("Archive verification failed. Forbidden paths found:`n - " + ($uniqueForbiddenPaths -join "`n - "))
        }

        $archiveEntryNames = @($readArchive.Entries | ForEach-Object { $_.FullName })
        $missingAfterPackaging = @(
            $requiredArchivePaths | Where-Object { $archiveEntryNames -notcontains $_ }
        )
        if ($missingAfterPackaging.Count -gt 0) {
            throw ('Archive verification failed. Required paths missing: ' + ($missingAfterPackaging -join ', '))
        }

        Write-Output "Project ZIP created and verified: $OutputPath"
        Write-Output "Included files: $($readArchive.Entries.Count)"
        Write-Output 'Forbidden paths found: 0'
    }
    finally {
        $readArchive.Dispose()
    }
}
catch {
    if ($null -ne $zipArchive) {
        $zipArchive.Dispose()
    }
    if ($archiveCreated -and (Test-Path -LiteralPath $OutputPath)) {
        Remove-Item -LiteralPath $OutputPath -Force -ErrorAction SilentlyContinue
    }
    throw
}

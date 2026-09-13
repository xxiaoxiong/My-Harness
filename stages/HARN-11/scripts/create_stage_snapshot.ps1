[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^HARN-\d{2}$')]
    [string]$Stage
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stagesRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot 'stages'))
$target = [System.IO.Path]::GetFullPath((Join-Path $stagesRoot $Stage))
$archive = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("agent-harness-$($Stage.ToLowerInvariant())-$([guid]::NewGuid().ToString('N')).zip")

if (-not $target.StartsWith(
    $stagesRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Snapshot target escapes the stages directory: $target"
}

if (Test-Path -LiteralPath $target) {
    throw "Snapshot already exists and will not be overwritten: $target"
}

Push-Location $repositoryRoot
try {
    $gitRoot = [System.IO.Path]::GetFullPath(
        (git rev-parse --show-toplevel).Trim()
    )
    if (
        $LASTEXITCODE -ne 0 -or
        -not $gitRoot.Equals($repositoryRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Run this script from the My-Harness repository."
    }

    $changes = @(git status --porcelain)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the Git working tree."
    }
    if ($changes.Count -gt 0) {
        throw "Commit or discard working-tree changes before creating a snapshot."
    }

    New-Item -ItemType Directory -Path $stagesRoot -Force | Out-Null
    git archive --format=zip --output=$archive HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "git archive failed."
    }

    Expand-Archive -LiteralPath $archive -DestinationPath $target
    Write-Output "Created $Stage from commit $(git rev-parse --short HEAD): $target"
}
catch {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    throw
}
finally {
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    Pop-Location
}

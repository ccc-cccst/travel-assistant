param(
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$RepositoryName = 'short-trip-assistant',
    [ValidateSet('private', 'public')][string]$Visibility = 'private',
    [string]$ExpectedOwner = 'ccc-cccst'
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw 'Install GitHub CLI from https://cli.github.com/ first.'
    }
    $travelTop = (& git rev-parse --show-toplevel).Trim()
    if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($travelTop) -ne [IO.Path]::GetFullPath($PSScriptRoot)) {
        throw 'Run this script from the prepared team repository.'
    }
    $travelStatus = & git status --porcelain
    if ($travelStatus) { throw 'Uncommitted changes found. Review and commit them before publishing.' }
    $travelBranch = (& git branch --show-current).Trim()
    if ($travelBranch -ne 'main') { throw 'The initial upload expects the main branch.' }
    & python (Join-Path $PSScriptRoot 'scripts\check_publish.py')
    if ($LASTEXITCODE -ne 0) { throw 'Pre-upload check failed.' }

    $travelLogin = & gh api user --jq .login 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Sign in first: gh auth login --hostname github.com --git-protocol https --web'
    }
    $travelLogin = $travelLogin.Trim()
    if ($travelLogin -ne $ExpectedOwner) {
        throw "Signed in as $travelLogin, but the expected account is $ExpectedOwner. Check your account before publishing."
    }
    $travelFullName = "$travelLogin/$RepositoryName"
    $travelExpectedUrl = "https://github.com/$travelFullName.git"
    $travelRemotes = @(& git remote)
    if ($travelRemotes -contains 'origin') {
        $travelUrl = (& git remote get-url origin).Trim()
        if ($travelUrl -ne $travelExpectedUrl -and $travelUrl -ne "https://github.com/$travelFullName" -and $travelUrl -ne "git@github.com:$travelFullName.git") {
            throw 'The existing origin points to a different repository. It was not changed.'
        }
        $travelRemotePrivate = & gh repo view $travelFullName --json isPrivate --jq .isPrivate
        if ($LASTEXITCODE -ne 0) { throw 'Could not verify the existing remote repository.' }
        if ($Visibility -eq 'private' -and $travelRemotePrivate.Trim() -ne 'true') {
            throw 'The requested private upload points to a public repository. Check visibility first.'
        }
    }
    else {
        & gh repo create $travelFullName "--$Visibility" --source $PSScriptRoot --remote origin --description 'ShortTrip Assistant: travel planning, experience evaluation and local repair'
        if ($LASTEXITCODE -ne 0) { throw 'Repository creation failed. No upload was attempted.' }
    }
    & gh auth setup-git --hostname github.com
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure GitHub authentication for Git.' }
    & git push --set-upstream origin main
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Keep this window output and retry after fixing the reported problem.' }
    Write-Host "Uploaded: https://github.com/$travelFullName"
    Write-Host 'Invite your friends from repository Settings > Collaborators.'
}
finally { Pop-Location }

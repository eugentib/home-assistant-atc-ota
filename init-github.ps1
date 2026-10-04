param(
    [string]$Repo = "home-assistant-atc-ota",
    [string]$Owner = "eugentib"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is not installed or is not in PATH."
}

if (-not (Test-Path ".\repository.yaml")) {
    throw "Run this script from the repository root (the folder containing repository.yaml)."
}

if (-not (Test-Path ".git")) {
    git init
}

git add .

$hasHead = $true
try { git rev-parse --verify HEAD *> $null } catch { $hasHead = $false }
if (-not $hasHead) {
    git commit -m "Initial ATC OTA Home Assistant app"
} else {
    $changes = git status --porcelain
    if ($changes) {
        git commit -m "Update ATC OTA Home Assistant app"
    }
}

git branch -M main

if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status *> $null
    if ($LASTEXITCODE -ne 0) {
        gh auth login
    }

    $remote = git remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $remote) {
        gh repo create "$Owner/$Repo" --public --source=. --remote=origin --push
    } else {
        git push -u origin main
    }

    Write-Host "Repository: https://github.com/$Owner/$Repo"
} else {
    Write-Host "GitHub CLI (gh) was not found."
    Write-Host "Create https://github.com/$Owner/$Repo as an empty public repository, then run:"
    Write-Host "  git remote add origin https://github.com/$Owner/$Repo.git"
    Write-Host "  git push -u origin main"
}

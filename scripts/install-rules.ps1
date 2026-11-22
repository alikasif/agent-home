# Copies rules/*.md into ~/.claude/rules (global). Re-run after editing rules/.
$dest = Join-Path $HOME '.claude\rules'
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item (Join-Path $PSScriptRoot '..\rules\*.md') $dest -Force
Write-Host "Installed rules to $dest"

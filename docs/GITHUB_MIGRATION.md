# GitHub Migration Note

**Date:** 2026-09-09
**Repo:** RIP-PROTOTYPE
**GitHub target:** https://github.com/sultan-rrcat/rip.git
**Gitea workspace:** http://10.10.30.65:3000/trainee-ai-ml/rip-prototype-frontend.git

## Initial state
- `.git` = empty init, branch `master`, no commits
- `.git_gitea` = full Gitea history, 46 commits, branch `main`, tag `v0.1`
- Working tree matches `.git_gitea` HEAD

## Commands executed

### 1. Duplicate Gitea history into working `.git` for push
```powershell
Get-ChildItem -Force .git_gitea | Copy-Item -Destination .git -Recurse -Force
```
Verify:
```powershell
git log --oneline -5
```

### 2. Add GitHub remote
```powershell
git remote add github https://github.com/sultan-rrcat/rip.git
git remote -v
```

### 3. Push history to GitHub
```powershell
git push -u github main
git push github --tags
```
Output:
- `* [new branch] main -> main`
- `* [new tag] v0.1 -> v0.1`

### 4. Restore workspace layout
```powershell
Move-Item -LiteralPath .git -Destination .git_github
Move-Item -LiteralPath .git_gitea -Destination .git
```

Final layout:
- `.git` → Gitea workspace, default for daily work
- `.git_github` → GitHub mirror clone

## Future commits guide

### Working with Gitea only
```powershell
git status
git add .
git commit -m "message"
git push origin main
```

### Syncing Gitea → GitHub

How to push the same commit to GitHub
Option A – add GitHub as a second remote to your workspace .git once and push both:
git remote add github https://github.com/sultan-rrcat/rip.git

# Gitea
git push origin main

# GitHub
git push github main
git push github --tags
After this you can keep the remote permanently and push to both with two commands.


Option A does not require you to keep the .git_github mirror at all.
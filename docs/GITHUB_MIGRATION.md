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
Option A – use the mirror:
```powershell
git --git-dir=.git_github --work-tree=. pull origin main
git --git-dir=.git_github --work-tree=. push github main
git --git-dir=.git_github --work-tree=. push github --tags
```

Option B – temporarily add GitHub remote to workspace:
```powershell
git remote add github https://github.com/sultan-rrcat/rip.git
git push github main
git push github --tags
git remote remove github
```

Option C – keep both remotes:
```powershell
git remote add github https://github.com/sultan-rrcat/rip.git
git push origin main   # Gitea
git push github main   # GitHub
```

### Working with GitHub only
```powershell
git --git-dir=.git_github --work-tree=. status
git --git-dir=.git_github --work-tree=. add .
git --git-dir=.git_github --work-tree=. commit -m "message"
git --git-dir=.git_github --work-tree=. push github main
```

**Notes**
- `.gitignore` now locally ignores `.git_gitea` and `.git_github` to avoid tracking the repo dirs.
- No branch renaming was performed; `main` is used for both remotes.
- Auth for GitHub HTTPS is already configured.

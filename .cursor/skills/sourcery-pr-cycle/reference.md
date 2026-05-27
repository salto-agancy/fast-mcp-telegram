# Sourcery PR Cycle — gh command reference

Replace `{owner}`, `{repo}`, and `<N>` with actual values. Resolve owner/repo once:

```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```

## Trigger review

```bash
gh pr comment <N> --body "@sourcery-ai review"
```

Wait several minutes before polling.

## Poll checks

```bash
gh pr checks <N>
gh pr checks <N> --watch   # block until complete
```

## Fetch Sourcery comments only

```bash
gh api "repos/{owner}/{repo}/pulls/<N>/comments" \
  --jq '.[] | select(.user.login=="sourcery-ai[bot]") | {created_at, path, line, body}'
```

## Fetch Sourcery review bodies

```bash
gh api "repos/{owner}/{repo}/pulls/<N>/reviews" \
  --jq '.[] | select(.user.login=="sourcery-ai[bot]") | {submitted_at, state, body}'
```

## List review headings (numbered items)

```bash
gh api "repos/{owner}/{repo}/pulls/<N>/reviews" --jq '.[].body' | python3 -c "
import sys, re
data = sys.stdin.read()
for m in sorted(set(re.findall(r'(### \d\. [^\n]+)', data))):
    print(m)
"
```

## List comment issue/suggestion lines

```bash
gh api "repos/{owner}/{repo}/pulls/<N>/comments" --jq '.[].body' | python3 -c "
import sys
for line in sys.stdin.read().split('\n'):
    line = line.strip()
    if line.startswith('**') and ('issue' in line or 'suggestion' in line):
        print(line[:120])
"
```

## PR metadata

```bash
gh pr view <N> --json url,title,state,mergeable,statusCheckRollup
gh pr diff <N>
```

## Push branch (first time)

```bash
git push -u origin HEAD
```

## Create PR with HEREDOC body

```bash
gh pr create --title "title" --body "$(cat <<'EOF'
## Summary
- ...

## Test plan
- [ ] ...

EOF
)"
```

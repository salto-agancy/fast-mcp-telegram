---
name: Release Notes Generation
description: Generate release notes for fast-mcp-telegram GitHub releases
disable-model-invocation: true
---

# Release Notes Generation

## Process for Creating Release Notes

When creating release notes, follow this systematic approach:

### 1. Collect Context from Memory Bank
- **Read memory bank files** to identify completed work since last release:
  ```bash
  cat .cursor/memory-bank/progress.md
  cat .cursor/memory-bank/activeContext.md
  ```
- **Focus on entries with dates** - these represent completed features, fixes, and improvements
- **Cross-reference with git** - verify memory bank entries match actual code changes
- Memory bank typically contains structured summaries of user-facing changes organized by date

### 2. Identify Changes
- Find the latest major version tag (`X.Y.0` format):
  ```bash
  git tag --sort=-version:refname | grep -E '^[0-9]+\.[0-9]+\.0$' | head -1
  ```
- Check for commits since the last tag:
  ```bash
  git log --oneline <last-major-tag>..HEAD
  ```
- **Check branch context**: Verify which branch commits are on using:
  ```bash
  git log --oneline --all --graph --decorate | head -40
  ```
- **CRITICAL - Verify on master**: Memory bank may contain entries from other branches. Only include changes that are actually merged to `master`. Use the git graph to confirm.
- **Include all changes**: Analyze both local commits and any remote changes since the last major version
- **Note**: Typically you'll be preparing release notes before creating the new tag
- **Merge sources**: Combine memory bank insights with git analysis for complete picture

### 3. Analyze Code Changes
- **Primary focus** - examine user-facing code changes only:
  ```bash
  git diff <previous-major-tag>..HEAD
  ```
- **User-facing filter**: Look for new features, bug fixes, and behavior changes that affect end users
- **Skip internal changes**: Ignore refactoring, code cleanup, documentation, and architecture changes unless they're the only changes
- **Final state** - shows final user-visible functionality:
  ```bash
  git show HEAD:<file>
  ```
- **Summaries** - identify user-facing commits:
  ```bash
  git log --oneline <previous-major-tag>..HEAD
  ```
- **Cross-validate**: Treat commit messages as hints, but validate against actual user-visible functionality
- **Describe user value**: Focus on what users can now do, not implementation details

### 4. Categorize Changes
- **AI-driven categorization**: Let the AI analyze code changes and determine appropriate categories
- **Focus on impact**: Group changes by their functional impact

#### Prioritization
- **PRIMARY FOCUS**: Added and fixed user-facing features only (new functionality, bug fixes, behavior changes)
- **SECONDARY**: Internal improvements only if there are no user-facing features to highlight
- **OMIT**: Refactoring, code quality improvements, documentation changes, and meta changes unless they're the only changes
- **CONCISE**: If there are user-facing features, omit all internal improvements to keep notes focused on value
- **Fixes vs new work**: Do not list under **Fixes** items that only polish or correct behavior of features introduced in the same release; describe those features once under **New Features**. Reserve **Fixes** for regressions or bugs unrelated to that release's new capabilities.
- **Major dependency milestones**: When the stack changes in a user-visible way (e.g. FastMCP 3.x), call it out under **New Features**, not only in prose.

### 5. Format Release Notes

Use this markdown template (paste as a markdown code block in chat):

```markdown
<One short sentence: main value — NO VERSION NUMBERS in this line>

## New Features
- **`tool_name` / feature** - What users can now do (use backticks for tools, params like `chat_id`)
- **Another feature** - User-visible capability or improvement

## Fixes
- **Issue resolved** - What was broken and how it's now fixed
- **Another fix** - User-visible problem that was solved

This release <briefly describe the primary user-facing value proposition>.

**Full Changelog**: https://github.com/leshchenko1979/fast-mcp-telegram/compare/<previous-major-tag>...<current-tag>
```
**Note**:
- Replace `<current-tag>` and `<previous-major-tag>` with actual tags (e.g. `0.12.0`, `0.11.0`)
- DO NOT repeat the title as the first line of the body — the title is already displayed separately by GitHub
- Only include internal improvements if there are no user-facing features to highlight
- Omit the **Fixes** section entirely if everything user-facing is already covered under **New Features** (see prioritization above)

### 6. Quality Checks
- Verify all significant changes are mentioned
- Ensure categories accurately reflect the nature of changes made
- **Validate against code**: Ensure release note descriptions accurately reflect the actual code changes
- **Final state verification**: Confirm descriptions match the final state, not intermediate commits

### 7. Best Practices
- **No emojis** in release notes
- **User-facing focus**: Prioritize what users can now do or what problems are now solved
- **Omit internal changes**: Don't mention refactoring, code cleanup, or internal improvements unless they're the only changes
- **Impact over implementation**: Describe user-visible benefits, not how the code was changed
- **Backticks in descriptions**: Wrap tool names (e.g. `get_messages`), parameter names (e.g. `reply_to`), field names (e.g. `reply_markup`), and file paths in backticks when referenced in release notes
- **Code-first analysis**: Base descriptions on actual user-facing code changes, not commit messages
- **Final state focus**: Describe what users can now accomplish, not the development journey
- **Provide as markdown block**: Output release notes as a markdown code block in chat for easy copy/paste, do NOT create files
- **Aggregate major releases**: Include all changes since the last major version (`X.Y.0`)
- **Include recent commits**: Always analyze commits since the last tag, even if not yet tagged
- **Omit file stats**: Never include "Files Changed" or technical implementation details
- **Plain changelog URL**: Use a bare markdown link (no extra wrapping). Example:
  ```
  **Full Changelog**: https://github.com/leshchenko1979/fast-mcp-telegram/compare/<previous-major-tag>...<current-tag>
  ```

### 8. Version Bump and GitHub Release
- **Never** edit `[project].version` in `pyproject.toml` by hand — use **`uv version`** only.
- **First**: Bump version (writes `pyproject.toml`; may update `uv.lock`):
  ```bash
  uv version <version>
  # or: uv version --bump patch | minor | major
  ```
- **Then**: If `uv.lock` changed, include it; otherwise `uv lock` only when dependencies changed.
- **`src/_version.py`**: Do not edit for releases — it reads version from package metadata or `pyproject.toml`.
- **Commit, tag, push** (no `v` prefix on tags):
  ```bash
  git add pyproject.toml uv.lock
  git commit -m "chore: bump version to <version>"
  git tag <version>
  git push origin <branch> && git push origin <version>
  ```
- **Create GitHub release with `gh`** (after tag is pushed). Title: short user-facing headline **without** the version number. Body: notes from section 5 (HEREDOC avoids shell escaping issues):
  ```bash
  gh release create <version> \
    --title "<short headline without version>" \
    --notes-file - <<'EOF'
  <paste release notes body here — no duplicate title line>
  EOF
  ```
  Or write body to a temp file and use `--notes-file /path/to/notes.md` (do not commit that file).
- **Verify**: `gh release view <version>` — confirm title, body, and compare URL.
- **Wait for confirmation**: Do not send Telegram announcements until the user confirms the GitHub release is published.
- **Finally**: Send community announcement to Telegram (section 9).

**Important**:
- Release notes are for GitHub releases only - do NOT commit release notes files to git repository
- Version is **`[project].version` in `pyproject.toml`**, maintained with **`uv version`** (not manual edits to `src/_version.py`)
- There are no release files in this repository. Do not add any `RELEASE_NOTES*` or similar files to git
- Tags are plain semantic versions without a leading `v` (example: `0.3.0`)

**Typical workflow**: Identify last tag → Analyze user-facing changes → Prepare release notes (section 5) → `uv version` → commit → tag → push → `gh release create` → **Wait for user confirmation** → Send community message

### 9. Community Announcement Process
- **Prerequisite**: Only proceed after user confirms GitHub release is published
- **Targets**: English group [invite link](https://t.me/+U_3CpNWhXa9jZDcy); Russian `@mcp_telegram` ([t.me/mcp_telegram](https://t.me/mcp_telegram))
- **Language**: English for the English group; Russian for the Russian group
- **Content**: Include version number, short header line, and key features aligned with the GitHub release
- **Telegram formatting (required for `send_message`)**: Use **HTML**, not Markdown. Telegram Markdown modes often mangle mixed punctuation and code; HTML is reliable. Call `send_message` with `parse_mode="html"`. Allowed tags include `<b>`, `<i>`, `<code>`, `<a href="https://...">text</a>`. Escape `&`, `<`, `>` in literal text if needed (`&amp;`, `&lt;`, `&gt;`).
- **Presentation**: Emojis and checkmark lines are fine; use `<b>` for the version line and emphasis, `<code>` for parameter and field names
- **Structure**:
  - Version header with date (e.g. `🚀 <b>fast-mcp-telegram 0.x.y</b> · YYYY-MM-DD`)
  - Feature highlights (e.g. lines starting with ✅)
  - GitHub release link via `<a href="https://github.com/leshchenko1979/fast-mcp-telegram/releases/tag/0.x.y">…</a>`
- **Timing**: Send only after user confirms GitHub release is published
- **Using MCP for sending**:
  - Try `find_chats` to discover chat IDs first, then `send_message` with `parse_mode="html"`
  - **Private English group**: If not discoverable via `find_chats`, give the user the HTML message to paste manually
  - **Group posting**: Bot must be an admin/member of the group to post; if `ChatWriteForbiddenError`, give user message to paste manually

# FinanceTracker — Setup

A complete local-first scaffold. `.claude/` and `CLAUDE.md` are gitignored on purpose (your config, not tracked). The `.gitignore`, hooks, and `SETUP.md` are the only things meant to be committed at this stage.

> **Note on CLAUDE.md location:** it sits at the repo root (not inside `.claude/`) because that is the location Claude Code reliably auto-loads as project memory. It is gitignored, so it is still not tracked.

## 1. Put the files in place
Copy this whole `FinanceTracker/` folder to where you want the project. It already contains:
```
FinanceTracker/
├── CLAUDE.md                       (root, gitignored, auto-loaded)
├── .gitignore                      (COMMIT THIS — it protects everything)
├── .env.example                    (copy to .env)
├── SETUP.md
├── .githooks/
│   ├── pre-commit                  (blocks committing sensitive files)
│   └── commit-msg                  (blocks Claude/Anthropic in messages)
└── .claude/                        (gitignored)
    ├── settings.json               (includeCoAuthoredBy: false)
    ├── prd/financetracker-prd.md
    ├── rules/{security,coding-practices,pre-deployment,mcp-rules}.md
    ├── agents/{planner,coder,tester,reviewer}.md
    └── commands/ship.md
```

## 2. Init git locally (do this BEFORE adding anything)
```bash
cd FinanceTracker
git init
git config core.hooksPath .githooks        # activate the safety hooks
git branch -m main                          # start on main
```

## 3. Verify nothing sensitive is tracked, then commit the scaffold
```bash
git add .gitignore .githooks SETUP.md
git status                                   # confirm: NO .claude/, NO CLAUDE.md, NO data/secrets
git commit -m "chore: project scaffold and safety hooks"
```
(`.claude/` and `CLAUDE.md` won't appear because they're gitignored — that's intended.)

## 4. Secrets and data folders
```bash
cp .env.example .env                         # fill in OPENROUTER_API_KEY etc. (.env is gitignored)
mkdir -p data/inbox output logs              # all gitignored
```
Drop your CommBank + Westpac CSVs into `data/inbox/` when you have them. They will never be committed.

## 5. Create the v1 branch and build, section by section
```bash
git checkout -b v1
```
Open Claude Code in this folder (you can run with permissions bypassed). Then build one PRD section at a time, e.g.:
```
/ship 7.3 per-bank CSV parsers (CommBank + Westpac profiles)
/ship 7.5 sanitiser (mandatory pre-LLM, fail closed)
/ship 7.4 three-layer idempotency
... and so on through PRD §7
```
Each `/ship` runs planner→coder→tester→reviewer, writes handoffs to `.pipeline/`, stops on open questions or failing tests, and ends with a SHIP/NEEDS WORK/BLOCK verdict. It does not merge or auto-commit — you review, then commit (clean messages enforced by the hook) and merge `v1` → `main` when the whole of v1 is confirmed.

## 6. Test the guards once (recommended)
```bash
echo "test" > data/inbox/probe.csv
git add -f data/inbox/probe.csv && git commit -m "test"   # should be BLOCKED by pre-commit
git restore --staged data/inbox/probe.csv && rm data/inbox/probe.csv
git commit --allow-empty -m "made with claude"            # should be BLOCKED by commit-msg
```

# docs/proposed-workflows

The agent's GitHub token has no Workflows permission, so it can't push to `.github/workflows/`
(`AGENTS.md` → Hard rules, rule 4). Proposed workflow files land here instead, and the PR that
adds one says so. A human reviews the file, moves it to `.github/workflows/<name>.yaml` in their
own commit, and deletes it from here.

## Proposed now

- `pages.yaml`: publishes the recorded demo site to GitHub Pages (#89). Two actions still need a full SHA
  (`# TODO pin`): `actions/upload-pages-artifact` and `actions/deploy-pages`.
